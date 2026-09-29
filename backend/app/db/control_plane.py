"""Control-plane data access: the tenant registry itself.

This module is the deliberate exception to repositories.py. Those functions are
all tenant-filtered because they touch tenant *data*. These touch the *registry*
of tenants, which is platform-owned - a company is not data belonging to itself.

Three rules make the exception safe, and they are the reason this code is not in
repositories.py where it could be reached by accident:

1. Every function here is reachable only behind `PlatformAdminUser`, and the
   platform role is bound to the reserved platform tenant in core/auth.py.
2. Nothing here returns tenant *content* - no documents, conversations or
   messages. Onboarding a company never means being able to read its files.
3. Every mutation writes an audit event naming the actor.

See .claude/rules/tenant-isolation.md section 2, which requires exactly this:
a cross-tenant admin view must be explicitly separate and explicitly audited.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import PLATFORM_TENANT_ID, RequestContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    AuditEvent,
    LlmUsagePeriod,
    PlanEntitlement,
    SubscriptionPlan,
    Tenant,
    TenantSubscription,
    User,
    UserRole,
)
from app.services.llm_quota import ENTITLEMENT_KEY, current_period


async def get_tenant(session: AsyncSession, tenant_id: str) -> Tenant:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Company not found.")
    return tenant


async def tenant_id_taken(session: AsyncSession, tenant_id: str, slug: str) -> bool:
    stmt = select(func.count(Tenant.id)).where((Tenant.id == tenant_id) | (Tenant.slug == slug))
    return int((await session.execute(stmt)).scalar_one()) > 0


async def create_tenant(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    tenant_id: str,
    name: str,
    contact_email: str | None,
) -> Tenant:
    if await tenant_id_taken(session, tenant_id, tenant_id):
        raise ValidationError("A company with that id already exists.")

    tenant = Tenant(
        id=tenant_id,
        name=name,
        slug=tenant_id,
        contact_email=contact_email,
        created_by=ctx.user_id,
        is_active=True,
    )
    session.add(tenant)
    await session.flush()
    from app.services.billing import grant_complimentary

    await grant_complimentary(session, tenant.id)
    return tenant


async def list_tenants(session: AsyncSession) -> Sequence[Tenant]:
    """Every company except the platform's own operations tenant."""
    return (
        (
            await session.execute(
                select(Tenant)
                .where(Tenant.id != PLATFORM_TENANT_ID)
                .order_by(Tenant.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def tenant_user_counts(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Per-tenant seat counts, in one query rather than one per tenant."""
    rows = (
        await session.execute(
            select(
                User.tenant_id,
                func.count(User.id),
                func.coalesce(func.sum(cast(User.is_active, Integer)), 0),
                func.count(User.id).filter(User.role == UserRole.ADMIN),
            ).group_by(User.tenant_id)
        )
    ).all()
    return {
        str(tenant_id): {
            "users": int(total),
            "active_users": int(active),
            "admins": int(admins),
        }
        for tenant_id, total, active, admins in rows
    }


async def tenant_billing_summaries(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Per-tenant subscription state, in one query rather than one per tenant.

    Billing facts only - which plan a company bought, whether it is active, and
    when the period ends. This is registry data about the account, not content
    belonging to it, so rule 2 in this module's header still holds: nothing here
    can reach a document, a chunk, a conversation or a message.
    """
    rows = (
        await session.execute(
            select(
                TenantSubscription.tenant_id,
                TenantSubscription.plan_id,
                TenantSubscription.status,
                TenantSubscription.complimentary,
                TenantSubscription.current_period_end,
                TenantSubscription.cancel_at_period_end,
                SubscriptionPlan.name,
                SubscriptionPlan.interval,
                SubscriptionPlan.base_amount_paise,
            ).join(SubscriptionPlan, SubscriptionPlan.id == TenantSubscription.plan_id)
        )
    ).all()
    return {
        str(tenant_id): {
            "plan_id": str(plan_id),
            "plan_name": str(plan_name),
            "interval": interval.value if hasattr(interval, "value") else str(interval),
            "status": status.value if hasattr(status, "value") else str(status),
            "complimentary": bool(complimentary),
            "cancel_at_period_end": bool(cancel_at_period_end),
            "current_period_end": period_end,
            "base_amount_paise": int(base_amount_paise),
        }
        for (
            tenant_id,
            plan_id,
            status,
            complimentary,
            period_end,
            cancel_at_period_end,
            plan_name,
            interval,
            base_amount_paise,
        ) in rows
    }


async def tenant_token_usage(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Per-tenant LLM token consumption this period, in one query.

    Metering, not content: how much allowance an account has consumed is the
    same kind of fact as how many seats it holds.

    ``used`` is committed plus reserved, because that is what quota enforcement
    itself compares against the limit - reservation_allowed() weighs
    ``committed + reserved + estimate``. Reporting only the committed figure
    would show a company under its allowance at the very moment enforcement had
    already started refusing it. Both parts are returned separately as well, so
    the in-flight share stays visible rather than hidden inside one number.

    This only reads the row the quota service maintains; nothing here reserves,
    releases or alters a balance.
    """
    start, _ = current_period()
    rows = (
        await session.execute(
            select(
                LlmUsagePeriod.tenant_id,
                LlmUsagePeriod.total_tokens,
                LlmUsagePeriod.reserved_tokens,
                LlmUsagePeriod.request_count,
            ).where(LlmUsagePeriod.period_start == start.date())
        )
    ).all()
    return {
        str(tenant_id): {
            "committed_tokens": int(committed or 0),
            "reserved_tokens": int(reserved or 0),
            "used": int(committed or 0) + int(reserved or 0),
            "request_count": int(requests or 0),
        }
        for tenant_id, committed, reserved, requests in rows
    }


async def plan_token_limits(session: AsyncSession) -> dict[str, int]:
    """The plan allowance, so a quota bar needs no second round trip.

    The key comes from the quota service rather than a literal here: if that
    entitlement is ever renamed, this reads the new one instead of silently
    reporting no limit at all.
    """
    rows = (
        await session.execute(
            select(PlanEntitlement.plan_id, PlanEntitlement.entitlement_value).where(
                PlanEntitlement.entitlement_key == ENTITLEMENT_KEY
            )
        )
    ).all()
    return {str(plan_id): int(value) for plan_id, value in rows}


async def find_user_in_tenant(session: AsyncSession, tenant_id: str, email: str) -> User | None:
    stmt = select(User).where(User.tenant_id == tenant_id, User.email == email)
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_tenant_admins(session: AsyncSession, tenant_id: str) -> int:
    stmt = select(func.count(User.id)).where(
        User.tenant_id == tenant_id,
        User.role == UserRole.ADMIN,
        User.is_active.is_(True),
    )
    return int((await session.execute(stmt)).scalar_one())


async def list_control_plane_events(
    session: AsyncSession, *, limit: int = 100
) -> Sequence[AuditEvent]:
    """The onboarding trail across every tenant.

    Scoped to control-plane event types by prefix, so this view can never turn
    into a window onto another tenant's chat, retrieval or document activity.
    """
    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.event_type.like("tenant.%")
            | AuditEvent.event_type.like("user.%")
            | AuditEvent.event_type.like("platform.%")
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()
