"""Qdrant vector store.

THE CENTRAL RULE OF THIS MODULE:
    Every search takes a RequestContext and builds the tenant filter INTERNALLY.
    There is no parameter that lets a caller supply their own filter object, and
    no search function that can be called without a tenant. A retrieval function
    that could omit the tenant filter must not exist - so it doesn't.

See .claude/rules/tenant-isolation.md section 4.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.core.config import settings
from app.core.context import RequestContext
from app.core.exceptions import UpstreamError
from app.core.logging import get_logger, log_security_event

logger = get_logger(__name__)

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
            timeout=30,
        )
    return _client


@dataclass(slots=True)
class ChunkPayload:
    """The vector metadata contract from PROJECT.md section 6.

    tenant_id is mandatory - a point without it breaks isolation.
    """

    document_id: str
    chunk_id: str
    document_name: str
    page_number: int | None
    source_uri: str
    owner_id: str
    tenant_id: str
    document_version: int
    created_by: str
    created_at: str
    # extra context used by retrieval and citations
    text: str = ""
    modality: str = "text"
    department: str | None = None
    section: str | None = None
    suspicious: bool = False  # flagged by the poisoning scanner

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "document_name": self.document_name,
            "page_number": self.page_number,
            "source_uri": self.source_uri,
            "owner_id": self.owner_id,
            "tenant_id": self.tenant_id,
            "document_version": self.document_version,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "text": self.text,
            "modality": self.modality,
            "department": self.department,
            "section": self.section,
            "suspicious": self.suspicious,
        }


@dataclass(slots=True)
class SearchHit:
    score: float
    payload: dict[str, Any]

    @property
    def text(self) -> str:
        return str(self.payload.get("text", ""))

    @property
    def tenant_id(self) -> str:
        return str(self.payload.get("tenant_id", ""))


# ---------------------------------------------------------------------------
# collection lifecycle
# ---------------------------------------------------------------------------
async def ensure_collection() -> None:
    """Create the collection if absent, with a tenant_id payload index.

    The tenant_id index is not optional - it is what makes the mandatory filter
    fast enough that nobody is ever tempted to drop it.
    """
    dim = settings.bedrock_embedding_dimension
    name = settings.qdrant_collection

    def _ensure() -> None:
        client = get_client()
        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            client.create_collection(
                collection_name=name,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )
            logger.info("qdrant_collection_created", extra={"extra": {"name": name, "dim": dim}})
        else:
            info = client.get_collection(name)
            actual = info.config.params.vectors.size  # type: ignore[union-attr]
            if actual != dim:
                raise RuntimeError(
                    f"Qdrant collection '{name}' has dimension {actual} but the configured "
                    f"embedding model produces {dim}. Never mix embedding models in one "
                    "collection - create a new collection and re-index."
                )

        for field, schema in (
            ("tenant_id", qm.PayloadSchemaType.KEYWORD),
            ("document_id", qm.PayloadSchemaType.KEYWORD),
            ("owner_id", qm.PayloadSchemaType.KEYWORD),
            ("department", qm.PayloadSchemaType.KEYWORD),
        ):
            try:
                client.create_payload_index(
                    collection_name=name, field_name=field, field_schema=schema
                )
            except Exception:  # noqa: BLE001, S110 - index already exists is fine
                pass

    try:
        await asyncio.to_thread(_ensure)
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("qdrant_ensure_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("The vector store is unavailable.") from exc


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------
async def upsert_chunks(vectors: list[list[float]], payloads: list[ChunkPayload]) -> int:
    """Write chunks. Every payload MUST carry a tenant_id."""
    if len(vectors) != len(payloads):
        raise ValueError("vector/payload length mismatch")

    for payload in payloads:
        if not payload.tenant_id:
            raise ValueError("refusing to write a chunk without tenant_id")

    points = [
        qm.PointStruct(id=p.chunk_id, vector=v, payload=p.to_dict())
        for v, p in zip(vectors, payloads, strict=True)
    ]

    def _upsert() -> None:
        get_client().upsert(collection_name=settings.qdrant_collection, points=points, wait=True)

    try:
        await asyncio.to_thread(_upsert)
    except Exception as exc:  # noqa: BLE001
        logger.error("qdrant_upsert_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("The vector store is unavailable.") from exc

    return len(points)


# ---------------------------------------------------------------------------
# search - tenant filter built internally, never supplied by the caller
# ---------------------------------------------------------------------------
def _tenant_filter(
    ctx: RequestContext,
    *,
    document_ids: list[str] | None = None,
    department: str | None = None,
    include_suspicious: bool = False,
) -> qm.Filter:
    must: list[qm.Condition] = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=ctx.tenant_id))
    ]
    if document_ids:
        must.append(qm.FieldCondition(key="document_id", match=qm.MatchAny(any=document_ids)))
    if department:
        must.append(qm.FieldCondition(key="department", match=qm.MatchValue(value=department)))

    must_not: list[qm.Condition] = []
    if not include_suspicious:
        # Retrieval-poisoning defence: quarantined chunks stay out by default.
        must_not.append(qm.FieldCondition(key="suspicious", match=qm.MatchValue(value=True)))

    return qm.Filter(must=must, must_not=must_not or None)


async def search(
    ctx: RequestContext,
    query_vector: list[float],
    *,
    top_k: int | None = None,
    document_ids: list[str] | None = None,
    department: str | None = None,
    score_threshold: float | None = None,
) -> list[SearchHit]:
    """Tenant-filtered similarity search. The filter cannot be overridden."""
    limit = top_k or settings.retrieval_top_k
    query_filter = _tenant_filter(ctx, document_ids=document_ids, department=department)

    def _search() -> list[Any]:
        # query_points, not the removed search(): qdrant-client dropped
        # QdrantClient.search() in 1.19. query_points returns a QueryResponse
        # whose .points hold the scored hits.
        response = get_client().query_points(
            collection_name=settings.qdrant_collection,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
        )
        return list(response.points)

    try:
        results = await asyncio.to_thread(_search)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "qdrant_search_failed",
            extra={"extra": {"error": type(exc).__name__, "detail": str(exc)[:200]}},
        )
        raise UpstreamError("The vector store is unavailable.") from exc

    hits = [SearchHit(score=float(r.score), payload=dict(r.payload or {})) for r in results]

    # Defence in depth: verify what came back actually belongs to this tenant.
    # If Qdrant ever returned a foreign chunk, that is a critical incident.
    clean: list[SearchHit] = []
    for hit in hits:
        if hit.tenant_id != ctx.tenant_id:
            log_security_event(
                "tenant.vector_leak_detected",
                reason="qdrant_returned_foreign_tenant_chunk",
                severity="critical",
            )
            continue
        clean.append(hit)

    return clean


async def delete_document_chunks(ctx: RequestContext, document_id: str) -> None:
    """Remove every chunk of a document - scoped to the caller's tenant."""
    selector = qm.FilterSelector(
        filter=qm.Filter(
            must=[
                qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=ctx.tenant_id)),
                qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id)),
            ]
        )
    )

    def _delete() -> None:
        get_client().delete(
            collection_name=settings.qdrant_collection, points_selector=selector, wait=True
        )

    try:
        await asyncio.to_thread(_delete)
    except Exception as exc:  # noqa: BLE001
        logger.error("qdrant_delete_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("The vector store is unavailable.") from exc


async def count_chunks(ctx: RequestContext) -> int:
    def _count() -> int:
        result = get_client().count(
            collection_name=settings.qdrant_collection,
            count_filter=qm.Filter(
                must=[qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=ctx.tenant_id))]
            ),
            exact=True,
        )
        return int(result.count)

    try:
        return await asyncio.to_thread(_count)
    except Exception:  # noqa: BLE001
        return 0


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
