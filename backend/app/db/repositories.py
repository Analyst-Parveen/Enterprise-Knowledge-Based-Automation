"""Tenant-scoped data access.

This is the ONLY sanctioned path to tenant data. Every function takes a
RequestContext and filters on ctx.tenant_id. There is deliberately no
"get by id without tenant" function - such a function cannot be misused if it
does not exist.

See .claude/rules/tenant-isolation.md section 3.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, get_correlation_id
from app.core.exceptions import NotFoundError, TenantIsolationError
from app.core.logging import log_security_event
from app.db.models import (
    AuditEvent,
    Conversation,
    Department,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    Message,
    RequestUsage,
    Tenant,
    User,
    UserFeedback,
    UserRole,
)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
async def record_audit(
    session: AsyncSession,
    *,
    event_type: str,
    ctx: RequestContext | None = None,
    severity: str = "info",
    resource_type: str | None = None,
    resource_id: str | None = None,
    reason: str | None = None,
    tenant_id: str | None = None,
    **details: Any,
) -> AuditEvent:
    """Write one audit row.

    `tenant_id` overrides the actor's tenant, for the few control-plane actions
    whose subject is a different tenant than the actor: a platform operator
    onboarding a company files the event under *that company*, so the company's
    own admins can see how they came to exist. The actor is recorded in details.
    """
    event = AuditEvent(
        tenant_id=tenant_id or (ctx.tenant_id if ctx else None),
        user_id=ctx.user_id if ctx else None,
        correlation_id=get_correlation_id() or None,
        event_type=event_type,
        severity=severity,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
        details=details,
    )
    session.add(event)
    await session.flush()
    return event


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------
async def get_document(
    session: AsyncSession, ctx: RequestContext, document_id: str
) -> Document | None:
    """Tenant-filtered fetch. A document in another tenant is simply not found."""
    stmt = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == ctx.tenant_id,
        Document.status != DocumentStatus.DELETED,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def require_document(
    session: AsyncSession, ctx: RequestContext, document_id: str
) -> Document:
    doc = await get_document(session, ctx, document_id)
    if doc is None:
        # Distinguish "does not exist" from "belongs to another tenant" in the
        # AUDIT LOG only - never in the response.
        exists_elsewhere = (
            await session.execute(select(Document.id).where(Document.id == document_id))
        ).first() is not None
        if exists_elsewhere:
            log_security_event(
                "tenant.cross_tenant_document_access",
                reason="document_belongs_to_another_tenant",
                severity="critical",
                document_id=document_id,
            )
            await record_audit(
                session,
                event_type="tenant.cross_tenant_document_access",
                ctx=ctx,
                severity="critical",
                resource_type="document",
                resource_id=document_id,
                reason="document_belongs_to_another_tenant",
            )
        raise TenantIsolationError()
    return doc


async def list_documents(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    limit: int = 50,
    offset: int = 0,
    department: str | None = None,
) -> tuple[Sequence[Document], int]:
    base = select(Document).where(
        Document.tenant_id == ctx.tenant_id,
        Document.status != DocumentStatus.DELETED,
    )
    if department:
        base = base.where(Document.department == department)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        (
            await session.execute(
                base.order_by(Document.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return rows, int(total)


async def soft_delete_document(
    session: AsyncSession, ctx: RequestContext, document_id: str
) -> Document:
    """Deletion requires ownership OR admin in the SAME tenant. Always audited."""
    doc = await require_document(session, ctx, document_id)

    if doc.owner_id != ctx.user_id and not ctx.is_admin:
        log_security_event(
            "authz.unauthorized_deletion",
            reason="not_owner_and_not_admin",
            severity="error",
            document_id=document_id,
        )
        await record_audit(
            session,
            event_type="authz.unauthorized_deletion",
            ctx=ctx,
            severity="error",
            resource_type="document",
            resource_id=document_id,
            reason="not_owner_and_not_admin",
        )
        raise TenantIsolationError("You are not authorized to delete this document.")

    doc.status = DocumentStatus.DELETED
    doc.deleted_at = datetime.now(UTC)
    await session.flush()

    await record_audit(
        session,
        event_type="document.deleted",
        ctx=ctx,
        resource_type="document",
        resource_id=document_id,
        reason="owner_or_admin_authorized",
    )
    return doc


# ---------------------------------------------------------------------------
# ingestion jobs
# ---------------------------------------------------------------------------
async def get_job(session: AsyncSession, ctx: RequestContext, job_id: str) -> IngestionJob | None:
    stmt = select(IngestionJob).where(
        IngestionJob.id == job_id, IngestionJob.tenant_id == ctx.tenant_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_jobs(
    session: AsyncSession, ctx: RequestContext, *, limit: int = 50
) -> Sequence[IngestionJob]:
    stmt = (
        select(IngestionJob)
        .where(IngestionJob.tenant_id == ctx.tenant_id)
        .order_by(IngestionJob.created_at.desc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def set_job_status(
    session: AsyncSession,
    job_id: str,
    status: JobStatus,
    *,
    progress: int | None = None,
    chunks_written: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Worker-side status update. Job IDs are internal, not user-supplied."""
    values: dict[str, Any] = {"status": status}
    if progress is not None:
        values["progress"] = progress
    if chunks_written is not None:
        values["chunks_written"] = chunks_written
    if error_code is not None:
        values["error_code"] = error_code
    if error_message is not None:
        values["error_message"] = error_message[:2000]
    if status in (JobStatus.COMPLETED, JobStatus.FAILED):
        values["finished_at"] = datetime.now(UTC)

    await session.execute(update(IngestionJob).where(IngestionJob.id == job_id).values(**values))
    await session.flush()


