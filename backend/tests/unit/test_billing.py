"""Subscription rules, price math, and webhook idempotency. No network."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.context import PLATFORM_TENANT_ID, RequestContext
from app.core.exceptions import SubscriptionRequiredError
from app.db.models import (
    BillingWebhookEvent,
    PaymentTransaction,
    SubscriptionStatus,
    TenantSubscription,
)
from app.services import billing


def test_signature_accepts_the_raw_body() -> None:
    body = b'{"event":"subscription.activated"}'
    secret = "whsec_test"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert billing.verify_webhook_signature(body, digest, secret)
    assert not billing.verify_webhook_signature(body, "nope", secret)
    assert not billing.verify_webhook_signature(body, digest, "")


def test_gateway_fee_is_not_added_when_razorpay_deducts_it() -> None:
    gst, displayed, charged, total = billing.quote_amount(
        99900,
        saas_gst_bps=1800,
        platform_fee_bps=200,
        gst_on_fee_bps=1800,
        customer_pays_gateway_fee=False,
    )
    assert gst == 17982
    # Fees round to the nearest paise (2357.64 + 424.38), matching how Razorpay
    # rounds the plan amount it charges.
    assert displayed == 2782
    assert charged == 0
    assert total == 99900 + gst


def test_gateway_fee_is_added_only_when_the_customer_pays_it() -> None:
    gst, displayed, charged, total = billing.quote_amount(
        99900,
        saas_gst_bps=1800,
        platform_fee_bps=200,
        gst_on_fee_bps=1800,
        customer_pays_gateway_fee=True,
    )
    assert charged == displayed
    assert total == 99900 + gst + displayed


def test_subscription_allows_active_and_rejects_expired() -> None:
    active = TenantSubscription(
        tenant_id="acme",
        plan_id="basic-monthly",
        status=SubscriptionStatus.ACTIVE,
        complimentary=True,
    )
    assert billing.subscription_allows(active)
    assert not billing.subscription_allows(None)
    active.current_period_end = datetime.now(UTC) - timedelta(days=1)
    assert not billing.subscription_allows(active)
    active.current_period_end = None
    active.status = SubscriptionStatus.CANCELLED
    assert not billing.subscription_allows(active)


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _Session:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls = 0

    async def execute(self, _statement: Any) -> _Result:
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return _Result(self.value)


def _user() -> RequestContext:
    return RequestContext(user_id="u", tenant_id="acme", role="user", email="u@example.com")


@pytest.mark.asyncio
async def test_platform_operator_skips_the_subscription_lookup() -> None:
    session = _Session(None)
    ctx = RequestContext(
        user_id="op", tenant_id=PLATFORM_TENANT_ID, role="platform_admin", email="op@example.com"
    )
    await billing.require_active_subscription(session, ctx)
    assert session.calls == 0


@pytest.mark.asyncio
async def test_stub_bool_does_not_deny_existing_tests() -> None:
    await billing.require_active_subscription(_Session(True), _user())


@pytest.mark.asyncio
async def test_missing_row_denies_and_a_database_error_does_not() -> None:
    with pytest.raises(SubscriptionRequiredError):
        await billing.require_active_subscription(_Session(None), _user())
    await billing.require_active_subscription(_Session(RuntimeError("no table")), _user())


class _Store:
    def __init__(self, sub: TenantSubscription) -> None:
        self.sub = sub
        self.events: dict[str, BillingWebhookEvent] = {}
        self.payments: dict[str, PaymentTransaction] = {}

    def add(self, obj: Any) -> None:
        if isinstance(obj, BillingWebhookEvent):
            self.events[obj.razorpay_event_id] = obj
        elif isinstance(obj, PaymentTransaction):
            self.payments[obj.razorpay_payment_id or ""] = obj

    async def flush(self) -> None:
        return None

    async def execute(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params
        if entity is BillingWebhookEvent:
            event_id = _bound(params, "razorpay_event_id")
            return _Result(self.events.get(event_id))
        if entity is PaymentTransaction:
            payment_id = _bound(params, "razorpay_payment_id")
            return _Result(self.payments.get(payment_id))
        if entity is TenantSubscription:
            razorpay_id = _bound(params, "razorpay_subscription_id")
            if razorpay_id and self.sub.razorpay_subscription_id == razorpay_id:
                return _Result(self.sub)
            tenant_id = _bound(params, "tenant_id")
            if tenant_id and self.sub.tenant_id == tenant_id:
                return _Result(self.sub)
        return _Result(None)


def _bound(params: dict[str, Any], name: str) -> Any:
    for key, value in params.items():
        if name in key:
            return value
    return None


def _event() -> dict[str, Any]:
    return {
        "event": "subscription.activated",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_rzp",
                    "status": "active",
                    "notes": {"tenant_id": "acme", "plan_id": "pro-monthly"},
                    "current_end": 1893456000,
                }
            },
            "payment": {
                "entity": {"id": "pay_1", "status": "captured", "amount": 117882, "method": "card"}
            },
        },
    }


@pytest.mark.asyncio
async def test_webhook_is_idempotent_and_activates_the_paid_plan() -> None:
    sub = TenantSubscription(
        id="sub-1",
        tenant_id="acme",
        plan_id="basic-monthly",
        status=SubscriptionStatus.ACTIVE,
        complimentary=True,
        razorpay_subscription_id="sub_rzp",
    )
    store = _Store(sub)
    assert await billing.apply_webhook(store, "evt_1", _event()) is True
    assert sub.plan_id == "pro-monthly"
    assert sub.complimentary is False
    assert sub.status == SubscriptionStatus.ACTIVE
    assert len(store.payments) == 1
    assert await billing.apply_webhook(store, "evt_1", _event()) is False
    assert len(store.payments) == 1


@pytest.mark.asyncio
async def test_pending_webhook_keeps_a_complimentary_subscription() -> None:
    sub = TenantSubscription(
        tenant_id="acme",
        plan_id="basic-monthly",
        status=SubscriptionStatus.ACTIVE,
        complimentary=True,
        razorpay_subscription_id="sub_rzp",
    )
    event = _event()
    event["payload"]["subscription"]["entity"]["status"] = "pending"
    event["payload"].pop("payment")
    store = _Store(sub)
    assert await billing.apply_webhook(store, "evt_pending", event) is True
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.complimentary is True
    assert sub.plan_id == "basic-monthly"
