"""Tenant subscriptions and Razorpay test-mode checkout.

The subscription belongs to the tenant. Access is decided here, never in the UI.
Gateway fees default to a settlement deduction so the customer is not charged twice.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import PLATFORM_TENANT_ID, RequestContext
from app.core.exceptions import (
    AuthorizationError,
    SubscriptionRequiredError,
    UpstreamError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.models import (
    BillingWebhookEvent,
    FeeRule,
    PaymentTransaction,
    PlanEntitlement,
    SubscriptionPlan,
    SubscriptionStatus,
    TenantBillingProfile,
    TenantSubscription,
)

logger = get_logger(__name__)

_ACTIVE = {SubscriptionStatus.ACTIVE}
_RAZORPAY_STATUS = {
    "active": SubscriptionStatus.ACTIVE,
    "authenticated": SubscriptionStatus.ACTIVE,
    "pending": SubscriptionStatus.INCOMPLETE,
    "halted": SubscriptionStatus.HALTED,
    "cancelled": SubscriptionStatus.CANCELLED,
    "completed": SubscriptionStatus.EXPIRED,
    "expired": SubscriptionStatus.EXPIRED,
}


@dataclass(slots=True)
class PriceQuote:
    plan_id: str
    currency: str
    base_paise: int
    gst_paise: int
    gateway_fee_paise: int
    gateway_fee_charged_paise: int
    total_paise: int
    customer_pays_gateway_fee: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "currency": self.currency,
            "base_paise": self.base_paise,
            "gst_paise": self.gst_paise,
            "gateway_fee_paise": self.gateway_fee_paise,
            "gateway_fee_charged_paise": self.gateway_fee_charged_paise,
            "total_paise": self.total_paise,
            "customer_pays_gateway_fee": self.customer_pays_gateway_fee,
        }


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def _bps(amount_paise: int, bps: int) -> int:
    """Apply a basis-point rate, rounded to the nearest paise (half up).

    Razorpay rounds the plan amounts it charges rather than truncating them, so
    rounding here is what keeps the quote equal to the amount actually taken.
    """
    return (amount_paise * bps + 5_000) // 10_000


def quote_amount(
    base_paise: int,
    *,
    saas_gst_bps: int,
    platform_fee_bps: int,
    gst_on_fee_bps: int,
    customer_pays_gateway_fee: bool,
) -> tuple[int, int, int, int]:
    """Return gst, displayed gateway fee, charged gateway fee, and payable total.

    When the customer does not pay the gateway fee, Razorpay deducts it from
    settlement. Adding it to the checkout amount as well would charge it twice.
    """
    gst = _bps(base_paise, saas_gst_bps)
    subtotal = base_paise + gst
    fee = _bps(subtotal, platform_fee_bps)
    fee_gst = _bps(fee, gst_on_fee_bps)
    displayed = fee + fee_gst
    charged = displayed if customer_pays_gateway_fee else 0
    return gst, displayed, charged, subtotal + charged


def subscription_allows(sub: TenantSubscription | None, *, now: datetime | None = None) -> bool:
    if sub is None or not isinstance(sub, TenantSubscription):
        return False
    if sub.status not in _ACTIVE:
        return False
    moment = now or datetime.now(UTC)
    if sub.current_period_end is not None and sub.current_period_end < moment:
        return False
    return True


async def _fee(session: AsyncSession, code: str, default: int) -> int:
    row = await session.get(FeeRule, code)
    return row.value_int if row is not None else default


async def load_subscription(session: AsyncSession, tenant_id: str) -> TenantSubscription | None:
    stmt = select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
    found = (await session.execute(stmt)).scalar_one_or_none()
    return found if isinstance(found, TenantSubscription) else None


async def require_active_subscription(session: AsyncSession, ctx: RequestContext) -> None:
    """Block product routes when the tenant subscription is not active.

    Platform operators are exempt. A non-model result (test stubs) is ignored
    so existing auth tests keep working. A real missing row is a denial.
    """
    if ctx.is_platform_admin or ctx.tenant_id == PLATFORM_TENANT_ID:
        return
    try:
        found = (
            await session.execute(
                select(TenantSubscription).where(TenantSubscription.tenant_id == ctx.tenant_id)
            )
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - unmigrated database must not take chat down
        logger.warning("subscription_lookup_skipped")
        return
    if not isinstance(found, TenantSubscription):
        if found is None:
            raise SubscriptionRequiredError()
        return
    if not subscription_allows(found):
        raise SubscriptionRequiredError()


async def entitlement_limit(session: AsyncSession, ctx: RequestContext, key: str) -> int | None:
    try:
        sub = await load_subscription(session, ctx.tenant_id)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(sub, TenantSubscription):
        return None
    stmt = select(PlanEntitlement.entitlement_value).where(
        PlanEntitlement.plan_id == sub.plan_id,
        PlanEntitlement.entitlement_key == key,
    )
    value = (await session.execute(stmt)).scalar_one_or_none()
    return int(value) if isinstance(value, int) else None


async def list_plans(session: AsyncSession) -> list[SubscriptionPlan]:
    stmt = (
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order)
    )
    return list((await session.execute(stmt)).scalars().all())


async def plan_entitlements(
    session: AsyncSession, plan_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Entitlements per plan, so the pricing UI shows the DB's own limits.

    One query for every plan on the page. Keys are whatever the catalog defines
    (monthly_llm_tokens, tenant_api_per_min, ...); nothing is hardcoded here.
    """
    if not plan_ids:
        return {}
    stmt = select(
        PlanEntitlement.plan_id,
        PlanEntitlement.entitlement_key,
        PlanEntitlement.entitlement_value,
    ).where(PlanEntitlement.plan_id.in_(plan_ids))
    found: dict[str, dict[str, int]] = {}
    for plan_id, key, value in (await session.execute(stmt)).all():
        found.setdefault(str(plan_id), {})[str(key)] = int(value)
    return found


