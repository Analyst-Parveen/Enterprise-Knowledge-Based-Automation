"""Tenant subscriptions, plans, payments, and fee rules.

Additive only. Existing tenants except the platform tenant receive a
complimentary active Basic subscription so current workspaces keep working.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLATFORM_TENANT_ID = "platform"

# Catalog prices are ours (paise). Gateway fees live in fee_rules, not here.
_PLANS = [
    ("basic-monthly", "basic", "Basic", "MONTHLY", 99900, 10),
    ("basic-yearly", "basic", "Basic", "YEARLY", 999000, 11),
    ("pro-monthly", "pro", "Pro", "MONTHLY", 299900, 20),
    ("pro-yearly", "pro", "Pro", "YEARLY", 2999000, 21),
    ("enterprise-monthly", "enterprise", "Enterprise", "MONTHLY", 999900, 30),
    ("enterprise-yearly", "enterprise", "Enterprise", "YEARLY", 9999000, 31),
]

# Requests per minute / concurrent jobs. Read by the app; not hardcoded in handlers.
_ENTITLEMENTS = {
    "basic": {"tenant_api_per_min": 100, "tenant_upload_per_min": 20, "max_concurrent_jobs": 1},
    "pro": {"tenant_api_per_min": 300, "tenant_upload_per_min": 60, "max_concurrent_jobs": 2},
    "enterprise": {
        "tenant_api_per_min": 1000,
        "tenant_upload_per_min": 200,
        "max_concurrent_jobs": 4,
    },
}

# Public Razorpay domestic card is 2% + 18% GST on that fee, deducted at settlement.
# SaaS GST 18% is the usual Indian rate and is configurable here, not in Python.
_FEES = {
    "saas_gst_bps": 1800,
    "domestic_platform_fee_bps": 200,
    "gst_on_platform_fee_bps": 1800,
    "customer_pays_gateway_fee": 0,
}


def upgrade() -> None:
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column(
            "interval",
            sa.Enum("MONTHLY", "YEARLY", name="billing_interval"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("base_amount_paise", sa.Integer(), nullable=False),
        sa.Column("razorpay_plan_id", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_subscription_plans_code", "subscription_plans", ["code"])

    op.create_table(
        "plan_entitlements",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("subscription_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entitlement_key", sa.String(80), nullable=False),
        sa.Column("entitlement_value", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("plan_id", "entitlement_key", name="uq_plan_entitlement"),
    )

    op.create_table(
        "tenant_subscriptions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("subscription_plans.id"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "PAST_DUE",
                "CANCELLED",
                "EXPIRED",
                "HALTED",
                "INCOMPLETE",
                name="subscription_status",
            ),
            nullable=False,
        ),
        sa.Column("complimentary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("razorpay_subscription_id", sa.String(64), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_subscription"),
        sa.UniqueConstraint("razorpay_subscription_id", name="uq_razorpay_subscription"),
    )
    op.create_index("ix_tenant_subscriptions_tenant_id", "tenant_subscriptions", ["tenant_id"])

    op.create_table(
        "tenant_billing_profiles",
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("state", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.String(64),
            sa.ForeignKey("tenant_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("razorpay_payment_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("base_paise", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gst_paise", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gateway_fee_paise", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_paise", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("method", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("razorpay_payment_id", name="uq_razorpay_payment"),
    )
    op.create_index(
        "ix_payments_tenant_created", "payment_transactions", ["tenant_id", "created_at"]
    )

    op.create_table(
        "billing_webhook_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("razorpay_event_id", sa.String(80), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("razorpay_event_id", name="uq_razorpay_event"),
    )

    op.create_table(
        "fee_rules",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("value_int", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    conn = op.get_bind()
    for plan_id, code, name, interval, amount, sort_order in _PLANS:
        conn.execute(
            sa.text(
                """
                INSERT INTO subscription_plans
                    (id, code, name, interval, currency, base_amount_paise, is_active, sort_order,
                     created_at, updated_at)
                VALUES
                    (:id, :code, :name, CAST(:interval AS billing_interval), 'INR', :amount,
                     true, :sort_order, now(), now())
                """
            ).bindparams(
                id=plan_id,
                code=code,
                name=name,
                interval=interval,
                amount=amount,
                sort_order=sort_order,
            )
        )
        for key, value in _ENTITLEMENTS[code].items():
            conn.execute(
                sa.text(
                    """
                    INSERT INTO plan_entitlements
                        (id, plan_id, entitlement_key, entitlement_value, created_at, updated_at)
                    VALUES (:id, :plan_id, :key, :value, now(), now())
                    """
                ).bindparams(
                    id=f"{plan_id}:{key}",
                    plan_id=plan_id,
                    key=key,
                    value=value,
                )
            )

    for code, value in _FEES.items():
        conn.execute(
            sa.text(
                """
                INSERT INTO fee_rules (code, value_int, created_at, updated_at)
                VALUES (:code, :value, now(), now())
                """
            ).bindparams(code=code, value=value)
        )

    conn.execute(
        sa.text(
            """
            INSERT INTO tenant_subscriptions
                (id, tenant_id, plan_id, status, complimentary, cancel_at_period_end,
                 created_at, updated_at)
            SELECT 'comp-' || id, id, 'basic-monthly', 'ACTIVE', true, false, now(), now()
            FROM tenants
            WHERE id <> :platform
            """
        ).bindparams(platform=PLATFORM_TENANT_ID)
    )


def downgrade() -> None:
    op.drop_table("fee_rules")
    op.drop_table("billing_webhook_events")
    op.drop_index("ix_payments_tenant_created", table_name="payment_transactions")
    op.drop_table("payment_transactions")
    op.drop_table("tenant_billing_profiles")
    op.drop_index("ix_tenant_subscriptions_tenant_id", table_name="tenant_subscriptions")
    op.drop_table("tenant_subscriptions")
    op.drop_table("plan_entitlements")
    op.drop_index("ix_subscription_plans_code", table_name="subscription_plans")
    op.drop_table("subscription_plans")
    op.execute("DROP TYPE IF EXISTS subscription_status")
    op.execute("DROP TYPE IF EXISTS billing_interval")