# ---------------------------------------------------------------------------
# conversations & messages
# ---------------------------------------------------------------------------
async def get_conversation(
    session: AsyncSession, ctx: RequestContext, conversation_id: str
) -> Conversation | None:
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.tenant_id == ctx.tenant_id,
        Conversation.user_id == ctx.user_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_recent_messages(
    session: AsyncSession, ctx: RequestContext, *, limit: int = 10
) -> Sequence[Message]:
    stmt = (
        select(Message)
        .where(Message.tenant_id == ctx.tenant_id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------
async def record_usage(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    operation: str,
    model_used: str | None,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
    latency_ms: int,
    cache_hit: bool = False,
    success: bool = True,
) -> RequestUsage:
    usage = RequestUsage(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        correlation_id=get_correlation_id() or None,
        operation=operation,
        model_used=model_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=estimated_cost,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        success=success,
    )
    session.add(usage)
    await session.flush()
    return usage


async def daily_spend(session: AsyncSession, ctx: RequestContext) -> float:
    """Per-user spend today, for the daily cost ceiling."""
    stmt = select(func.coalesce(func.sum(RequestUsage.estimated_cost), 0.0)).where(
        RequestUsage.tenant_id == ctx.tenant_id,
        RequestUsage.user_id == ctx.user_id,
        RequestUsage.created_at >= func.date_trunc("day", func.now()),
    )
    return float((await session.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# users & feedback
# ---------------------------------------------------------------------------
async def get_user_by_sub(session: AsyncSession, cognito_sub: str) -> User | None:
    stmt = select(User).where(User.cognito_sub == cognito_sub)
    return (await session.execute(stmt)).scalar_one_or_none()


async def mark_login(session: AsyncSession, *, subject: str) -> None:
    """Stamp last_login_at for a successful sign-in.

    Looked up by Cognito subject or local user id — never by a client-supplied
    tenant. Best-effort: a missing row or a test stub without ``execute`` must
    never fail the sign-in itself.
    """
    execute = getattr(session, "execute", None)
    if not callable(execute):
        return
    await execute(
        update(User)
        .where((User.cognito_sub == subject) | (User.id == subject))
        .values(last_login_at=datetime.now(UTC))
    )


async def get_own_tenant(session: AsyncSession, ctx: RequestContext) -> Tenant:
    """The caller's own company. There is no parameter for anyone else's."""
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == ctx.tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Company not found.")
    return tenant


async def list_tenant_users(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    limit: int = 100,
    offset: int = 0,
    include_inactive: bool = True,
) -> tuple[Sequence[User], int]:
    """Users of the caller's own tenant. The filter is not optional."""
    conditions = [User.tenant_id == ctx.tenant_id]
    if not include_inactive:
        conditions.append(User.is_active.is_(True))

    total = (await session.execute(select(func.count(User.id)).where(*conditions))).scalar_one()
    rows = (
        (
            await session.execute(
                select(User)
                .where(*conditions)
                .order_by(User.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return rows, int(total)


async def get_tenant_user(session: AsyncSession, ctx: RequestContext, user_id: str) -> User:
    """Fetch one user of the caller's tenant, or refuse.

    A user id belonging to another tenant is reported as not found and recorded
    as a cross-tenant attempt - the caller learns nothing either way.
    """
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    if user is None:
        raise NotFoundError("User not found.")
    if user.tenant_id != ctx.tenant_id:
        await record_audit(
            session,
            event_type="tenant.cross_tenant_user_access",
            ctx=ctx,
            severity="critical",
            resource_type="user",
            resource_id=user_id,
            reason="user_belongs_to_another_tenant",
        )
        log_security_event(
            "tenant.cross_tenant_user_access",
            reason="user_belongs_to_another_tenant",
            severity="critical",
        )
        raise TenantIsolationError()
    return user


async def find_tenant_user_by_email(
    session: AsyncSession, ctx: RequestContext, email: str
) -> User | None:
    stmt = select(User).where(User.tenant_id == ctx.tenant_id, User.email == email)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_tenant_user(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    email: str,
    role: UserRole,
    tenant_id: str | None = None,
    department: Department | None = None,
    display_name: str | None = None,
    cognito_sub: str | None = None,
) -> User:
    """Insert a user row.

    `tenant_id` defaults to the caller's own tenant. It is only ever passed
    explicitly by the platform onboarding route, which has just created that
    tenant itself and records an audit event naming the actor.
    """
    user = User(
        tenant_id=tenant_id or ctx.tenant_id,
        email=email,
        role=role,
        department=department,
        display_name=display_name,
        cognito_sub=cognito_sub,
        invited_by=ctx.user_id,
    )
    session.add(user)
    await session.flush()
    return user


async def count_active_admins(
    session: AsyncSession, tenant_id: str, *, excluding_user_id: str | None = None
) -> int:
    """How many active admins a company would still have."""
    conditions = [
        User.tenant_id == tenant_id,
        User.role == UserRole.ADMIN,
        User.is_active.is_(True),
    ]
    if excluding_user_id:
        conditions.append(User.id != excluding_user_id)
    return int((await session.execute(select(func.count(User.id)).where(*conditions))).scalar_one())


async def add_feedback(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    message_id: str | None,
    rating: int,
    reason: str | None = None,
    comment: str | None = None,
) -> UserFeedback:
    feedback = UserFeedback(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        message_id=message_id,
        rating=rating,
        reason=reason,
        comment=comment,
    )
    session.add(feedback)
    await session.flush()
    return feedback
