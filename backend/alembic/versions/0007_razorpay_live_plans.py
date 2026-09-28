"""Point the six plan rows at the Razorpay LIVE plans.

Only ``razorpay_plan_id`` changes. ``base_amount_paise`` is deliberately left
alone: the LIVE plans were created to charge exactly what the existing pricing
and fee rules already produce (base + 18% GST + 2% platform fee, rounded), so
the quote still equals the amount Razorpay charges, to the paise:

    basic-monthly       INR 10   -> 1204    basic-yearly       INR 50   -> 6018
    pro-monthly         INR 30   -> 3611    pro-yearly         INR 100  -> 12036
    enterprise-monthly  INR 50   -> 6018    enterprise-yearly  INR 100  -> 12036

Every ID below was read back from the LIVE Razorpay account with the LIVE key
and checked for name, currency, amount, period, interval and active state.

All six rows move together on purpose. The account's keys are LIVE, and a row
still holding a test-mode plan id would fail at checkout, so a partial switch
would leave the catalog half broken.

Nothing is inserted or deleted, so there is no second catalog and no duplicate
plan. Entitlements, quotas, fee rules, tenant subscriptions and payments are
untouched. Re-running is a no-op: every statement is an UPDATE keyed by plan id.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# plan id -> Razorpay LIVE plan id, verified against the LIVE account.
LIVE_PLAN_IDS: dict[str, str] = {
    "basic-monthly": "plan_ThO2saW6lrwf3H",
    "basic-yearly": "plan_ThO69Sw6Ziys5g",
    "pro-monthly": "plan_ThO6gd0L5YJiZ4",
    "pro-yearly": "plan_ThO75Q8o15Q9mh",
    "enterprise-monthly": "plan_ThOgNwQywQRnF4",
    "enterprise-yearly": "plan_ThO7zyfLE0IkIe",
}

# What 0005 set, so a downgrade restores the test-mode links exactly rather than
# clearing the column and leaving the catalog unpayable.
TEST_PLAN_IDS: dict[str, str] = {
    "basic-monthly": "plan_TgcQe73TSRI59D",
    "basic-yearly": "plan_TgcRKpjTnQLTzx",
    "pro-monthly": "plan_TgcS3FpPqs7Mre",
    "pro-yearly": "plan_TgcT0uywkG0rRO",
    "enterprise-monthly": "plan_TgcTbTar7kJqCa",
    "enterprise-yearly": "plan_TgcU0IeDsj1SMx",
}

_UPDATE = sa.text(
    """
    UPDATE subscription_plans
       SET razorpay_plan_id = :razorpay_plan_id,
           updated_at       = now()
     WHERE id = :plan_id
    """
)


def _apply(mapping: dict[str, str]) -> None:
    conn = op.get_bind()
    for plan_id, razorpay_plan_id in mapping.items():
        conn.execute(_UPDATE.bindparams(razorpay_plan_id=razorpay_plan_id, plan_id=plan_id))


def upgrade() -> None:
    _apply(LIVE_PLAN_IDS)


def downgrade() -> None:
    _apply(TEST_PLAN_IDS)
