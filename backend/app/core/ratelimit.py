"""Redis-backed per-user rate limiting.

Limits (PROJECT.md section 9), all per user per minute:
    api      20
    server   10
    upload    5
    auth     10   per account - sign-in has no principal to key on yet

Keys are namespaced by tenant AND user so buckets never collide across tenants.
See .claude/rules/tenant-isolation.md section 5.
"""

from __future__ import annotations

import hashlib
from typing import Literal

import redis.asyncio as redis

from app.core.config import settings
from app.core.context import RequestContext
from app.core.exceptions import RateLimitError
from app.core.logging import log_security_event

Bucket = Literal["api", "server", "upload", "auth"]

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
        "auth": settings.rate_limit_auth_attempts_per_min,
    }[bucket]


async def _consume(key: str, bucket: Bucket) -> None:
    """Fixed-window counter. Raises RateLimitError with Retry-After when exceeded."""
    limit = _limit_for(bucket)

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


async def enforce(ctx: RequestContext, bucket: Bucket = "api") -> None:
    await _consume(f"{settings.project_code}:rl:{bucket}:{ctx.tenant_id}:{ctx.user_id}", bucket)


async def enforce_anonymous(identifier: str, bucket: Bucket = "auth") -> None:
    """Rate limit for endpoints reached before any principal exists.

    Keyed by the account being authenticated rather than the client IP: behind
    CloudFront and an ALB the client IP is either shared by everyone or taken
    from a client-supplied `X-Forwarded-For`, so an IP bucket would be both
    unfair and trivially rotated. The account name is the thing under attack in
    credential stuffing, and it cannot be rotated away.

    The identifier is hashed so the Redis keyspace never holds an address.
    """
    digest = hashlib.sha256(f"{settings.project_code}:{identifier}".encode()).hexdigest()[:32]
    await _consume(f"{settings.project_code}:rl:{bucket}:{digest}", bucket)
