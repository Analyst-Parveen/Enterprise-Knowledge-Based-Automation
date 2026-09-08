"""Semantic cache, namespaced by tenant.

A semantically similar question from ANOTHER tenant must never return a cached
answer. Every key carries the tenant, and every stored entry is re-checked on
read. See .claude/rules/tenant-isolation.md section 5.

A cache hit never bypasses authentication, tenant filtering, or the output
guardrail - the caller re-applies those.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.context import RequestContext
from app.core.logging import get_logger, log_security_event
from app.core.ratelimit import get_redis

logger = get_logger(__name__)

_MAX_CANDIDATES = 50


@dataclass(slots=True)
class CachedAnswer:
    answer: str
    citations: list[dict[str, Any]]
    model_used: str
    confidence: float
    similarity: float


def _index_key(ctx: RequestContext) -> str:
    return f"{settings.project_code}:sc:idx:{ctx.tenant_id}"


def _entry_key(ctx: RequestContext, digest: str) -> str:
    return f"{settings.project_code}:sc:e:{ctx.tenant_id}:{digest}"


def _digest(question: str) -> str:
    return hashlib.sha256(question.strip().lower().encode()).hexdigest()[:32]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def lookup(
    ctx: RequestContext, question: str, query_vector: list[float]
) -> CachedAnswer | None:
    """Find a cached answer for a semantically equivalent question in THIS tenant."""
    client = get_redis()
    index_key = _index_key(ctx)

    try:
        digests = await client.lrange(index_key, 0, _MAX_CANDIDATES - 1)
    except Exception:  # noqa: BLE001 - cache must never break the request path
        return None

    best: CachedAnswer | None = None
    best_similarity = 0.0

    for digest in digests:
        try:
            raw = await client.get(_entry_key(ctx, digest))
        except Exception:  # noqa: BLE001, S112 - a cache miss must never break the request
            continue
        if not raw:
            continue

        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Re-verify tenant on read. A mismatch means the cache is compromised.
        if entry.get("tenant_id") != ctx.tenant_id:
            log_security_event(
                "tenant.cache_leak_detected",
                reason="cached_entry_tenant_mismatch",
                severity="critical",
            )
            continue

        similarity = _cosine(query_vector, entry.get("vector", []))
        if similarity >= settings.semantic_cache_similarity and similarity > best_similarity:
            best_similarity = similarity
            best = CachedAnswer(
                answer=entry["answer"],
                citations=entry.get("citations", []),
                model_used=entry.get("model_used", "unknown"),
                confidence=float(entry.get("confidence", 0.0)),
                similarity=round(similarity, 4),
            )

    return best


async def store(
    ctx: RequestContext,
    question: str,
    query_vector: list[float],
    *,
    answer: str,
    citations: list[dict[str, Any]],
    model_used: str,
    confidence: float,
) -> None:
    """Cache an answer under the caller's tenant namespace."""
    client = get_redis()
    digest = _digest(question)
    payload = json.dumps(
        {
            "tenant_id": ctx.tenant_id,
            "question": question,
            "vector": query_vector,
            "answer": answer,
            "citations": citations,
            "model_used": model_used,
            "confidence": confidence,
        }
    )

    try:
        pipe = client.pipeline()
        pipe.setex(_entry_key(ctx, digest), settings.semantic_cache_ttl_seconds, payload)
        pipe.lrem(_index_key(ctx), 0, digest)
        pipe.lpush(_index_key(ctx), digest)
        pipe.ltrim(_index_key(ctx), 0, _MAX_CANDIDATES - 1)
        pipe.expire(_index_key(ctx), settings.semantic_cache_ttl_seconds)
        await pipe.execute()
    except Exception:  # noqa: BLE001 - a cache write failure is not a request failure
        logger.warning("semantic_cache_store_failed")


async def invalidate_tenant(ctx: RequestContext) -> None:
    """Drop this tenant's cache - called when documents change."""
    client = get_redis()
    try:
        digests = await client.lrange(_index_key(ctx), 0, -1)
        if digests:
            await client.delete(*[_entry_key(ctx, d) for d in digests])
        await client.delete(_index_key(ctx))
    except Exception:  # noqa: BLE001
        logger.warning("semantic_cache_invalidate_failed")
