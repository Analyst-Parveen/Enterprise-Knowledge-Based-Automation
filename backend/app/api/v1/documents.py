"""Document upload, listing, status, and deletion."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, UploadFile

from app.api.deps import CurrentUser, DbSession, RateLimitedUser, UploadUser
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import Department, Document, IngestionJob
from app.db.session import get_sessionmaker
from app.schemas import (
    DeleteResponse,
    DocumentListResponse,
    DocumentOut,
    IngestionJobOut,
    UploadResponse,
)
from app.services import storage, vector
from app.services.ingestion.pipeline import ingest_document
from app.services.rag import cache
from app.services.security import files

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

_UPLOAD_READ_CHUNK = 1024 * 1024  # 1 MB


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_document(
    ctx: UploadUser,
    session: DbSession,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    department: Department | None = Form(default=None),
) -> UploadResponse:
    """Accept a document, store it, and queue ingestion.

    Safety order: extension -> magic bytes -> size -> server-generated key.
    See .claude/rules/security.md section 5.
    """
    if not file.filename:
        raise ValidationError("A filename is required.")

    # 1. extension allow-list
    ext, content_type, modality = files.validate_extension(file.filename)

    # 2 & 3. stream with a hard size cap, sniffing magic bytes from the head
    buffer = bytearray()
    head = b""
    while True:
        piece = await file.read(_UPLOAD_READ_CHUNK)
        if not piece:
            break
        if not head:
            head = piece[:32]
            files.verify_magic_bytes(ext, head)
        buffer.extend(piece)
        files.enforce_size(len(buffer), settings.s3_upload_max_bytes)

    data = bytes(buffer)
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if not head:
        files.verify_magic_bytes(ext, data[:32])

    # 4. key is derived from the verified tenant + a uuid, never the filename
    key = files.build_storage_key(ctx.tenant_id, ext)
    source_uri = await storage.put_object(key, data, content_type)

    document = Document(
        tenant_id=ctx.tenant_id,
        owner_id=ctx.user_id,
        created_by=ctx.user_id,
        name=files.sanitize_display_name(file.filename),
        original_filename=files.sanitize_display_name(file.filename),
        content_type=content_type,
        modality=modality,
        department=department,
        source_uri=source_uri,
        size_bytes=len(data),
        checksum_sha256=files.sha256_of(data),
    )
    session.add(document)
    await session.flush()

    job = IngestionJob(
        tenant_id=ctx.tenant_id,
        document_id=document.id,
        created_by=ctx.user_id,
        correlation_id=ctx.correlation_id or None,
    )
    session.add(job)
    await session.flush()

    await repo.record_audit(
        session,
        event_type="document.uploaded",
        ctx=ctx,
        resource_type="document",
        resource_id=document.id,
        reason=f"modality_{modality.value}",
    )
    await session.commit()
    await session.refresh(document)

    # Ingestion runs outside the request. Never parse documents in a handler.
    background.add_task(_run_ingestion_task, ctx, document.id, job.id)

    return UploadResponse(document=DocumentOut.model_validate(document), job_id=job.id)


async def _run_ingestion_task(ctx, document_id: str, job_id: str) -> None:  # type: ignore[no-untyped-def]
    """Background ingestion with its own session (the request's is closed)."""
    from app.core.context import set_request_context

    set_request_context(ctx)
    async with get_sessionmaker()() as session:
        try:
            await ingest_document(session, ctx, document_id, job_id)
            await cache.invalidate_tenant(ctx)
        except Exception:  # noqa: BLE001 - never let a task die silently
            logger.exception(
                "ingestion_task_crashed",
                extra={"extra": {"document_id": document_id, "job_id": job_id}},
            )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    ctx: RateLimitedUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    department: Department | None = Query(default=None),
) -> DocumentListResponse:
    rows, total = await repo.list_documents(
        session,
        ctx,
        limit=limit,
        offset=offset,
        department=department.value if department else None,
    )
    return DocumentListResponse(
        items=[DocumentOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(ctx: RateLimitedUser, session: DbSession, document_id: str) -> DocumentOut:
    # Raises TenantIsolationError (as 403/not-found) for another tenant's document.
    document = await repo.require_document(session, ctx, document_id)
    return DocumentOut.model_validate(document)


@router.get("/{document_id}/status", response_model=IngestionJobOut)
async def get_ingestion_status(
    ctx: RateLimitedUser, session: DbSession, document_id: str
) -> IngestionJobOut:
    await repo.require_document(session, ctx, document_id)
    jobs = await repo.list_jobs(session, ctx, limit=200)
    for job in jobs:
        if job.document_id == document_id:
            return IngestionJobOut.model_validate(job)
    raise NotFoundError("No ingestion job found for this document.")


@router.get("/{document_id}/download")
async def download_document(
    ctx: RateLimitedUser, session: DbSession, document_id: str
) -> dict[str, str]:
    """Short-lived pre-signed URL, issued only after the tenant check."""
    document = await repo.require_document(session, ctx, document_id)
    key = storage.key_from_uri(document.source_uri)
    url = await storage.presigned_url(ctx, key, expires_in=300)
    return {"url": url, "expires_in_seconds": "300"}


@router.delete("/{document_id}", response_model=DeleteResponse)
async def delete_document(ctx: CurrentUser, session: DbSession, document_id: str) -> DeleteResponse:
    """Deletion requires ownership or admin in the same tenant. Always audited.

    Removes the S3 object, the DB row (soft), and ALL Qdrant points.
    """
    document = await repo.soft_delete_document(session, ctx, document_id)
    key = storage.key_from_uri(document.source_uri)

    # Vector points and the object go together - a document is not "deleted"
    # while its chunks remain searchable.
    await vector.delete_document_chunks(ctx, document_id)
    await asyncio.gather(
        storage.delete_object(ctx, key),
        cache.invalidate_tenant(ctx),
        return_exceptions=True,
    )
    await session.commit()

    return DeleteResponse(document_id=document_id)
