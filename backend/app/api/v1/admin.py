"""Admin endpoints: operational metrics, audit logs, and the company's users.

Admin grants access to OPERATIONAL data for the admin's own tenant. It does not
grant cross-tenant data access - every query here is still tenant-filtered.
See .claude/rules/tenant-isolation.md section 2.

User management additionally requires `TenantAdminUser` rather than `AdminUser`:
a platform operator onboards a company and then stays out of its user list,
which is reached through the audited platform routes instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from sqlalchemy import Integer, cast, func, select

from app.api.deps import AdminUser, DbSession, TenantAdminUser
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import (
    AuditEvent,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    RequestUsage,
    User,
    UserRole,
)
from app.schemas import (
    AcknowledgedResponse,
    AdminMetricsResponse,
    AuditEventOut,
    InviteResponse,
    TenantOut,
    UsageSummary,
    UserInviteRequest,
    UserListResponse,
    UserOut,
    UserUpdateRequest,
)
from app.services import vector
from app.services.identity import get_identity_provider
from app.services.onboarding import (
    assert_not_last_admin,
    assert_not_self,
    assert_role_assignable,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/metrics", response_model=AdminMetricsResponse)
async def metrics(ctx: AdminUser, session: DbSession) -> AdminMetricsResponse:
    usage_row = (
        await session.execute(
            select(
                func.count(RequestUsage.id),
                func.coalesce(func.sum(RequestUsage.input_tokens), 0),
                func.coalesce(func.sum(RequestUsage.output_tokens), 0),
                func.coalesce(func.sum(RequestUsage.estimated_cost), 0.0),
                func.coalesce(func.avg(RequestUsage.latency_ms), 0.0),
                func.coalesce(
                    # cast(), not func.cast(): func.INTEGER() builds a SQL
                    # function call whose .type is NullType, which fails to
                    # compile. The cache hit rate is AVG over booleans as 0/1.
                    func.avg(cast(RequestUsage.cache_hit, Integer)),
                    0.0,
                ),
            ).where(RequestUsage.tenant_id == ctx.tenant_id)
        )
    ).one()

    documents = (
        await session.execute(
            select(func.count(Document.id)).where(
                Document.tenant_id == ctx.tenant_id,
                Document.status != DocumentStatus.DELETED,
            )
        )
    ).scalar_one()

    failures = (
        await session.execute(
            select(func.count(IngestionJob.id)).where(
                IngestionJob.tenant_id == ctx.tenant_id,
                IngestionJob.status == JobStatus.FAILED,
            )
        )
    ).scalar_one()

    security_events = (
        await session.execute(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.tenant_id == ctx.tenant_id,
                AuditEvent.severity.in_(["error", "critical"]),
            )
        )
    ).scalar_one()

    chunks = await vector.count_chunks(ctx)

    return AdminMetricsResponse(
        tenant_id=ctx.tenant_id,
        usage=UsageSummary(
            total_requests=int(usage_row[0]),
            total_input_tokens=int(usage_row[1]),
            total_output_tokens=int(usage_row[2]),
            total_estimated_cost=round(float(usage_row[3]), 6),
            avg_latency_ms=round(float(usage_row[4]), 1),
            cache_hit_rate=round(float(usage_row[5]), 3),
        ),
        documents=int(documents),
        chunks=chunks,
        ingestion_failures=int(failures),
        security_events=int(security_events),
    )


@router.get("/audit", response_model=list[AuditEventOut])
async def audit_log(
    ctx: AdminUser,
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
    severity: str | None = Query(default=None),
) -> list[AuditEventOut]:
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant_id)
    if severity:
        stmt = stmt.where(AuditEvent.severity == severity)
    stmt = stmt.order_by(AuditEvent.created_at.desc()).limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    return [AuditEventOut.model_validate(r) for r in rows]


@router.get("/security", response_model=list[AuditEventOut])
async def security_events(
    ctx: AdminUser, session: DbSession, limit: int = Query(default=100, ge=1, le=500)
) -> list[AuditEventOut]:
    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == ctx.tenant_id,
            AuditEvent.severity.in_(["warning", "error", "critical"]),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [AuditEventOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# the admin's own company
# ---------------------------------------------------------------------------
@router.get("/tenant", response_model=TenantOut)
async def own_tenant(ctx: AdminUser, session: DbSession) -> TenantOut:
    """The caller's own company. There is no path parameter to change it."""
    tenant = await repo.get_own_tenant(session, ctx)
    out = TenantOut.model_validate(tenant)
    seats = (
        await session.execute(
            select(
                func.count(User.id),
                func.coalesce(func.sum(cast(User.is_active, Integer)), 0),
            ).where(User.tenant_id == ctx.tenant_id)
        )
    ).one()
    out.user_count = int(seats[0])
    out.active_user_count = int(seats[1])
    out.admin_count = await repo.count_active_admins(session, ctx.tenant_id)
    return out


