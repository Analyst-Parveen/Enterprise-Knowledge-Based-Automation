"""Admin endpoints: operational metrics and audit logs.

Admin grants access to OPERATIONAL data for the admin's own tenant. It does not
grant cross-tenant data access - every query here is still tenant-filtered.
See .claude/rules/tenant-isolation.md section 2.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import Integer, cast, func, select

from app.api.deps import AdminUser, DbSession
from app.db.models import (
    AuditEvent,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    RequestUsage,
)
from app.schemas import AdminMetricsResponse, AuditEventOut, UsageSummary
from app.services import vector

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
