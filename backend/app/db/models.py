"""SQLAlchemy models.

Every table holding tenant data has a non-nullable, indexed tenant_id.
See .claude/rules/tenant-isolation.md section 3.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------
class Department(str, enum.Enum):
    HR = "hr"
    FINANCE = "finance"
    LEGAL = "legal"
    SALES = "sales"
    MARKETING = "marketing"
    OPERATIONS = "operations"
    TECHNICAL = "technical"


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"
    # Service-provider operator. Lives only in the reserved platform tenant and
    # is the only role permitted to create a tenant. See core/context.py.
    PLATFORM_ADMIN = "platform_admin"


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class Modality(str, enum.Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# tenants & users
# ---------------------------------------------------------------------------
class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Primary contact for the company, captured at onboarding. Not a credential.
    contact_email: Mapped[str | None] = mapped_column(String(320))
    # The platform operator who onboarded this company.
    created_by: Mapped[str | None] = mapped_column(String(64))

    users: Mapped[list[User]] = relationship(back_populates="tenant")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_tenant", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # Cognito 'sub'. Unique globally - one identity belongs to one tenant.
    cognito_sub: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.USER, nullable=False
    )
    department: Mapped[Department | None] = mapped_column(Enum(Department, name="department"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who issued the invitation. A platform operator for a first tenant admin,
    # a tenant admin for everybody else.
    invited_by: Mapped[str | None] = mapped_column(String(64))

    tenant: Mapped[Tenant] = relationship(back_populates="users")


# ---------------------------------------------------------------------------
# documents & ingestion
# ---------------------------------------------------------------------------
class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_tenant_status", "tenant_id", "status"),
        Index("ix_documents_tenant_department", "tenant_id", "department"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    modality: Mapped[Modality] = mapped_column(Enum(Modality, name="modality"), nullable=False)
    department: Mapped[Department | None] = mapped_column(Enum(Department, name="department"))

    # S3 key is generated server-side as <tenant_id>/<uuid>, never from filename.
    source_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), index=True)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        default=DocumentStatus.PENDING,
        nullable=False,
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    doc_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionJob(Base, TimestampMixin):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (Index("ix_jobs_tenant_status", "tenant_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.QUEUED, nullable=False
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunks_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------
class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_tenant_user", "tenant_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    department: Mapped[Department | None] = mapped_column(Enum(Department, name="department"))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_tenant_conv", "tenant_id", "conversation_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Response envelope fields (PROJECT.md section 7)
    citations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    retrieved_chunks: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float | None] = mapped_column(Float)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# usage, feedback, prompts, audit
# ---------------------------------------------------------------------------
class RequestUsage(Base, TimestampMixin):
    __tablename__ = "request_usage"
    __table_args__ = (Index("ix_usage_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)

    operation: Mapped[str] = mapped_column(String(80), nullable=False)  # chat | embed | vision ...
    model_used: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserFeedback(Base, TimestampMixin):
    __tablename__ = "user_feedback"
    __table_args__ = (Index("ix_feedback_tenant", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(64), index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # -1 | 1
    reason: Mapped[str | None] = mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(Text)


class PromptRelease(Base, TimestampMixin):
    __tablename__ = "prompt_releases"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_prompt_name_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    released_by: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base, TimestampMixin):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_type", "event_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # Nullable: some security events (failed auth) have no established tenant.
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="info", nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    # Reason codes only - never the offending payload.
    reason: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# billing — subscription belongs to the tenant, not a user
# ---------------------------------------------------------------------------
class BillingInterval(str, enum.Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    HALTED = "halted"
    INCOMPLETE = "incomplete"


class SubscriptionPlan(Base, TimestampMixin):
    __tablename__ = "subscription_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    interval: Mapped[BillingInterval] = mapped_column(
        Enum(BillingInterval, name="billing_interval"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    base_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    razorpay_plan_id: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    entitlements: Mapped[list[PlanEntitlement]] = relationship(back_populates="plan")


class PlanEntitlement(Base, TimestampMixin):
    __tablename__ = "plan_entitlements"
    __table_args__ = (UniqueConstraint("plan_id", "entitlement_key", name="uq_plan_entitlement"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("subscription_plans.id", ondelete="CASCADE"), nullable=False
    )
    entitlement_key: Mapped[str] = mapped_column(String(80), nullable=False)
    entitlement_value: Mapped[int] = mapped_column(Integer, nullable=False)

    plan: Mapped[SubscriptionPlan] = relationship(back_populates="entitlements")


class TenantSubscription(Base, TimestampMixin):
    __tablename__ = "tenant_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_subscription"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("subscription_plans.id"), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        default=SubscriptionStatus.INCOMPLETE,
        nullable=False,
    )
    complimentary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    razorpay_subscription_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    plan: Mapped[SubscriptionPlan] = relationship()


class TenantBillingProfile(Base, TimestampMixin):
    __tablename__ = "tenant_billing_profiles"

    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    legal_name: Mapped[str | None] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15))
    state: Mapped[str | None] = mapped_column(String(80))


class PaymentTransaction(Base, TimestampMixin):
    __tablename__ = "payment_transactions"
    __table_args__ = (Index("ix_payments_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("tenant_subscriptions.id", ondelete="SET NULL")
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    base_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gst_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gateway_fee_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    method: Mapped[str | None] = mapped_column(String(40))


class BillingWebhookEvent(Base, TimestampMixin):
    __tablename__ = "billing_webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    razorpay_event_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FeeRule(Base, TimestampMixin):
    __tablename__ = "fee_rules"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_int: Mapped[int] = mapped_column(Integer, nullable=False)


class LlmUsagePeriod(Base, TimestampMixin):
    """One locked row per tenant per calendar month. Holds the quota balance."""

    __tablename__ = "llm_usage_periods"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period_start", name="uq_llm_period_tenant"),
        Index("ix_llm_period_tenant", "tenant_id", "period_start"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class LlmUsageLedger(Base, TimestampMixin):
    """One row per LLM call. user_id is the token subject, same as request_usage."""

    __tablename__ = "llm_usage_ledger"
    __table_args__ = (
        Index("ix_llm_ledger_tenant_period", "tenant_id", "period_start"),
        Index("ix_llm_ledger_user", "user_id"),
        Index("ix_llm_ledger_correlation", "correlation_id"),
        CheckConstraint(
            "status IN ('reserved', 'reconciled', 'released')",
            name="ck_llm_ledger_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    period_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("llm_usage_periods.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
