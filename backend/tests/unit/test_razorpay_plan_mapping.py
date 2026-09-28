"""The Razorpay test-mode plan mapping in migration 0005.

These assertions are the ones a database cannot make for us here: that the
mapping targets exactly the six plans 0003 seeded, that every amount is the
intended paise value, and that no two plans share a Razorpay plan ID. A wrong
ID here would charge the wrong price in Razorpay, so it is worth pinning.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load(filename: str) -> ModuleType:
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mapping_migration() -> ModuleType:
    return _load("0005_razorpay_test_plans.py")


@pytest.fixture(scope="module")
def seed_migration() -> ModuleType:
    return _load("0003_tenant_subscriptions.py")


def test_revision_chain(mapping_migration: ModuleType) -> None:
    assert mapping_migration.revision == "0005"
    assert mapping_migration.down_revision == "0004"


def test_targets_exactly_the_seeded_plans(
    mapping_migration: ModuleType, seed_migration: ModuleType
) -> None:
    """No new plan, no plan left behind - the same six ids 0003 inserted."""
    seeded = {row[0] for row in seed_migration._PLANS}
    assert set(mapping_migration.TEST_PLAN_MAPPING) == seeded
    assert len(seeded) == 6


def test_demo_amounts_in_paise(mapping_migration: ModuleType) -> None:
    expected_rupees = {
        "basic-monthly": 10,
        "basic-yearly": 50,
        "pro-monthly": 30,
        "pro-yearly": 100,
        "enterprise-monthly": 50,
        "enterprise-yearly": 100,
    }
    actual = {
        plan_id: amount // 100
        for plan_id, (amount, _) in mapping_migration.TEST_PLAN_MAPPING.items()
    }
    assert actual == expected_rupees
    # Whole rupees: a stray paise remainder would mean a typo in the mapping.
    for amount, _ in mapping_migration.TEST_PLAN_MAPPING.values():
        assert amount % 100 == 0


def test_razorpay_ids_are_distinct_and_well_formed(mapping_migration: ModuleType) -> None:
    ids = [razorpay_id for _, razorpay_id in mapping_migration.TEST_PLAN_MAPPING.values()]
    assert len(set(ids)) == len(ids), "two plans share one Razorpay plan ID"
    for razorpay_id in ids:
        assert razorpay_id.startswith("plan_")
        assert len(razorpay_id) > len("plan_")


def test_expected_mapping_pairs(mapping_migration: ModuleType) -> None:
    assert mapping_migration.TEST_PLAN_MAPPING == {
        "basic-monthly": (1_000, "plan_TgcQe73TSRI59D"),
        "basic-yearly": (5_000, "plan_TgcRKpjTnQLTzx"),
        "pro-monthly": (3_000, "plan_TgcS3FpPqs7Mre"),
        "pro-yearly": (10_000, "plan_TgcT0uywkG0rRO"),
        "enterprise-monthly": (5_000, "plan_TgcTbTar7kJqCa"),
        "enterprise-yearly": (10_000, "plan_TgcU0IeDsj1SMx"),
    }


def test_downgrade_restores_the_seeded_catalog(
    mapping_migration: ModuleType, seed_migration: ModuleType
) -> None:
    seeded_amounts = {row[0]: row[4] for row in seed_migration._PLANS}
    assert mapping_migration.PRODUCTION_AMOUNTS_PAISE == seeded_amounts


# What each Razorpay test plan actually charges, read from the Razorpay API:
# base + 18% GST + 2% platform fee, rounded. The quote has to equal this, or the
# customer is shown one number and charged another.
RAZORPAY_CHARGE_PAISE: dict[str, int] = {
    "basic-monthly": 1_204,
    "basic-yearly": 6_018,
    "pro-monthly": 3_611,
    "pro-yearly": 12_036,
    "enterprise-monthly": 6_018,
    "enterprise-yearly": 12_036,
}


@pytest.fixture(scope="module")
def fee_alignment(seed_migration: ModuleType) -> dict[str, int]:
    """Fee rules as the database holds them after 0003 then 0006."""
    fees = dict(seed_migration._FEES)
    fees.update(_load("0006_razorpay_fee_alignment.py").ALIGNED_FEES)
    return fees


def test_quote_matches_what_razorpay_charges(
    mapping_migration: ModuleType, fee_alignment: dict[str, int]
) -> None:
    from app.services import billing

    for plan_id, (base_paise, _) in mapping_migration.TEST_PLAN_MAPPING.items():
        _gst, _displayed, _charged, total = billing.quote_amount(
            base_paise,
            saas_gst_bps=fee_alignment["saas_gst_bps"],
            platform_fee_bps=fee_alignment["domestic_platform_fee_bps"],
            gst_on_fee_bps=fee_alignment["gst_on_platform_fee_bps"],
            customer_pays_gateway_fee=bool(fee_alignment["customer_pays_gateway_fee"]),
        )
        assert total == RAZORPAY_CHARGE_PAISE[plan_id], (
            f"{plan_id}: quote {total} paise but Razorpay charges "
            f"{RAZORPAY_CHARGE_PAISE[plan_id]} paise"
        )


# --- migration 0007: the LIVE-mode plan IDs ----------------------------------


@pytest.fixture(scope="module")
def live_migration() -> ModuleType:
    return _load("0007_razorpay_live_plans.py")


def test_live_revision_chain(live_migration: ModuleType) -> None:
    assert live_migration.revision == "0007"
    assert live_migration.down_revision == "0006"


def test_live_targets_exactly_the_seeded_plans(
    live_migration: ModuleType, seed_migration: ModuleType
) -> None:
    """The same six ids 0003 inserted - no new plan, none left behind."""
    seeded = {row[0] for row in seed_migration._PLANS}
    assert set(live_migration.LIVE_PLAN_IDS) == seeded
    assert set(live_migration.TEST_PLAN_IDS) == seeded


def test_live_ids_are_distinct_and_well_formed(live_migration: ModuleType) -> None:
    ids = list(live_migration.LIVE_PLAN_IDS.values())
    assert len(set(ids)) == len(ids), "two plans share one Razorpay plan ID"
    for razorpay_id in ids:
        assert razorpay_id.startswith("plan_")
        # Razorpay entity ids carry exactly 14 characters after the prefix; a
        # different length means a mis-transcribed ID, which cost a cycle once.
        assert len(razorpay_id) == len("plan_") + 14, razorpay_id


def test_live_ids_verified_against_the_live_account(live_migration: ModuleType) -> None:
    """Pinned from a read of the LIVE Razorpay account with the LIVE key."""
    assert live_migration.LIVE_PLAN_IDS == {
        "basic-monthly": "plan_ThO2saW6lrwf3H",
        "basic-yearly": "plan_ThO69Sw6Ziys5g",
        "pro-monthly": "plan_ThO6gd0L5YJiZ4",
        "pro-yearly": "plan_ThO75Q8o15Q9mh",
        "enterprise-monthly": "plan_ThOgNwQywQRnF4",
        "enterprise-yearly": "plan_ThO7zyfLE0IkIe",
    }


def test_live_switch_does_not_touch_prices(live_migration: ModuleType) -> None:
    """0007 maps IDs only - no amount column appears in its statement."""
    assert "base_amount_paise" not in live_migration._UPDATE.text


def test_downgrade_restores_the_test_mode_links(
    live_migration: ModuleType, mapping_migration: ModuleType
) -> None:
    expected = {plan: rid for plan, (_amount, rid) in mapping_migration.TEST_PLAN_MAPPING.items()}
    assert live_migration.TEST_PLAN_IDS == expected


def test_live_plan_amounts_still_match_the_quote(
    mapping_migration: ModuleType, fee_alignment: dict[str, int]
) -> None:
    """The LIVE plans were created at these amounts, so quote == charge holds."""
    from app.services import billing

    for plan_id, (base_paise, _) in mapping_migration.TEST_PLAN_MAPPING.items():
        *_, total = billing.quote_amount(
            base_paise,
            saas_gst_bps=fee_alignment["saas_gst_bps"],
            platform_fee_bps=fee_alignment["domestic_platform_fee_bps"],
            gst_on_fee_bps=fee_alignment["gst_on_platform_fee_bps"],
            customer_pays_gateway_fee=bool(fee_alignment["customer_pays_gateway_fee"]),
        )
        assert total == RAZORPAY_CHARGE_PAISE[plan_id]
