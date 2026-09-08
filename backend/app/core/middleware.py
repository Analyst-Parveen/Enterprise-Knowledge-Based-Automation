"""Correlation ID, security headers, and request size limiting."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.context import get_correlation_id, new_correlation_id, set_correlation_id
from app.core.logging import get_logger

logger = get_logger("request")

CORRELATION_HEADER = "X-Correlation-ID"

# One ID across frontend, API, backend, logs, AI calls, LangSmith, errors.
# See PROJECT.md section 10.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(self)",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Accept a client-supplied correlation ID or mint one, and echo it back."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(CORRELATION_HEADER, "").strip()
        # Only accept a sane-looking ID; never reflect arbitrary client input.
        cid = incoming if incoming.isalnum() and len(incoming) <= 64 else new_correlation_id()
        set_correlation_id(cid)

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        response.headers[CORRELATION_HEADER] = cid
        logger.info(
            "request",
            extra={
                "extra": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": elapsed_ms,
                }
            },
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        # HSTS only means anything over TLS, and only outside local dev.
        if not settings.is_dev:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class BodySizeLimitMiddleware:
    """Reject oversized bodies from Content-Length before anything is parsed.

    Upload routes enforce their own larger cap while streaming.
    See .claude/rules/security.md section 3.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, exempt_prefixes: tuple[str, ...] = ()) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith(self.exempt_prefixes):
            for name, value in scope.get("headers", []):
                if name == b"content-length":
                    try:
                        if int(value) > self.max_bytes:
                            response = JSONResponse(
                                status_code=413,
                                content={
                                    "error": {
                                        "code": "payload_too_large",
                                        "message": "Request body is too large.",
                                    },
                                    "correlation_id": get_correlation_id(),
                                },
                            )
                            await response(scope, receive, send)
                            return
                    except ValueError:
                        pass
                    break

        await self.app(scope, receive, send)
