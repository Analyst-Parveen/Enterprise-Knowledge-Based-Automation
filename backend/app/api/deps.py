"""FastAPI dependencies: authentication, RBAC, rate limiting.

The authenticated principal is bound into the request context here, once. Routes
receive a RequestContext and never parse tokens or read tenant IDs themselves.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.auth import (
    require_admin,
    require_platform_admin,
    require_tenant_admin,
    verify_token,
)
from app.core.context import RequestContext, set_request_context
from app.core.exceptions import AuthenticationError, CompanySuspendedError
from app.core.logging import log_security_event
from app.db import repositories as repo
from app.db.session import get_session

_bearer = HTTPBearer(auto_error=False)


async def current_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> RequestContext:
    """Verify the JWT and bind the principal for the rest of the request."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError()

    ctx = verify_token(credentials.credentials)
    set_request_context(ctx)
    request.state.ctx = ctx
    return ctx


CurrentUser = Annotated[RequestContext, Depends(current_context)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


async def admin_context(ctx: CurrentUser) -> RequestContext:
    """Admin gate. Admin does NOT grant cross-tenant data access."""
    require_admin(ctx)
    return ctx


AdminUser = Annotated[RequestContext, Depends(admin_context)]


async def tenant_admin_context(ctx: CurrentUser) -> RequestContext:
    """Gate for managing the users of one company, scoped to that company."""
    require_tenant_admin(ctx)
    return ctx


TenantAdminUser = Annotated[RequestContext, Depends(tenant_admin_context)]


async def platform_admin_context(ctx: CurrentUser) -> RequestContext:
    """Service-provider gate. The only role that can create a tenant."""
    require_platform_admin(ctx)
    return ctx


PlatformAdminUser = Annotated[RequestContext, Depends(platform_admin_context)]


# ---------------------------------------------------------------------------
# company suspension
# ---------------------------------------------------------------------------
async def _require_active_tenant(ctx: RequestContext, session: AsyncSession) -> None:
    """Block a suspended company's workspace, server-side.

    Platform operators live in the platform tenant and must keep working so
    they can reactivate a company - they are never subject to this check.
    """
    if ctx.is_platform_admin:
        return
    if await repo.tenant_is_suspended(session, ctx.tenant_id):
        log_security_event(
            "tenant.suspended_access_denied", reason="company_suspended", severity="warning"
        )
        raise CompanySuspendedError()


async def active_tenant_context(ctx: CurrentUser, session: DbSession) -> RequestContext:
    await _require_active_tenant(ctx, session)
    return ctx


ActiveTenantUser = Annotated[RequestContext, Depends(active_tenant_context)]


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------
async def rate_limit_api(ctx: CurrentUser, session: DbSession) -> RequestContext:
    await ratelimit.enforce(ctx, "api")
    await _require_active_tenant(ctx, session)
    return ctx


async def rate_limit_upload(ctx: CurrentUser, session: DbSession) -> RequestContext:
    # Uploads count against BOTH buckets: 5/min uploads and 20/min overall.
    await ratelimit.enforce(ctx, "api")
    await ratelimit.enforce(ctx, "upload")
    await _require_active_tenant(ctx, session)
    return ctx


async def rate_limit_server(ctx: CurrentUser, session: DbSession) -> RequestContext:
    await ratelimit.enforce(ctx, "server")
    await _require_active_tenant(ctx, session)
    return ctx


RateLimitedUser = Annotated[RequestContext, Depends(rate_limit_api)]
UploadUser = Annotated[RequestContext, Depends(rate_limit_upload)]
ServerUser = Annotated[RequestContext, Depends(rate_limit_server)]
