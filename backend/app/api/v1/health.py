"""Liveness and readiness. Unauthenticated by design - no data is exposed."""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.ratelimit import get_redis
from app.schemas import ComponentHealth, HealthResponse, MeResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: is the process up? Deliberately cheap and dependency-free."""
    return HealthResponse(status="ok", environment=settings.environment)


@router.get("/health/ready", response_model=HealthResponse)
async def readiness(session: DbSession, response: Response) -> HealthResponse:
    """Readiness: can we actually serve? Checks every hard dependency."""
    components: list[ComponentHealth] = []

    # database
    try:
        await session.execute(text("SELECT 1"))
        components.append(ComponentHealth(name="postgres", healthy=True))
    except Exception as exc:  # noqa: BLE001
        components.append(
            ComponentHealth(name="postgres", healthy=False, detail=type(exc).__name__)
        )

    # redis
    try:
        await get_redis().ping()
        components.append(ComponentHealth(name="redis", healthy=True))
    except Exception as exc:  # noqa: BLE001
        components.append(ComponentHealth(name="redis", healthy=False, detail=type(exc).__name__))

    # qdrant
    try:
        import asyncio

        from app.services.vector import get_client

        await asyncio.to_thread(get_client().get_collections)
        components.append(ComponentHealth(name="qdrant", healthy=True))
    except Exception as exc:  # noqa: BLE001
        components.append(ComponentHealth(name="qdrant", healthy=False, detail=type(exc).__name__))

    healthy = all(c.healthy for c in components)
    if not healthy:
        response.status_code = 503

    return HealthResponse(
        status="ok" if healthy else "degraded",
        environment=settings.environment,
        components=components,
    )


@router.get("/me", response_model=MeResponse)
async def me(ctx: CurrentUser) -> MeResponse:
    """Echo the verified principal - useful for debugging tenant binding."""
    return MeResponse(user_id=ctx.user_id, tenant_id=ctx.tenant_id, role=ctx.role, email=ctx.email)
