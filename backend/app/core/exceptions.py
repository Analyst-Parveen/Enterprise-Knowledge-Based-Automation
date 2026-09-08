"""Application exceptions and their HTTP mapping.

Error responses never leak stack traces, SQL, or internal paths to the client.
See .claude/rules/coding-standards.md section 2.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.context import get_correlation_id
from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base for all deliberate application errors."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An internal error occurred."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Authentication required."


class AuthorizationError(AppError):
    status_code = 403
    code = "forbidden"
    message = "You are not authorized to perform this action."


class TenantIsolationError(AuthorizationError):
    """Raised on any attempt to reach across a tenant boundary.

    Always a security event. Never returns detail about the other tenant.
    """

    code = "cross_tenant_denied"
    message = "Resource not found."  # deliberately indistinguishable from 404


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found."


class ValidationError(AppError):
    status_code = 422
    code = "invalid_input"
    message = "The request was invalid."


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "The uploaded content exceeds the allowed size."


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"
    message = "This file type is not accepted."


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please slow down."

    def __init__(self, retry_after: int = 60, **details: Any) -> None:
        self.retry_after = retry_after
        super().__init__(**details)


class GuardrailError(AppError):
    """Input or output blocked by a guardrail stage."""

    status_code = 400
    code = "guardrail_blocked"
    message = "This request was blocked by a safety guardrail."


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"
    message = "An upstream service is unavailable."


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    headers = {}
    if isinstance(exc, RateLimitError):
        headers["Retry-After"] = str(exc.retry_after)

    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content={
            "error": {"code": exc.code, "message": exc.message},
            "correlation_id": get_correlation_id(),
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the detail; return nothing internal to the client."""
    logger.exception("unhandled_error", extra={"extra": {"path": request.url.path}})
    return JSONResponse(
        status_code=500,
        content={
            "error": {"code": "internal_error", "message": "An internal error occurred."},
            "correlation_id": get_correlation_id(),
        },
    )
