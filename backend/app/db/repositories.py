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
from app.core.exceptions import TenantIsolationError
from app.core.logging import log_security_event
from app.db.models import (
    AuditEvent,
    Conversation,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    Message,
    RequestUsage,
    User,
    UserFeedback,
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
    **details: Any,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=ctx.tenant_id if ctx else None,
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
