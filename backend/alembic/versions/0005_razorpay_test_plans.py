"""Razorpay test-mode plan IDs and temporary demo prices.

Updates the six plan rows that 0003 seeded, in place. Nothing is inserted or
deleted, so there is no second catalog and no duplicate plan: entitlements,
tenant subscriptions, payments and fee rules are untouched.

Re-running is a no-op - every statement is an UPDATE keyed by the plan id - so
this is safe on a database that already holds these values.

Prices are deliberately low while the product is being demonstrated. They live
in the database, so real pricing later is an UPDATE (or another migration like
this one), never an application change.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# plan id -> (amount in paise, Razorpay TEST-mode plan id).
# Paise, so 1_000 = INR 10.00. The Razorpay plan must carry the amount Razorpay
# should charge; this column is what the app prices and displays.
TEST_PLAN_MAPPING: dict[str, tuple[int, str]] = {
    "basic-monthly": (1_000, "plan_TgcQe73TSRI59D"),
    "basic-yearly": (5_000, "plan_TgcRKpjTnQLTzx"),
    "pro-monthly": (3_000, "plan_TgcS3FpPqs7Mre"),
    "pro-yearly": (10_000, "plan_TgcT0uywkG0rRO"),
    "enterprise-monthly": (5_000, "plan_TgcTbTar7kJqCa"),
    "enterprise-yearly": (10_000, "plan_TgcU0IeDsj1SMx"),
}

# What 0003 seeded, so a downgrade restores exactly the previous catalog and
# clears the test-mode links rather than leaving test IDs behind.
PRODUCTION_AMOUNTS_PAISE: dict[str, int] = {
    "basic-monthly": 99_900,
    "basic-yearly": 999_000,
    "pro-monthly": 299_900,
    "pro-yearly": 2_999_000,
    "enterprise-monthly": 999_900,
    "enterprise-yearly": 9_999_000,
}

_UPDATE = sa.text(
    """
    UPDATE subscription_plans
       SET base_amount_paise = :amount,
           razorpay_plan_id  = :razorpay_plan_id,
           updated_at        = now()
     WHERE id = :plan_id
    """
)


def upgrade() -> None:
    conn = op.get_bind()
    for plan_id, (amount_paise, razorpay_plan_id) in TEST_PLAN_MAPPING.items():
        conn.execute(
            _UPDATE.bindparams(
                amount=amount_paise,
                razorpay_plan_id=razorpay_plan_id,
                plan_id=plan_id,
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    for plan_id, amount_paise in PRODUCTION_AMOUNTS_PAISE.items():
        conn.execute(
            _UPDATE.bindparams(
                amount=amount_paise,
                razorpay_plan_id=None,
                plan_id=plan_id,
            )
        )
