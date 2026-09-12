"""The service provider's control plane: companies and their first admin.

Every route here requires `PlatformAdminUser`, which core/auth.py binds to the
reserved platform tenant - so a customer identity cannot reach any of it even
if it somehow carried the platform role.

What a platform operator can do:  create a company, invite that company's
administrator, suspend a company, read the onboarding trail.

What a platform operator explicitly cannot do:  read a company's documents,
conversations, messages or metrics. Onboarding a tenant is not a key to it.
See .claude/rules/tenant-isolation.md section 2.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, PlatformAdminUser
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.db import control_plane as cp
from app.db import repositories as repo
from app.db.models import UserRole
from app.schemas import (
    AuditEventOut,
    InviteResponse,
    TenantAdminInviteRequest,
    TenantCreateRequest,
    TenantListResponse,
    TenantOut,
    TenantUpdateRequest,
    UserOut,
)
from app.services.identity import get_identity_provider
from app.services.onboarding import slugify_tenant_id, validate_tenant_id

logger = get_logger(__name__)

router = APIRouter(prefix="/platform", tags=["platform"])


def _with_counts(tenant: object, counts: dict[str, dict[str, int]]) -> TenantOut:
    out = TenantOut.model_validate(tenant)
    seats = counts.get(out.id, {})
    out.user_count = seats.get("users", 0)
    out.active_user_count = seats.get("active_users", 0)
    out.admin_count = seats.get("admins", 0)
    return out


# ---------------------------------------------------------------------------
# companies
# ---------------------------------------------------------------------------
@router.post("/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreateRequest, ctx: PlatformAdminUser, session: DbSession
) -> TenantOut:
    """Onboard a company.

    The tenant id becomes an S3 prefix, a Qdrant filter value and a Redis key
    segment, so it is normalized and checked against the reserved list before
    anything is written.
    """
    tenant_id = validate_tenant_id(payload.tenant_id or slugify_tenant_id(payload.name))

    tenant = await cp.create_tenant(
        session,
        ctx,
        tenant_id=tenant_id,
        name=payload.name.strip(),
        contact_email=payload.contact_email,
    )

    await repo.record_audit(
        session,
        event_type="tenant.created",
        ctx=ctx,
        tenant_id=tenant.id,
        severity="info",
        resource_type="tenant",
        resource_id=tenant.id,
        reason="platform_admin_onboarded_company",
        tenant_name=tenant.name,
        actor_user_id=ctx.user_id,
        actor_tenant_id=ctx.tenant_id,
    )
    logger.info("tenant_created", extra={"extra": {"tenant_id": tenant.id}})
    return TenantOut.model_validate(tenant)


@router.get("/tenants", response_model=TenantListResponse)
async def list_tenants(ctx: PlatformAdminUser, session: DbSession) -> TenantListResponse:
    tenants = await cp.list_tenants(session)
    counts = await cp.tenant_user_counts(session)
    items = [_with_counts(t, counts) for t in tenants]
    return TenantListResponse(items=items, total=len(items))


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: str, ctx: PlatformAdminUser, session: DbSession) -> TenantOut:
    tenant = await cp.get_tenant(session, tenant_id)
    counts = await cp.tenant_user_counts(session)
    return _with_counts(tenant, counts)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: str,
    payload: TenantUpdateRequest,
    ctx: PlatformAdminUser,
    session: DbSession,
) -> TenantOut:
    """Rename or suspend a company.

    Suspension is reversible and destroys nothing: the company's documents,
    conversations and users stay exactly where they are.
    """
    tenant = await cp.get_tenant(session, tenant_id)

    changes: dict[str, object] = {}
    if payload.name is not None and payload.name.strip() != tenant.name:
        changes["name"] = payload.name.strip()
        tenant.name = payload.name.strip()
    if payload.is_active is not None and payload.is_active != tenant.is_active:
        changes["is_active"] = payload.is_active
        tenant.is_active = payload.is_active

    if not changes:
        counts = await cp.tenant_user_counts(session)
        return _with_counts(tenant, counts)

    await session.flush()
    await repo.record_audit(
        session,
        event_type="tenant.updated",
        ctx=ctx,
        tenant_id=tenant.id,
        severity="warning" if "is_active" in changes else "info",
        resource_type="tenant",
        resource_id=tenant.id,
        reason="platform_admin_updated_company",
        actor_user_id=ctx.user_id,
        **{f"new_{k}": v for k, v in changes.items()},
    )
    counts = await cp.tenant_user_counts(session)
    return _with_counts(tenant, counts)


# ---------------------------------------------------------------------------
# a company's administrator
# ---------------------------------------------------------------------------
@router.post(
    "/tenants/{tenant_id}/admins",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_tenant_admin(
    tenant_id: str,
    payload: TenantAdminInviteRequest,
    ctx: PlatformAdminUser,
    session: DbSession,
) -> InviteResponse:
    """Invite a company's administrator.

    The role is not a parameter. This endpoint issues exactly one kind of
    identity - a tenant admin inside the named company - so there is no request
    shape in which a platform operator could be minted by mistake.

    The database row is written first and the directory second, inside one
    transaction: if Cognito refuses, the row rolls back and no half-created
    account is left behind.
    """
    tenant = await cp.get_tenant(session, tenant_id)
    if not tenant.is_active:
        raise ValidationError("That company is suspended. Reactivate it first.")

    if await cp.find_user_in_tenant(session, tenant_id, payload.email):
        raise ValidationError("That person already has an account in this company.")

    existing_admins = await cp.count_tenant_admins(session, tenant_id)

    user = await repo.create_tenant_user(
        session,
        ctx,
        tenant_id=tenant_id,
        email=payload.email,
        role=UserRole.ADMIN,
        department=payload.department,
        display_name=payload.display_name,
    )

    provider = get_identity_provider(session)
    identity = await provider.invite(
        email=payload.email,
        tenant_id=tenant_id,
        role="admin",
        display_name=payload.display_name,
    )
    user.cognito_sub = identity.subject
    await session.flush()

    await repo.record_audit(
        session,
        event_type="user.tenant_admin_invited",
        ctx=ctx,
        tenant_id=tenant_id,
        severity="warning",
        resource_type="user",
        resource_id=user.id,
        reason="platform_admin_invited_tenant_admin",
        invited_email=payload.email,
        granted_role="admin",
        first_admin=existing_admins == 0,
        actor_user_id=ctx.user_id,
        actor_tenant_id=ctx.tenant_id,
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


# ---------------------------------------------------------------------------
# the onboarding trail
# ---------------------------------------------------------------------------
@router.get("/audit", response_model=list[AuditEventOut])
async def control_plane_audit(
    ctx: PlatformAdminUser,
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditEventOut]:
    """Tenant and user lifecycle events across every company.

    Restricted by event type to the control plane, so this can never widen into
    a view of another company's chat or document activity.
    """
    rows = await cp.list_control_plane_events(session, limit=limit)
    return [AuditEventOut.model_validate(r) for r in rows]
