"""Tenant billing. Checkout is company-admin only. Access checks stay on product routes."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession, _require_active_tenant
from app.core import ratelimit
from app.core.auth import require_tenant_admin
from app.core.config import settings
from app.core.context import RequestContext
from app.core.exceptions import AuthenticationError, ValidationError
from app.db.session import get_session
from app.services import billing
from app.services.llm_quota import usage_snapshot

router = APIRouter(prefix="/billing", tags=["billing"])


class PlanOut(BaseModel):
    id: str
    code: str
    name: str
    interval: str
    currency: str
    base_amount_paise: int
    # Plan limits straight from plan_entitlements, so the UI never hardcodes them.
    entitlements: dict[str, int] = Field(default_factory=dict)


class QuoteOut(BaseModel):
    plan_id: str
    currency: str
    base_paise: int
    gst_paise: int
    gateway_fee_paise: int
    gateway_fee_charged_paise: int
    total_paise: int
    customer_pays_gateway_fee: bool


class CheckoutIn(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)


class CheckoutOut(BaseModel):
    key_id: str
    subscription_id: str
    quote: QuoteOut


class SubscriptionOut(BaseModel):
    status: str
    plan_id: str | None
    complimentary: bool
    current_period_end: str | None
    payments_configured: bool


class PaymentOut(BaseModel):
    id: str
    status: str
    total_paise: int
    method: str | None
    created_at: str


class UsageOut(BaseModel):
    used: int
    limit: int | None
    remaining: int | None
    period_start: str
    period_end: str


class BillingProfileIn(BaseModel):
    legal_name: str | None = None
    gstin: str | None = Field(default=None, max_length=15)
    state: str | None = None


async def billing_user(ctx: CurrentUser, session: DbSession) -> RequestContext:
    await ratelimit.enforce(ctx, "api")
    await _require_active_tenant(ctx, session)
    return ctx


BillingUser = Annotated[RequestContext, Depends(billing_user)]


@router.get("/usage", response_model=UsageOut)
async def usage(ctx: BillingUser, session: DbSession) -> UsageOut:
    return UsageOut(**await usage_snapshot(session, ctx))


@router.get("/plans", response_model=list[PlanOut])
async def plans(ctx: BillingUser, session: DbSession) -> list[PlanOut]:
    del ctx
    rows = await billing.list_plans(session)
    limits = await billing.plan_entitlements(session, [row.id for row in rows])
    return [
        PlanOut(
            id=row.id,
            code=row.code,
            name=row.name,
            interval=row.interval.value,
            currency=row.currency,
            base_amount_paise=row.base_amount_paise,
            entitlements=limits.get(row.id, {}),
        )
        for row in rows
    ]


@router.get("/subscription", response_model=SubscriptionOut)
async def subscription(ctx: BillingUser, session: DbSession) -> SubscriptionOut:
    sub = await billing.load_subscription(session, ctx.tenant_id)
    if not isinstance(sub, billing.TenantSubscription):
        return SubscriptionOut(
            status="none",
            plan_id=None,
            complimentary=False,
            current_period_end=None,
            payments_configured=billing.razorpay_configured(),
        )
    end = sub.current_period_end.isoformat() if sub.current_period_end else None
    return SubscriptionOut(
        status=sub.status.value,
        plan_id=sub.plan_id,
        complimentary=sub.complimentary,
        current_period_end=end,
        payments_configured=billing.razorpay_configured(),
    )


@router.post("/quote", response_model=QuoteOut)
async def quote(payload: CheckoutIn, ctx: BillingUser, session: DbSession) -> QuoteOut:
    require_tenant_admin(ctx)
    priced = await billing.build_quote(session, payload.plan_id)
    return QuoteOut(**priced.as_dict())


@router.post("/checkout", response_model=CheckoutOut)
async def checkout(payload: CheckoutIn, ctx: BillingUser, session: DbSession) -> CheckoutOut:
    result = await billing.start_checkout(session, ctx, payload.plan_id)
    await session.commit()
    return CheckoutOut(**result)


@router.post("/cancel")
async def cancel(ctx: BillingUser, session: DbSession) -> dict[str, str]:
    await billing.schedule_cancel(session, ctx)
    await session.commit()
    return {"status": "cancel_scheduled"}


@router.get("/payments", response_model=list[PaymentOut])
async def payments(ctx: BillingUser, session: DbSession) -> list[PaymentOut]:
    rows = await billing.list_payments(session, ctx.tenant_id)
    return [
        PaymentOut(
            id=row.id,
            status=row.status,
            total_paise=row.total_paise,
            method=row.method,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]


@router.put("/profile")
async def update_profile(
    payload: BillingProfileIn, ctx: BillingUser, session: DbSession
) -> dict[str, str]:
    require_tenant_admin(ctx)
    await billing.upsert_billing_profile(
        session,
        ctx.tenant_id,
        legal_name=payload.legal_name,
        gstin=payload.gstin,
        state=payload.state,
    )
    await session.commit()
    return {"status": "saved"}


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    secret = (
        settings.razorpay_webhook_secret.get_secret_value()
        if settings.razorpay_webhook_secret
        else ""
    )
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    if not billing.verify_webhook_signature(body, signature, secret):
        raise AuthenticationError("Invalid webhook signature.")
    try:
        event = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValidationError("Webhook body is not JSON.") from exc
    if not isinstance(event, dict):
        raise ValidationError("Webhook body is not JSON.")
    event_id = request.headers.get("x-razorpay-event-id", "").strip()
    if not event_id:
        event_id = hashlib.sha256(body).hexdigest()
    applied = await billing.apply_webhook(session, event_id, event)
    await session.commit()
    return {"status": "ok" if applied else "duplicate"}
