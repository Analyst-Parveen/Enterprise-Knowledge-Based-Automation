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

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import PLATFORM_TENANT_ID, RequestContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import AuditEvent, Tenant, User, UserRole


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
