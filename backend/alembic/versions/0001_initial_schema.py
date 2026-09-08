"""Initial schema - the 10 entities from PROJECT.md section 14.

Every tenant-scoped table carries a non-nullable, indexed tenant_id.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    department = sa.Enum(
        "HR", "FINANCE", "LEGAL", "SALES", "MARKETING", "OPERATIONS", "TECHNICAL",
        name="department",
    )
    user_role = sa.Enum("USER", "ADMIN", name="user_role")
    document_status = sa.Enum(
        "PENDING", "PROCESSING", "READY", "FAILED", "DELETED", name="document_status"
    )
    modality = sa.Enum("TEXT", "TABLE", "IMAGE", "AUDIO", "VIDEO", name="modality")
    job_status = sa.Enum(
        "QUEUED", "EXTRACTING", "TRANSCRIBING", "CHUNKING", "EMBEDDING",
        "COMPLETED", "FAILED", name="job_status",
    )

    # -- tenants ---------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"])
    op.create_index("ix_tenants_created_at", "tenants", ["created_at"])

    # -- users -----------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("cognito_sub", sa.String(128), nullable=True, unique=True),
        sa.Column(
            "tenant_id", sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("role", user_role, nullable=False, server_default="USER"),
        sa.Column("department", department, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )
    op.create_index("ix_users_tenant", "users", ["tenant_id"])
    op.create_index("ix_users_cognito_sub", "users", ["cognito_sub"])
    op.create_index("ix_users_created_at", "users", ["created_at"])

    # -- documents -------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(200), nullable=False),
        sa.Column("modality", modality, nullable=False),
        sa.Column("department", department, nullable=True),
        sa.Column("source_uri", sa.String(1000), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", document_status, nullable=False, server_default="PENDING"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "doc_metadata", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_index("ix_documents_checksum", "documents", ["checksum_sha256"])
    op.create_index("ix_documents_created_at", "documents", ["created_at"])
    op.create_index("ix_documents_tenant_status", "documents", ["tenant_id", "status"])
    op.create_index(
        "ix_documents_tenant_department", "documents", ["tenant_id", "department"]
    )

    # -- ingestion_jobs --------------------------------------------------
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "document_id", sa.String(64),
            sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("status", job_status, nullable=False, server_default="QUEUED"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_tenant_id", "ingestion_jobs", ["tenant_id"])
    op.create_index("ix_jobs_document_id", "ingestion_jobs", ["document_id"])
    op.create_index("ix_jobs_correlation_id", "ingestion_jobs", ["correlation_id"])
    op.create_index("ix_jobs_created_at", "ingestion_jobs", ["created_at"])
    op.create_index("ix_jobs_tenant_status", "ingestion_jobs", ["tenant_id", "status"])

    # -- conversations ---------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("department", department, nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_index("ix_conversations_created_at", "conversations", ["created_at"])
    op.create_index(
        "ix_conversations_tenant_user", "conversations", ["tenant_id", "user_id"]
    )

    # -- messages --------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "conversation_id", sa.String(64),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "retrieved_chunks", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column("model_used", sa.String(200), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])
    op.create_index("ix_messages_correlation_id", "messages", ["correlation_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index("ix_messages_tenant_conv", "messages", ["tenant_id", "conversation_id"])

    # -- request_usage ---------------------------------------------------
    op.create_table(
        "request_usage",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("model_used", sa.String(200), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_tenant_id", "request_usage", ["tenant_id"])
    op.create_index("ix_usage_user_id", "request_usage", ["user_id"])
    op.create_index("ix_usage_correlation_id", "request_usage", ["correlation_id"])
    op.create_index("ix_usage_created_at", "request_usage", ["created_at"])
    op.create_index("ix_usage_tenant_created", "request_usage", ["tenant_id", "created_at"])

    # -- user_feedback ---------------------------------------------------
    op.create_table(
        "user_feedback",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(100), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_feedback_tenant_id", "user_feedback", ["tenant_id"])
    op.create_index("ix_feedback_message_id", "user_feedback", ["message_id"])
    op.create_index("ix_feedback_created_at", "user_feedback", ["created_at"])
    op.create_index("ix_feedback_tenant", "user_feedback", ["tenant_id"])

    # -- prompt_releases -------------------------------------------------
    op.create_table(
        "prompt_releases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("released_by", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_prompt_name_version"),
    )
    op.create_index("ix_prompts_name", "prompt_releases", ["name"])
    op.create_index("ix_prompts_created_at", "prompt_releases", ["created_at"])

    # -- audit_events ----------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("resource_type", sa.String(80), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_tenant_id", "audit_events", ["tenant_id"])
    op.create_index("ix_audit_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_correlation_id", "audit_events", ["correlation_id"])
    op.create_index("ix_audit_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_tenant_created", "audit_events", ["tenant_id", "created_at"])
    op.create_index("ix_audit_type", "audit_events", ["event_type"])


def downgrade() -> None:
    for table in (
        "audit_events", "prompt_releases", "user_feedback", "request_usage",
        "messages", "conversations", "ingestion_jobs", "documents", "users", "tenants",
    ):
        op.drop_table(table)

    for enum_name in (
        "job_status", "modality", "document_status", "user_role", "department"
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