# ---------------------------------------------------------------------------
# users of the admin's own company
# ---------------------------------------------------------------------------
@router.get("/users", response_model=UserListResponse)
async def list_users(
    ctx: TenantAdminUser,
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> UserListResponse:
    rows, total = await repo.list_tenant_users(session, ctx, limit=limit, offset=offset)
    return UserListResponse(items=[UserOut.model_validate(r) for r in rows], total=total)


@router.post("/users", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def invite_user(
    payload: UserInviteRequest, ctx: TenantAdminUser, session: DbSession
) -> InviteResponse:
    """Invite a colleague into the caller's own company.

    The tenant is taken from the caller's token, so there is no request in which
    a user can be planted in somebody else's company. The role passes through
    `assert_role_assignable`, which refuses the platform role outright - the
    schema already makes it unrequestable, and this is the second gate.

    No password is chosen, returned, or logged: Cognito emails a one-time
    password and the invitee replaces it on first sign-in.
    """
    role = assert_role_assignable(ctx, payload.role)

    if await repo.find_tenant_user_by_email(session, ctx, payload.email):
        raise ValidationError("That person already has an account in this company.")

    user = await repo.create_tenant_user(
        session,
        ctx,
        email=payload.email,
        role=UserRole(role),
        department=payload.department,
        display_name=payload.display_name,
    )

    provider = get_identity_provider(session)
    identity = await provider.invite(
        email=payload.email,
        tenant_id=ctx.tenant_id,
        role=role,
        display_name=payload.display_name,
    )
    user.cognito_sub = identity.subject
    await session.flush()

    await repo.record_audit(
        session,
        event_type="user.created",
        ctx=ctx,
        severity="warning" if role == "admin" else "info",
        resource_type="user",
        resource_id=user.id,
        reason="tenant_admin_invited_user",
        invited_email=payload.email,
        granted_role=role,
        department=payload.department.value if payload.department else None,
    )

    message = (
        "Invitation email sent. They set their own password on first sign-in."
        if identity.invitation_sent
        else "Account created. Local development sends no email - sign in with the dev password."
    )
    return InviteResponse(
        user=UserOut.model_validate(user),
        invitation_sent=identity.invitation_sent,
        message=message,
    )


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    ctx: TenantAdminUser,
    session: DbSession,
) -> UserOut:
    """Change a colleague's role, department, or active status.

    Two lockouts are refused rather than warned about: an admin changing its own
    role or status, and any change that would leave the company with no active
    administrator. Both would otherwise end with a company unable to administer
    itself until the service provider intervened.
    """
    user = await repo.get_tenant_user(session, ctx, user_id)
    changes: dict[str, object] = {}

    if payload.role is not None and payload.role != user.role.value:
        role = assert_role_assignable(ctx, payload.role)
        assert_not_self(ctx, user_id, action="change the role of")
        if user.role == UserRole.ADMIN:
            assert_not_last_admin(
                target_is_admin=True,
                remaining_active_admins=await repo.count_active_admins(
                    session, ctx.tenant_id, excluding_user_id=user_id
                ),
                action="demote",
            )
        user.role = UserRole(role)
        changes["role"] = role

    if payload.department is not None and payload.department != user.department:
        user.department = payload.department
        changes["department"] = payload.department.value

    if payload.is_active is not None and payload.is_active != user.is_active:
        assert_not_self(ctx, user_id, action="deactivate")
        if not payload.is_active and user.role == UserRole.ADMIN:
            assert_not_last_admin(
                target_is_admin=True,
                remaining_active_admins=await repo.count_active_admins(
                    session, ctx.tenant_id, excluding_user_id=user_id
                ),
                action="deactivate",
            )
        user.is_active = payload.is_active
        changes["is_active"] = payload.is_active

    if not changes:
        return UserOut.model_validate(user)

    await session.flush()

    # Mirror the change into the directory. Authorization data lives in the
    # token, so a role left stale in Cognito would keep being honoured.
    if user.cognito_sub:
        provider = get_identity_provider(session)
        if "role" in changes:
            await provider.set_role(user.cognito_sub, user.role.value)
        if "is_active" in changes:
            await provider.set_enabled(user.cognito_sub, user.is_active)

    if "role" in changes:
        await repo.record_audit(
            session,
            event_type="user.role_changed",
            ctx=ctx,
            severity="warning",
            resource_type="user",
            resource_id=user.id,
            reason="tenant_admin_changed_role",
            new_role=changes["role"],
        )
    if "is_active" in changes:
        await repo.record_audit(
            session,
            event_type="user.deactivated" if not user.is_active else "user.reactivated",
            ctx=ctx,
            severity="warning",
            resource_type="user",
            resource_id=user.id,
            reason="tenant_admin_changed_status",
        )
    if "department" in changes:
        await repo.record_audit(
            session,
            event_type="user.updated",
            ctx=ctx,
            resource_type="user",
            resource_id=user.id,
            reason="tenant_admin_changed_department",
            new_department=changes["department"],
        )
    return UserOut.model_validate(user)


@router.post("/users/{user_id}/reset-password", response_model=AcknowledgedResponse)
async def reset_user_password(
    user_id: str, ctx: TenantAdminUser, session: DbSession
) -> AcknowledgedResponse:
    """Send a colleague a fresh one-time password.

    The admin never sees or chooses it. Cognito generates and delivers it, and
    the user replaces it on the next sign-in.
    """
    user = await repo.get_tenant_user(session, ctx, user_id)
    assert_not_self(ctx, user_id, action="reset the password of")

    if not user.cognito_sub:
        raise ValidationError("That account has no directory identity to reset.")

    provider = get_identity_provider(session)
    await provider.reset_to_temporary_password(user.cognito_sub)

    await repo.record_audit(
        session,
        event_type="user.password_reset_initiated",
        ctx=ctx,
        severity="warning",
        resource_type="user",
        resource_id=user.id,
        reason="tenant_admin_initiated_reset",
    )
    return AcknowledgedResponse(
        status="reset_sent", message="A one-time password is on its way to them."
    )