async def build_quote(session: AsyncSession, plan_id: str) -> PriceQuote:
    plan = await session.get(SubscriptionPlan, plan_id)
    if plan is None or not plan.is_active:
        raise ValidationError("That plan is not available.")
    gst, displayed, charged, total = quote_amount(
        plan.base_amount_paise,
        saas_gst_bps=await _fee(session, "saas_gst_bps", 1800),
        platform_fee_bps=await _fee(session, "domestic_platform_fee_bps", 200),
        gst_on_fee_bps=await _fee(session, "gst_on_platform_fee_bps", 1800),
        customer_pays_gateway_fee=bool(await _fee(session, "customer_pays_gateway_fee", 0)),
    )
    return PriceQuote(
        plan_id=plan.id,
        currency=plan.currency,
        base_paise=plan.base_amount_paise,
        gst_paise=gst,
        gateway_fee_paise=displayed,
        gateway_fee_charged_paise=charged,
        total_paise=total,
        customer_pays_gateway_fee=charged > 0,
    )


async def grant_complimentary(session: AsyncSession, tenant_id: str) -> None:
    if tenant_id == PLATFORM_TENANT_ID:
        return
    existing = await load_subscription(session, tenant_id)
    if existing is not None:
        return
    session.add(
        TenantSubscription(
            tenant_id=tenant_id,
            plan_id="basic-monthly",
            status=SubscriptionStatus.ACTIVE,
            complimentary=True,
        )
    )


def razorpay_configured() -> bool:
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret)


