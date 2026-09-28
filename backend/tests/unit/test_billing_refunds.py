"""The Razorpay refund lifecycle.

A refund moves money, so the rules it has to obey are worth pinning precisely:
a full refund of the payment that activated the current subscription restores
exactly the state that payment replaced, and every other refund - partial,
failed, duplicated, for a renewal, for an older subscription, for another
tenant, or for a payment this system never recorded - leaves the subscription
alone.

No network, no database: the fake session below is enough to exercise the
branching, and it keeps these tests honest about which rows the code touches.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.db.models import (
    BillingWebhookEvent,
    PaymentRefund,
    PaymentTransaction,
    SubscriptionStatus,
    TenantSubscription,
)
from app.services import billing

TENANT = "acme"
SUB_A = "sub_rzp_A"
SUB_B = "sub_rzp_B"


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _Store:
    """Just enough session to record what the handler writes."""

    def __init__(self, sub: TenantSubscription | None) -> None:
        self.sub = sub
        self.events: dict[str, BillingWebhookEvent] = {}
        self.payments: list[PaymentTransaction] = []
        self.refunds: list[PaymentRefund] = []

    def add(self, obj: Any) -> None:
        if isinstance(obj, BillingWebhookEvent):
            self.events[obj.razorpay_event_id] = obj
        elif isinstance(obj, PaymentTransaction):
            obj.id = obj.id or uuid.uuid4().hex
            obj.refunded_paise = obj.refunded_paise or 0
            self.payments.append(obj)
        elif isinstance(obj, PaymentRefund):
            obj.id = obj.id or uuid.uuid4().hex
            self.refunds.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params
        if entity is BillingWebhookEvent:
            return _Result(self.events.get(_bound(params, "razorpay_event_id")))
        if entity is PaymentTransaction:
            pay_id = _bound(params, "razorpay_payment_id")
            if pay_id is not None:
                return _Result(
                    next((p for p in self.payments if p.razorpay_payment_id == pay_id), None)
                )
            sub_id = _bound(params, "razorpay_subscription_id")
            if sub_id is not None:
                return _Result(
                    next((p for p in self.payments if p.razorpay_subscription_id == sub_id), None)
                )
            return _Result(None)
        if entity is PaymentRefund:
            refund_id = _bound(params, "razorpay_refund_id")
            return _Result(
                next((r for r in self.refunds if r.razorpay_refund_id == refund_id), None)
            )
        if entity is TenantSubscription:
            if self.sub is None:
                return _Result(None)
            razorpay_id = _bound(params, "razorpay_subscription_id")
            if razorpay_id and self.sub.razorpay_subscription_id == razorpay_id:
                return _Result(self.sub)
            tenant_id = _bound(params, "tenant_id")
            if tenant_id and self.sub.tenant_id == tenant_id:
                return _Result(self.sub)
        return _Result(None)


def _bound(params: dict[str, Any], name: str) -> Any:
    for key, value in params.items():
        if key.startswith(name):
            return value
    return None


def _complimentary_sub() -> TenantSubscription:
    """What grant_complimentary() creates for a brand new company."""
    return TenantSubscription(
        id="sub-row-1",
        tenant_id=TENANT,
        plan_id="basic-monthly",
        status=SubscriptionStatus.ACTIVE,
        complimentary=True,
    )


def _activation(
    razorpay_sub: str = SUB_A,
    plan: str = "pro-monthly",
    payment_id: str = "pay_1",
    amount: int = 3611,
) -> dict[str, Any]:
    return {
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": razorpay_sub,
                    "status": "active",
                    "notes": {"tenant_id": TENANT, "plan_id": plan},
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "status": "captured",
                    "amount": amount,
                    "method": "upi",
                }
            },
        },
    }


def _refund(
    event: str = "refund.processed",
    refund_id: str = "rfnd_1",
    payment_id: str = "pay_1",
    amount: int = 3611,
) -> dict[str, Any]:
    return {
        "event": event,
        "payload": {
            "refund": {
                "entity": {"id": refund_id, "payment_id": payment_id, "amount": amount},
            },
            "payment": {"entity": {"id": payment_id, "status": "refunded"}},
        },
    }


async def _activate(store: _Store, event_id: str = "evt_act", **kwargs: Any) -> None:
    """Drive one activation the way production does: checkout stamps the new
    Razorpay subscription id on the row, then the webhook arrives."""
    if store.sub is not None:
        store.sub.razorpay_subscription_id = kwargs.get("razorpay_sub", SUB_A)
    assert await billing.apply_webhook(store, event_id, _activation(**kwargs)) is True


# --- activation: the snapshot the refund path depends on ---------------------


@pytest.mark.asyncio
async def test_activation_records_the_previous_state_and_links_the_payment() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    assert sub.plan_id == "pro-monthly"
    assert sub.complimentary is False
    assert sub.status == SubscriptionStatus.ACTIVE
    # the state it replaced, kept for a refund to restore
    assert sub.activation_razorpay_subscription_id == SUB_A
    assert sub.activation_prev_plan_id == "basic-monthly"
    assert sub.activation_prev_status == "active"
    assert sub.activation_prev_complimentary is True
    # and the payment knows which subscription it bought
    assert len(store.payments) == 1
    assert store.payments[0].razorpay_subscription_id == SUB_A
    assert store.payments[0].is_activation is True


@pytest.mark.asyncio
async def test_duplicate_activation_event_is_ignored_entirely() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)
    assert await billing.apply_webhook(store, "evt_act", _activation()) is False
    assert len(store.payments) == 1
    assert sub.activation_prev_plan_id == "basic-monthly"


@pytest.mark.asyncio
async def test_a_second_activating_event_does_not_overwrite_the_snapshot() -> None:
    """subscription.activated and subscription.charged both activate; the first
    one to arrive owns the snapshot."""
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store, event_id="evt_1")
    await _activate(store, event_id="evt_2")
    assert sub.activation_prev_plan_id == "basic-monthly"
    assert sub.activation_prev_complimentary is True


@pytest.mark.asyncio
async def test_renewal_charge_neither_resnapshots_nor_counts_as_activation() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store, event_id="evt_1", payment_id="pay_1")
    await _activate(store, event_id="evt_2", payment_id="pay_2")  # renewal charge

    assert sub.activation_prev_plan_id == "basic-monthly"
    assert [p.is_activation for p in store.payments] == [True, False]


# --- full refund of the activating payment -----------------------------------


@pytest.mark.asyncio
async def test_full_refund_of_the_activating_payment_restores_the_exact_state() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    assert await billing.apply_webhook(store, "evt_ref", _refund()) is True

    assert sub.plan_id == "basic-monthly"
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.complimentary is True  # the complimentary grant comes back
    assert store.payments[0].refunded_paise == 3611
    assert store.payments[0].refund_status == "processed"
    # the snapshot is spent, so nothing can restore twice
    assert sub.activation_prev_plan_id is None
    assert sub.activation_razorpay_subscription_id is None


@pytest.mark.asyncio
async def test_full_refund_restores_a_paid_previous_plan_not_free() -> None:
    """Basic paid -> Enterprise -> full refund must land back on Basic paid."""
    sub = TenantSubscription(
        id="sub-row-1",
        tenant_id=TENANT,
        plan_id="basic-monthly",
        status=SubscriptionStatus.ACTIVE,
        complimentary=False,
    )
    store = _Store(sub)
    await _activate(store, plan="enterprise-monthly", amount=6018)
    assert sub.plan_id == "enterprise-monthly"

    await billing.apply_webhook(store, "evt_ref", _refund(amount=6018))

    assert sub.plan_id == "basic-monthly"
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.complimentary is False


# --- partial, failed, created, duplicate -------------------------------------


@pytest.mark.asyncio
async def test_partial_refund_records_money_and_leaves_the_plan_alone() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    await billing.apply_webhook(store, "evt_ref", _refund(amount=1000))

    assert store.payments[0].refunded_paise == 1000
    assert sub.plan_id == "pro-monthly"
    assert sub.complimentary is False
    assert sub.activation_prev_plan_id == "basic-monthly"  # still restorable later


@pytest.mark.asyncio
async def test_two_partial_refunds_that_complete_the_payment_restore_once() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    await billing.apply_webhook(store, "evt_r1", _refund(refund_id="rfnd_1", amount=1611))
    assert sub.plan_id == "pro-monthly"

    await billing.apply_webhook(store, "evt_r2", _refund(refund_id="rfnd_2", amount=2000))

    assert store.payments[0].refunded_paise == 3611
    assert sub.plan_id == "basic-monthly"
    assert sub.complimentary is True


@pytest.mark.asyncio
async def test_refund_created_records_state_without_touching_the_plan() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    await billing.apply_webhook(store, "evt_c", _refund(event="refund.created"))

    assert store.payments[0].refunded_paise == 0
    assert store.payments[0].refund_status == "created"
    assert sub.plan_id == "pro-monthly"


@pytest.mark.asyncio
async def test_refund_failed_changes_nothing_about_the_subscription() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    await billing.apply_webhook(store, "evt_f", _refund(event="refund.failed"))

    assert store.payments[0].refunded_paise == 0
    assert store.payments[0].refund_status == "failed"
    assert sub.plan_id == "pro-monthly"


@pytest.mark.asyncio
async def test_created_then_processed_for_one_refund_counts_the_money_once() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    await billing.apply_webhook(store, "evt_c", _refund(event="refund.created"))
    await billing.apply_webhook(store, "evt_p", _refund(event="refund.processed"))

    assert len(store.refunds) == 1
    assert store.payments[0].refunded_paise == 3611
    assert sub.plan_id == "basic-monthly"


@pytest.mark.asyncio
async def test_duplicate_refund_processed_is_idempotent() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    await billing.apply_webhook(store, "evt_r", _refund())
    # same event id: stopped by the webhook-event guard
    assert await billing.apply_webhook(store, "evt_r", _refund()) is False
    # a different event id carrying the same refund: stopped by the refund row
    await billing.apply_webhook(store, "evt_r_again", _refund())

    assert store.payments[0].refunded_paise == 3611
    assert len(store.refunds) == 1
    assert sub.plan_id == "basic-monthly"


@pytest.mark.asyncio
async def test_a_late_created_event_cannot_walk_back_a_processed_refund() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    await billing.apply_webhook(store, "evt_p", _refund(event="refund.processed"))
    await billing.apply_webhook(store, "evt_c", _refund(event="refund.created"))

    assert store.refunds[0].status == "processed"
    assert store.payments[0].refunded_paise == 3611


# --- refunds that must never move the current subscription -------------------


@pytest.mark.asyncio
async def test_refund_for_a_payment_we_never_recorded_is_ignored() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    await billing.apply_webhook(store, "evt_x", _refund(payment_id="pay_unknown"))

    assert store.refunds == []
    assert store.payments[0].refunded_paise == 0
    assert sub.plan_id == "pro-monthly"


@pytest.mark.asyncio
async def test_refund_of_a_renewal_charge_does_not_downgrade_the_plan() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store, event_id="evt_1", payment_id="pay_1")
    await _activate(store, event_id="evt_2", payment_id="pay_2")  # renewal

    await billing.apply_webhook(store, "evt_ref", _refund(payment_id="pay_2"))

    assert store.payments[1].refunded_paise == 3611  # money recorded
    assert sub.plan_id == "pro-monthly"  # plan untouched
    assert sub.complimentary is False
    assert sub.activation_prev_plan_id == "basic-monthly"


@pytest.mark.asyncio
async def test_partial_refund_of_a_renewal_changes_nothing_either() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store, event_id="evt_1", payment_id="pay_1")
    await _activate(store, event_id="evt_2", payment_id="pay_2")

    await billing.apply_webhook(store, "evt_ref", _refund(payment_id="pay_2", amount=500))

    assert store.payments[1].refunded_paise == 500
    assert sub.plan_id == "pro-monthly"


@pytest.mark.asyncio
async def test_refunding_an_older_subscription_leaves_the_newer_one_active() -> None:
    """Basic -> Pro (A) -> Enterprise (B). Refunding A must not touch B."""
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(
        store, event_id="evt_a", razorpay_sub=SUB_A, plan="pro-monthly", payment_id="pay_A"
    )
    await _activate(
        store,
        event_id="evt_b",
        razorpay_sub=SUB_B,
        plan="enterprise-monthly",
        payment_id="pay_B",
        amount=6018,
    )
    assert sub.plan_id == "enterprise-monthly"
    assert sub.activation_prev_plan_id == "pro-monthly"

    await billing.apply_webhook(store, "evt_ref", _refund(payment_id="pay_A"))

    assert store.payments[0].refunded_paise == 3611  # the money is still recorded
    assert sub.plan_id == "enterprise-monthly"  # and nothing rolled back
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.activation_razorpay_subscription_id == SUB_B


@pytest.mark.asyncio
async def test_refund_without_a_snapshot_records_money_but_restores_nothing() -> None:
    """Payments taken before this feature existed have no snapshot."""
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)
    sub.activation_prev_plan_id = None  # as a pre-0008 row would look

    await billing.apply_webhook(store, "evt_ref", _refund())

    assert store.payments[0].refunded_paise == 3611
    assert sub.plan_id == "pro-monthly"


@pytest.mark.asyncio
async def test_refund_cannot_reach_a_subscription_in_another_tenant() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)
    # the refunded payment belongs to a different company
    store.payments[0].tenant_id = "other-co"

    await billing.apply_webhook(store, "evt_ref", _refund())

    assert sub.plan_id == "pro-monthly"
    assert sub.complimentary is False


@pytest.mark.asyncio
async def test_refund_with_no_subscription_row_is_safe() -> None:
    store = _Store(None)
    store.payments.append(
        PaymentTransaction(
            id="p1",
            tenant_id=TENANT,
            razorpay_payment_id="pay_1",
            razorpay_subscription_id=SUB_A,
            is_activation=True,
            status="captured",
            total_paise=3611,
        )
    )
    await billing.apply_webhook(store, "evt_ref", _refund())
    assert store.payments[0].refunded_paise == 3611


@pytest.mark.asyncio
async def test_a_refund_event_outside_the_three_handled_ones_changes_nothing() -> None:
    """refund.speed_changed carries a refund entity but reports how the money is
    travelling, not that it moved."""
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    event = _refund(event="refund.speed_changed")
    event["payload"]["refund"]["entity"]["status"] = "processed"
    await billing.apply_webhook(store, "evt_speed", event)

    assert store.refunds == []
    assert store.payments[0].refunded_paise == 0
    assert store.payments[0].refund_status is None
    assert sub.plan_id == "pro-monthly"


@pytest.mark.asyncio
async def test_refund_missing_ids_is_ignored() -> None:
    sub = _complimentary_sub()
    store = _Store(sub)
    await _activate(store)

    event = _refund()
    event["payload"]["refund"]["entity"].pop("id")
    event["payload"]["payment"]["entity"].pop("id")
    await billing.apply_webhook(store, "evt_bad", event)

    assert store.refunds == []
    assert sub.plan_id == "pro-monthly"


def test_a_refund_event_never_passes_an_unverified_signature() -> None:
    """The refund path sits behind the same signature check as every other event."""
    body = b'{"event":"refund.processed"}'
    secret = "whsec_live"
    import hashlib
    import hmac

    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert billing.verify_webhook_signature(body, good, secret)
    assert not billing.verify_webhook_signature(body, "forged", secret)
    assert not billing.verify_webhook_signature(body, good, "")
