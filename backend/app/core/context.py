"""Request-scoped context: correlation ID and the authenticated principal.

The tenant_id carried here is resolved ONCE from the verified JWT and is the only
tenant identity the application trusts. A tenant_id appearing in a request body,
path, query string, or header is never authoritative.

See .claude/rules/tenant-isolation.md section 2.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

Role = Literal["user", "admin"]

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_request_ctx: ContextVar[RequestContext | None] = ContextVar("request_ctx", default=None)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The authenticated principal for one request. Immutable by design."""

    user_id: str
    tenant_id: str
    role: Role
    email: str | None = None
    correlation_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# --------------------------------------------------------------------------
# correlation id
# --------------------------------------------------------------------------
def new_correlation_id() -> str:
    return uuid.uuid4().hex


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str:
    return _correlation_id.get()


# --------------------------------------------------------------------------
# request context
# --------------------------------------------------------------------------
def set_request_context(ctx: RequestContext) -> None:
    _request_ctx.set(ctx)


def get_request_context() -> RequestContext | None:
    return _request_ctx.get()


def require_request_context() -> RequestContext:
    """Fail loudly rather than silently operating without a tenant."""
    ctx = _request_ctx.get()
    if ctx is None:
        raise RuntimeError(
            "no RequestContext bound - refusing to operate without a tenant identity"
        )
    return ctx
