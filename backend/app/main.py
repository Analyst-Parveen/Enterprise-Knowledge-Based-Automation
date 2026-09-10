"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import admin, agents, chat, documents, health
from app.core.config import settings
from app.core.exceptions import AppError, app_error_handler, unhandled_error_handler
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    BodySizeLimitMiddleware,
    CorrelationIdMiddleware,
    HealthAwareTrustedHostMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.ratelimit import close_redis
from app.db.session import dispose_engine

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "startup",
        extra={
            "extra": {
                "environment": settings.environment,
                "ai_provider": settings.ai_provider,
                "embedding_model": settings.bedrock_embedding_model_id,
                "embedding_dimension": settings.bedrock_embedding_dimension,
            }
        },
    )

    # Fail fast on a dimension mismatch rather than writing bad vectors.
    try:
        from app.services.vector import ensure_collection

        await ensure_collection()
    except Exception as exc:  # noqa: BLE001 - log and continue; readiness reports it
        logger.error("qdrant_init_failed", extra={"extra": {"error": str(exc)}})

    if settings.dev_auth_enabled:
        logger.warning(
            "dev_auth_enabled",
            extra={"extra": {"warning": "DEV AUTH IS ON - local development only"}},
        )

    yield

    await close_redis()
    await dispose_engine()
    logger.info("shutdown")


app = FastAPI(
    title="Enterprise Knowledge AI",
    version="0.2.0",
    description="Multimodal enterprise RAG and agentic automation platform.",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_dev else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.is_dev else None,
)

# --------------------------------------------------------------------------
# middleware (outermost first)
# --------------------------------------------------------------------------
app.add_middleware(HealthAwareTrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # explicit allow-list, never "*"
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    expose_headers=["X-Correlation-ID"],
    max_age=600,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=settings.max_request_bytes,
    exempt_prefixes=("/api/v1/documents",),  # uploads cap themselves while streaming
)

# --------------------------------------------------------------------------
# error handling
# --------------------------------------------------------------------------
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(documents.router, prefix=API_PREFIX)
app.include_router(chat.router, prefix=API_PREFIX)
app.include_router(agents.router, prefix=API_PREFIX)
app.include_router(admin.router, prefix=API_PREFIX)


@app.get("/", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse({"name": "Enterprise Knowledge AI", "version": "0.2.0", "docs": "/docs"})
