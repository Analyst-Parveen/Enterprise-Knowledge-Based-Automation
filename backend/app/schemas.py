"""Pydantic request/response contracts. No bare dicts cross the API boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import Department, DocumentStatus, JobStatus, Modality
from app.services.agents.state import WorkflowType

# Never widen this to include "platform_admin". The API contract itself must
# make platform privileges unrequestable, so the escalation is impossible to
# express before any handler code runs. The server-side check in
# services/onboarding.py is the second, independent line of defence.
TenantAssignableRole = Literal["user", "admin"]

# Long enough that a policy-compliant Cognito password always fits, short
# enough that nothing enormous is ever forwarded upstream.
Password = Annotated[str, Field(min_length=12, max_length=256)]

# Deliberately not pydantic's EmailStr: that pulls in email-validator, and the
# authoritative check is Cognito's anyway. This rejects the shapes that are
# obviously not addresses and normalizes case, which is what the unique
# constraint on (tenant_id, email) needs.
Email = Annotated[
    str,
    Field(min_length=5, max_length=320, pattern=r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$"),
]


class EmailNormalizingModel(BaseModel):
    """Lower-cases every `email` field so one person cannot become two rows."""

    @field_validator("email", check_fields=False, mode="before")
    @classmethod
    def _normalize_email(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v


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
    tenant_name: str | None = None


# ---------------------------------------------------------------------------
# authentication
#
# There is no tenant_id or role anywhere in these requests. A client states who
# it claims to be and proves it; what it is *allowed* to be comes from the
# directory, through the token, and from nowhere else.
# ---------------------------------------------------------------------------
class LoginRequest(EmailNormalizingModel):
    email: Email
    password: Password


class SessionResponse(BaseModel):
    """Issued session.

    `token` is the bearer token for every other endpoint. `challenge` is set
    instead when one more step is required before a session exists - a first
    sign-in on an invited account, for example.
    """

    token: str | None = None
    expires_in: int | None = None
    refresh_token: str | None = None
    challenge: str | None = None
    challenge_session: str | None = None
    user: MeResponse | None = None


class NewPasswordRequest(EmailNormalizingModel):
    """Completes a first sign-in on an invited account."""

    email: Email
    challenge_session: str = Field(min_length=1, max_length=8192)
    new_password: Password


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=8192)


class ForgotPasswordRequest(EmailNormalizingModel):
    email: Email


class ConfirmPasswordResetRequest(EmailNormalizingModel):
    email: Email
    code: str = Field(min_length=1, max_length=32)
    new_password: Password


class AcknowledgedResponse(BaseModel):
    """A deliberately uninformative 'we have handled it'.

    Password reset uses this so the response is identical whether or not the
    address is registered. See .claude/rules/security.md section 1.
    """

    status: str = "accepted"
    message: str = "If that account exists, instructions are on their way."


# ---------------------------------------------------------------------------
# tenants (control plane - platform_admin only)
# ---------------------------------------------------------------------------
class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    # Optional: derived from the name when omitted. Validated and checked
    # against the reserved list server-side either way.
    tenant_id: str | None = Field(default=None, min_length=2, max_length=64)
    contact_email: Email | None = None

    @field_validator("contact_email", mode="before")
    @classmethod
    def _blank_contact_is_absent(cls, v: Any) -> Any:
        if isinstance(v, str):
            stripped = v.strip().lower()
            return stripped or None
        return v


class TenantUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    is_active: bool | None = None


class TenantOut(ORMModel):
    id: str
    name: str
    slug: str
    is_active: bool
    contact_email: str | None = None
    created_by: str | None = None
    created_at: datetime
    # Seat counts, filled in by the route rather than read off the ORM object.
    # Deliberately not named `users`: that is the name of Tenant's relationship,
    # and from_attributes would try to lazy-load it on an async session.
    user_count: int = 0
    active_user_count: int = 0
    admin_count: int = 0


class TenantListResponse(BaseModel):
    items: list[TenantOut]
    total: int


# ---------------------------------------------------------------------------
# users (tenant admin for its own company; platform admin for a first admin)
# ---------------------------------------------------------------------------
class UserInviteRequest(EmailNormalizingModel):
    email: Email
    display_name: str | None = Field(default=None, max_length=200)
    role: TenantAssignableRole = "user"
    department: Department | None = None


class TenantAdminInviteRequest(EmailNormalizingModel):
    """A company's first administrator. The role is implied, never supplied."""

    email: Email
    display_name: str | None = Field(default=None, max_length=200)
    department: Department | None = None


class UserUpdateRequest(BaseModel):
    role: TenantAssignableRole | None = None
    department: Department | None = None
    is_active: bool | None = None


class UserOut(ORMModel):
    id: str
    email: str
    display_name: str | None
    role: str
    department: Department | None
    is_active: bool
    tenant_id: str
    invited_by: str | None = None
    last_login_at: datetime | None
    created_at: datetime


class UserListResponse(BaseModel):
    items: list[UserOut]
    total: int


class InviteResponse(BaseModel):
    """The result of an invitation.

    There is no password field, and there never will be: the invitee receives a
    one-time password from Cognito directly and replaces it on first sign-in.
    """

    user: UserOut
    invitation_sent: bool
    message: str


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


# ---------------------------------------------------------------------------
# agentic workflows
# ---------------------------------------------------------------------------
class WorkflowInfo(BaseModel):
    id: str
    name: str
    description: str


class AgentRequest(BaseModel):
    # No tenant_id here either - tenant comes from the JWT.
    workflow: WorkflowType
    question: str = Field(min_length=1, max_length=4000)
    department: Department | None = None
    document_ids: list[str] | None = Field(default=None, max_length=20)


class AgentStepOut(BaseModel):
    node: str
    model_config = ConfigDict(extra="allow")


class AgentResponseOut(BaseModel):
    """Same envelope as chat, plus the workflow trace."""

    answer: str
    citations: list[CitationOut] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunkOut] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    workflow: str
    model_used: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    latency_ms: int
    tenant_id: str
    confidence: float
    partial: bool = False
    error: str | None = None
    correlation_id: str
