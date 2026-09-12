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
from app.core.exceptions import AuthenticationError
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
# rate limiting
# ---------------------------------------------------------------------------
async def rate_limit_api(ctx: CurrentUser) -> RequestContext:
    await ratelimit.enforce(ctx, "api")
    return ctx


async def rate_limit_upload(ctx: CurrentUser) -> RequestContext:
    # Uploads count against BOTH buckets: 5/min uploads and 20/min overall.
    await ratelimit.enforce(ctx, "api")
    await ratelimit.enforce(ctx, "upload")
    return ctx


async def rate_limit_server(ctx: CurrentUser) -> RequestContext:
    await ratelimit.enforce(ctx, "server")
    return ctx


RateLimitedUser = Annotated[RequestContext, Depends(rate_limit_api)]
UploadUser = Annotated[RequestContext, Depends(rate_limit_upload)]
ServerUser = Annotated[RequestContext, Depends(rate_limit_server)]
