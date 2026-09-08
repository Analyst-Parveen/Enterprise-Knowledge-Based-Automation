"""Pydantic request/response contracts. No bare dicts cross the API boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Department, DocumentStatus, JobStatus, Modality


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
class ComponentHealth(BaseModel):
    name: str
    healthy: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    environment: str
    components: list[ComponentHealth] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
class MeResponse(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    email: str | None = None


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------
class DocumentOut(ORMModel):
    id: str
    name: str
    original_filename: str
    content_type: str
    modality: Modality
    department: Department | None
    status: DocumentStatus
    size_bytes: int
    chunk_count: int
    page_count: int | None
    version: int
    owner_id: str
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class UploadResponse(BaseModel):
    document: DocumentOut
    job_id: str
    message: str = "Upload accepted. Ingestion has been queued."


class IngestionJobOut(ORMModel):
    id: str
    document_id: str
    status: JobStatus
    progress: int
    chunks_written: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None


class DeleteResponse(BaseModel):
    document_id: str
    deleted: bool = True
    chunks_removed: bool = True


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    # NOTE: there is deliberately no tenant_id field. Tenant comes from the JWT.
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    department: Department | None = None
    document_ids: list[str] | None = Field(default=None, max_length=20)


class CitationOut(BaseModel):
    source_number: int
    document_id: str
    document_name: str
    chunk_id: str
    page_number: int | None = None
    section: str | None = None
    score: float = 0.0


class RetrievedChunkOut(BaseModel):
    chunk_id: str | None = None
    document_id: str | None = None
    document_name: str | None = None
    page_number: int | None = None
    section: str | None = None
    modality: str | None = None
    score: float = 0.0
    preview: str = ""


class ChatResponseOut(BaseModel):
    """PROJECT.md section 7 - the full response envelope."""

    answer: str
    citations: list[CitationOut] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunkOut] = Field(default_factory=list)
    model_used: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    latency_ms: int
    cache_hit: bool
    tenant_id: str
    confidence: float
    correlation_id: str
    conversation_id: str | None = None


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------
class FeedbackRequest(BaseModel):
    message_id: str | None = None
    rating: int = Field(ge=-1, le=1)
    reason: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# admin / metrics
# ---------------------------------------------------------------------------
class UsageSummary(BaseModel):
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost: float
    avg_latency_ms: float
    cache_hit_rate: float


class AdminMetricsResponse(BaseModel):
    tenant_id: str
    usage: UsageSummary
    documents: int
    chunks: int
    ingestion_failures: int
    security_events: int


class AuditEventOut(ORMModel):
    id: str
    event_type: str
    severity: str
    resource_type: str | None
    resource_id: str | None
    reason: str | None
    user_id: str | None
    correlation_id: str | None
    created_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)