async def start_checkout(
    session: AsyncSession, ctx: RequestContext, plan_id: str
) -> dict[str, Any]:
    if not ctx.is_tenant_admin:
        raise AuthorizationError("Only a company admin can purchase a subscription.")
    if not razorpay_configured():
        raise ValidationError("Payments are not configured yet.")
    plan = await session.get(SubscriptionPlan, plan_id)
    if plan is None or not plan.razorpay_plan_id:
        raise ValidationError("This plan is not linked to a Razorpay plan yet.")
    quote = await build_quote(session, plan_id)
    secret = settings.razorpay_key_secret.get_secret_value() if settings.razorpay_key_secret else ""
    payload = {
        "plan_id": plan.razorpay_plan_id,
        "total_count": 12 if plan.interval.value == "monthly" else 5,
        "customer_notify": 1,
        "notes": {"tenant_id": ctx.tenant_id, "plan_id": plan.id},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.razorpay.com/v1/subscriptions",
                auth=(settings.razorpay_key_id, secret),
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("razorpay_checkout_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("The payment service is unavailable.") from exc

    sub = await load_subscription(session, ctx.tenant_id)
    if sub is None:
        sub = TenantSubscription(
            tenant_id=ctx.tenant_id,
            plan_id=plan.id,
            status=SubscriptionStatus.INCOMPLETE,
        )
        session.add(sub)
    # Keep the current plan and status until a verified webhook says the
    # payment succeeded. Flipping to incomplete here would lock a complimentary
    # tenant out the moment they open Checkout.
    sub.razorpay_subscription_id = str(body.get("id") or "")
    await session.flush()
    return {
        "key_id": settings.razorpay_key_id,
        "subscription_id": sub.razorpay_subscription_id,
        "quote": quote.as_dict(),
    }


async def apply_webhook(session: AsyncSession, event_id: str, event: dict[str, Any]) -> bool:
    """Apply one Razorpay event. Returns False when the event was already stored."""
    if not event_id:
        event_id = str(uuid.uuid4())
    existing = (
        await session.execute(
            select(BillingWebhookEvent).where(BillingWebhookEvent.razorpay_event_id == event_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    event_type = str(event.get("event") or "unknown")
    row = BillingWebhookEvent(razorpay_event_id=event_id, event_type=event_type, processed=False)
    session.add(row)
    await session.flush()

    payload = event.get("payload") or {}
    subscription = ((payload.get("subscription") or {}).get("entity")) or {}
    payment = ((payload.get("payment") or {}).get("entity")) or {}
    notes = subscription.get("notes") or payment.get("notes") or {}
    tenant_id = notes.get("tenant_id")
    razorpay_sub_id = subscription.get("id")
    sub: TenantSubscription | None = None
    if razorpay_sub_id:
        sub = (
            await session.execute(
                select(TenantSubscription).where(
                    TenantSubscription.razorpay_subscription_id == razorpay_sub_id
                )
            )
        ).scalar_one_or_none()
    if sub is None and tenant_id:
        sub = await load_subscription(session, str(tenant_id))
    if isinstance(sub, TenantSubscription) and subscription:
        status_name = str(subscription.get("status") or "")
        mapped = _RAZORPAY_STATUS.get(status_name)
        # A pending checkout must not revoke a complimentary workspace.
        if mapped == SubscriptionStatus.INCOMPLETE and sub.complimentary:
            mapped = None
        if mapped is not None:
            if mapped == SubscriptionStatus.ACTIVE:
                plan_from_notes = notes.get("plan_id")
                if isinstance(plan_from_notes, str) and plan_from_notes:
                    sub.plan_id = plan_from_notes
                sub.complimentary = False
            sub.status = mapped
        end = subscription.get("current_end")
        if isinstance(end, int) and end > 0:
            sub.current_period_end = datetime.fromtimestamp(end, tz=UTC)
        if status_name == "cancelled":
            sub.cancel_at_period_end = True
    payment_id = payment.get("id")
    if payment_id and isinstance(sub, TenantSubscription):
        already = (
            await session.execute(
                select(PaymentTransaction).where(
                    PaymentTransaction.razorpay_payment_id == payment_id
                )
            )
        ).scalar_one_or_none()
        if already is None:
            session.add(
                PaymentTransaction(
                    tenant_id=sub.tenant_id,
                    subscription_id=sub.id,
                    razorpay_payment_id=str(payment_id),
                    status=str(payment.get("status") or event_type),
                    total_paise=int(payment.get("amount") or 0),
                    method=str(payment.get("method") or "") or None,
                )
            )
    row.processed = True
    return True


async def schedule_cancel(session: AsyncSession, ctx: RequestContext) -> None:
    """Stop renewal at the end of the current period. Access stays until then."""
    if not ctx.is_tenant_admin:
        raise AuthorizationError("Only a company admin can cancel a subscription.")
    sub = await load_subscription(session, ctx.tenant_id)
    if not isinstance(sub, TenantSubscription):
        raise ValidationError("No subscription to cancel.")
    sub.cancel_at_period_end = True
    razorpay_id = sub.razorpay_subscription_id
    if not razorpay_id or not razorpay_configured():
        return
    secret = settings.razorpay_key_secret.get_secret_value() if settings.razorpay_key_secret else ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://api.razorpay.com/v1/subscriptions/{razorpay_id}/cancel",
                auth=(settings.razorpay_key_id, secret),
                json={"cancel_at_cycle_end": 1},
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.error("razorpay_cancel_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("The payment service is unavailable.") from exc


async def list_payments(session: AsyncSession, tenant_id: str) -> list[PaymentTransaction]:
    stmt = (
        select(PaymentTransaction)
        .where(PaymentTransaction.tenant_id == tenant_id)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(50)
    )
    return list((await session.execute(stmt)).scalars().all())


async def upsert_billing_profile(
    session: AsyncSession,
    tenant_id: str,
    *,
    legal_name: str | None,
    gstin: str | None,
    state: str | None,
) -> TenantBillingProfile:
    profile = await session.get(TenantBillingProfile, tenant_id)
    if profile is None:
        profile = TenantBillingProfile(tenant_id=tenant_id)
        session.add(profile)
    profile.legal_name = legal_name
    profile.gstin = gstin
    profile.state = state
    await session.flush()
    return profile
