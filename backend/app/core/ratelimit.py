"""Redis-backed per-user rate limiting.

Limits (PROJECT.md section 9), all per user per minute:
    api      20
    server   10
    upload    5

Keys are namespaced by tenant AND user so buckets never collide across tenants.
See .claude/rules/tenant-isolation.md section 5.
"""

from __future__ import annotations

from typing import Literal

import redis.asyncio as redis

from app.core.config import settings
from app.core.context import RequestContext
from app.core.exceptions import RateLimitError
from app.core.logging import log_security_event

Bucket = Literal["api", "server", "upload"]

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _limit_for(bucket: Bucket) -> int:
    return {
        "api": settings.rate_limit_requests_per_min,
        "server": settings.rate_limit_server_requests_per_min,
        "upload": settings.rate_limit_uploads_per_min,
    }[bucket]


async def enforce(ctx: RequestContext, bucket: Bucket = "api") -> None:
    """Fixed-window counter. Raises RateLimitError with Retry-After when exceeded."""
    limit = _limit_for(bucket)
    key = f"{settings.project_code}:rl:{bucket}:{ctx.tenant_id}:{ctx.user_id}"

    client = get_redis()
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.ttl(key)
    count, ttl = await pipe.execute()

    if int(count) == 1:
        await client.expire(key, 60)
        ttl = 60

    if int(count) > limit:
        retry_after = int(ttl) if int(ttl) > 0 else 60
        log_security_event(
            "ratelimit.exceeded",
            reason=f"bucket_{bucket}_limit_{limit}",
            bucket=bucket,
            limit=limit,
        )
        raise RateLimitError(retry_after=retry_after)
