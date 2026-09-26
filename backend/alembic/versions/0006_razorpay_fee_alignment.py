"""Align the fee rules with how the Razorpay test plans are actually priced.

The Razorpay plans created for these six catalog plans charge
``base + 18% GST + 2% platform fee`` (verified against the Razorpay API), so
the customer does pay the gateway fee and there is no GST charged on that fee.
Two existing fee_rules rows are updated to say so; the quote then equals the
amount Razorpay takes, to the paise:

    basic-monthly       1000 ->  1204        pro-monthly     3000 ->  3611
    basic-yearly        5000 ->  6018        pro-yearly     10000 -> 12036
    enterprise-monthly  5000 ->  6018        enterprise-yearly 10000 -> 12036

No new fee model: saas_gst_bps (1800) and domestic_platform_fee_bps (200) keep
the values 0003 seeded, and both rows here already exist. UPDATE only, so
re-running changes nothing.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# fee_rules.code -> value_int
ALIGNED_FEES: dict[str, int] = {
    # The Razorpay plan amount includes the 2% fee, so the customer pays it.
    "customer_pays_gateway_fee": 1,
    # Those plan amounts carry no GST on the fee itself.
    "gst_on_platform_fee_bps": 0,
}

# What 0003 seeded, for a clean downgrade.
SEEDED_FEES: dict[str, int] = {
    "customer_pays_gateway_fee": 0,
    "gst_on_platform_fee_bps": 1800,
}

_UPDATE = sa.text(
    """
    UPDATE fee_rules
       SET value_int = :value,
           updated_at = now()
     WHERE code = :code
    """
)


def _apply(values: dict[str, int]) -> None:
    conn = op.get_bind()
    for code, value in values.items():
        conn.execute(_UPDATE.bindparams(code=code, value=value))


def upgrade() -> None:
    _apply(ALIGNED_FEES)


def downgrade() -> None:
    _apply(SEEDED_FEES)
