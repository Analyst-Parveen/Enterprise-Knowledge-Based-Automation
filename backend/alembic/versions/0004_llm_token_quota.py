"""Monthly LLM token quota: period balance, per-call ledger, plan limits.

Limits live in plan_entitlements (monthly_llm_tokens). They are this
application's allowance, not the model provider's.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    op.create_table(
        "llm_usage_periods",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        _ts(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "period_start", name="uq_llm_period_tenant"),
    )
    op.create_index(
        "ix_llm_period_tenant",
        "llm_usage_periods",
        ["tenant_id", "period_start"],
    )

    op.create_table(
        "llm_usage_ledger",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column(
            "period_id",
            sa.String(64),
            sa.ForeignKey("llm_usage_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        _ts(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'reconciled', 'released')",
            name="ck_llm_ledger_status",
        ),
    )
    op.create_index("ix_llm_ledger_tenant_period", "llm_usage_ledger", ["tenant_id", "period_start"])
    op.create_index("ix_llm_ledger_user", "llm_usage_ledger", ["user_id"])
    op.create_index("ix_llm_ledger_correlation", "llm_usage_ledger", ["correlation_id"])

    # Application quotas, per month, in tokens. Not the provider free tier.
    # basic 50_000, pro 500_000, enterprise 2_000_000.
    op.execute(
        sa.text(
            """
            INSERT INTO plan_entitlements
                (id, plan_id, entitlement_key, entitlement_value, created_at, updated_at)
            SELECT id || ':' || 'monthly_llm_tokens', id, 'monthly_llm_tokens',
                CASE code
                    WHEN 'basic' THEN 50000
                    WHEN 'pro' THEN 500000
                    ELSE 2000000
                END,
                now(), now()
            FROM subscription_plans
            ON CONFLICT ON CONSTRAINT uq_plan_entitlement DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM plan_entitlements WHERE entitlement_key = 'monthly_llm_tokens'")
    )
    op.drop_index("ix_llm_ledger_correlation", table_name="llm_usage_ledger")
    op.drop_index("ix_llm_ledger_user", table_name="llm_usage_ledger")
    op.drop_index("ix_llm_ledger_tenant_period", table_name="llm_usage_ledger")
    op.drop_table("llm_usage_ledger")
    op.drop_index("ix_llm_period_tenant", table_name="llm_usage_periods")
    op.drop_table("llm_usage_periods")
