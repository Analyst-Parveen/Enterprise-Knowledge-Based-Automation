"""The ingestion pipeline: extract -> scan -> chunk -> embed -> Qdrant.

Runs in a worker, never in a request handler. Failure is recorded on the job and
partial work is rolled back - no orphaned chunks in Qdrant, no orphaned rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import Document, DocumentStatus, JobStatus, Modality
from app.services import storage, vector
from app.services.ai.provider import get_provider
from app.services.ingestion import extractors
from app.services.ingestion.chunker import Chunk, chunk_blocks
from app.services.security.injection import scan_content

logger = get_logger(__name__)

EMBED_BATCH_SIZE = 25  # batch embedding calls; never one call per chunk


@dataclass(slots=True)
class IngestionResult:
    chunks_written: int
    pages: int | None
    input_tokens: int
    estimated_cost: float
    model_used: str
    suspicious_chunks: int


async def _extract(
    document: Document, data: bytes
) -> tuple[list[extractors.ExtractedBlock], int, float, str]:
    """Dispatch to the right extractor. Returns blocks + AI usage incurred."""
    ext = document.original_filename.rsplit(".", 1)[-1].lower()
    tokens, cost, model = 0, 0.0, "none"

    if document.modality is Modality.IMAGE:
        blocks, result = await extractors.extract_image(data, document.content_type)
        tokens = result.input_tokens + result.output_tokens  # type: ignore[attr-defined]
        cost = result.estimated_cost  # type: ignore[attr-defined]
        model = result.model_used  # type: ignore[attr-defined]

    elif document.modality in (Modality.AUDIO, Modality.VIDEO):
        blocks = await extractors.extract_media(document.source_uri, document.modality)
        model = "amazon-transcribe"

    elif ext == "pdf":
        blocks = extractors.extract_pdf(data)
    elif ext in ("docx", "doc"):
        blocks = extractors.extract_docx(data)
    elif ext == "csv":
        blocks = extractors.extract_csv(data)
    elif ext in ("xlsx", "xls"):
        blocks = extractors.extract_excel(data)
    elif ext == "md":
        blocks = extractors.extract_markdown(data)
    else:
        blocks = extractors.extract_plaintext(data)

    return blocks, tokens, cost, model


async def run_ingestion(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    document: Document,
    job_id: str,
) -> IngestionResult:
    """Full pipeline for one document. Raises on failure after cleanup."""
    await repo.set_job_status(session, job_id, JobStatus.EXTRACTING, progress=10)
    await session.commit()

    key = storage.key_from_uri(document.source_uri)
    data = await storage.get_object(ctx, key)

    blocks, ai_tokens, ai_cost, ai_model = await _extract(document, data)
    if not blocks:
        raise ValueError("No readable content could be extracted from this document.")

    pages = max((b.page_number or 0 for b in blocks), default=0) or None

    # ---- chunk ---------------------------------------------------------
    await repo.set_job_status(session, job_id, JobStatus.CHUNKING, progress=40)
    await session.commit()

    chunks: list[Chunk] = chunk_blocks(blocks)
    if not chunks:
        raise ValueError("Document produced no indexable chunks.")

    # ---- scan for retrieval poisoning ----------------------------------
    flags: list[bool] = []
    suspicious_count = 0
    for chunk in chunks:
        result = scan_content(chunk.text)
        flagged = result.is_blocking
        flags.append(flagged)
        if flagged:
            suspicious_count += 1

    if suspicious_count:
        logger.warning(
            "ingestion_suspicious_chunks",
            extra={
                "extra": {
                    "document_id": document.id,
                    "suspicious": suspicious_count,
                    "total": len(chunks),
                }
            },
        )

    # ---- embed (batched) ------------------------------------------------
    await repo.set_job_status(session, job_id, JobStatus.EMBEDDING, progress=60)
    await session.commit()

    provider = get_provider()
    all_vectors: list[list[float]] = []
    embed_tokens = 0
    embed_cost = 0.0
    embed_model = "none"

    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        result = await provider.embed([c.text for c in batch])
        all_vectors.extend(result.vectors)
        embed_tokens += result.input_tokens
        embed_cost += result.estimated_cost
        embed_model = result.model_used

    if len(all_vectors) != len(chunks):
        raise RuntimeError("embedding count does not match chunk count")

    # ---- write to Qdrant -------------------------------------------------
    created_at = datetime.now(UTC).isoformat()
    payloads = [
        vector.ChunkPayload(
            document_id=document.id,
            chunk_id=str(uuid.uuid4()),
            document_name=document.name,
            page_number=chunk.page_number,
            source_uri=document.source_uri,
            owner_id=document.owner_id,
            tenant_id=document.tenant_id,  # mandatory
            document_version=document.version,
            created_by=document.created_by,
            created_at=created_at,
            text=chunk.text,
            modality=chunk.modality,
            department=document.department.value if document.department else None,
            section=chunk.section,
            suspicious=flagged,
        )
        for chunk, flagged in zip(chunks, flags, strict=True)
    ]

    try:
        written = await vector.upsert_chunks(all_vectors, payloads)
    except Exception:
        # No orphaned chunks: remove anything that did land.
        await vector.delete_document_chunks(ctx, document.id)
        raise

    return IngestionResult(
        chunks_written=written,
        pages=pages,
        input_tokens=ai_tokens + embed_tokens,
        estimated_cost=ai_cost + embed_cost,
        model_used=embed_model if ai_model == "none" else f"{ai_model}+{embed_model}",
        suspicious_chunks=suspicious_count,
    )


async def ingest_document(
    session: AsyncSession, ctx: RequestContext, document_id: str, job_id: str
) -> None:
    """Worker entrypoint. Never raises - failure is recorded on the job."""
    document = await repo.get_document(session, ctx, document_id)
    if document is None:
        await repo.set_job_status(
            session,
            job_id,
            JobStatus.FAILED,
            error_code="document_not_found",
            error_message="Document not found for this tenant.",
        )
        await session.commit()
        return

    document.status = DocumentStatus.PROCESSING
    await session.commit()

    try:
        result = await run_ingestion(session, ctx, document=document, job_id=job_id)
    except Exception as exc:  # noqa: BLE001 - the job records every failure mode
        logger.exception(
            "ingestion_failed", extra={"extra": {"document_id": document_id, "job_id": job_id}}
        )
        document.status = DocumentStatus.FAILED
        await repo.set_job_status(
            session,
            job_id,
            JobStatus.FAILED,
            error_code=type(exc).__name__,
            error_message=str(exc)[:2000],
        )
        await repo.record_audit(
            session,
            event_type="ingestion.failed",
            ctx=ctx,
            severity="warning",
            resource_type="document",
            resource_id=document_id,
            reason=type(exc).__name__,
        )
        await session.commit()
        return

    document.status = DocumentStatus.READY
    document.chunk_count = result.chunks_written
    document.page_count = result.pages
    document.doc_metadata = {
        **(document.doc_metadata or {}),
        "suspicious_chunks": result.suspicious_chunks,
        "embedding_model": settings.bedrock_embedding_model_id,
        "embedding_dimension": settings.bedrock_embedding_dimension,
    }

    await repo.set_job_status(
        session,
        job_id,
        JobStatus.COMPLETED,
        progress=100,
        chunks_written=result.chunks_written,
    )
    await repo.record_usage(
        session,
        ctx,
        operation="ingestion",
        model_used=result.model_used,
        input_tokens=result.input_tokens,
        output_tokens=0,
        estimated_cost=result.estimated_cost,
        latency_ms=0,
    )
    await repo.record_audit(
        session,
        event_type="ingestion.completed",
        ctx=ctx,
        resource_type="document",
        resource_id=document_id,
        reason=f"chunks_{result.chunks_written}",
    )
    await session.commit()

    logger.info(
        "ingestion_completed",
        extra={
            "extra": {
                "document_id": document_id,
                "chunks": result.chunks_written,
                "estimated_cost": result.estimated_cost,
            }
        },
    )
