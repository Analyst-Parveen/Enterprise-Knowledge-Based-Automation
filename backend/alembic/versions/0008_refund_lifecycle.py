"""Refund lifecycle: activation snapshot, per-refund rows, cumulative totals.

Additive only. No existing row is read, rewritten or deleted, so subscriptions
and payments recorded before this migration keep exactly the values they have.

Three things are added:

* ``tenant_subscriptions`` remembers the plan, status and complimentary flag it
  held immediately before a given Razorpay subscription activated it. Without
  that, a refund can only guess at "cancelled" or "free", which is wrong for a
  tenant that was on a paid plan - or on a complimentary one - beforehand.
  It is stamped once per Razorpay subscription and never on a renewal.

* ``payment_transactions`` records which Razorpay subscription a payment paid
  for, whether it was that subscription's activating payment, and how much of
  it has been refunded so far.

* ``payment_refunds`` holds one row per Razorpay refund. Keying on the refund id
  is what makes cumulative totals exact: a redelivered or reordered event can
  never add the same refund twice, and two partial refunds that together cover
  the payment are recognised as a full refund.

Rows that predate this migration have no activation snapshot, so a refund of
those payments records the money and deliberately changes no subscription
state - see the report in docs/reports for the manual reconciliation path.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The state to restore to, stamped at activation.
    op.add_column(
        "tenant_subscriptions",
        sa.Column("activation_razorpay_subscription_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "tenant_subscriptions", sa.Column("activation_prev_plan_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "tenant_subscriptions", sa.Column("activation_prev_status", sa.String(32), nullable=True)
    )
    op.add_column(
        "tenant_subscriptions",
        sa.Column("activation_prev_complimentary", sa.Boolean(), nullable=True),
    )

    # Which subscription a payment paid for, and how much came back.
    op.add_column(
        "payment_transactions",
        sa.Column("razorpay_subscription_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_payments_razorpay_subscription",
        "payment_transactions",
        ["razorpay_subscription_id"],
    )
    op.add_column(
        "payment_transactions",
        sa.Column("is_activation", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "payment_transactions",
        sa.Column("refunded_paise", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("payment_transactions", sa.Column("refund_status", sa.String(32), nullable=True))

    op.create_table(
        "payment_refunds",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            sa.String(64),
            sa.ForeignKey("payment_transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("razorpay_refund_id", sa.String(64), nullable=False, unique=True),
        sa.Column("amount_paise", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_payment_refunds_tenant_id", "payment_refunds", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_refunds_tenant_id", table_name="payment_refunds")
    op.drop_table("payment_refunds")
    op.drop_column("payment_transactions", "refund_status")
    op.drop_column("payment_transactions", "refunded_paise")
    op.drop_column("payment_transactions", "is_activation")
    op.drop_index("ix_payments_razorpay_subscription", table_name="payment_transactions")
    op.drop_column("payment_transactions", "razorpay_subscription_id")
    op.drop_column("tenant_subscriptions", "activation_prev_complimentary")
    op.drop_column("tenant_subscriptions", "activation_prev_status")
    op.drop_column("tenant_subscriptions", "activation_prev_plan_id")
    op.drop_column("tenant_subscriptions", "activation_razorpay_subscription_id")
