"""Monthly LLM token quota for the tenant's active plan.

The limit is the plan entitlement ``monthly_llm_tokens``. It is not the
provider's own allowance. Redis rate limits stay a separate check.

A reservation is committed before the model call, under a PostgreSQL
``SELECT ... FOR UPDATE`` row lock, so a second request sees it. A failed
or cancelled call releases that reservation. The committed balance moves
only after the model returns real token counts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext, get_correlation_id
from app.core.exceptions import TokenQuotaError
from app.core.logging import get_logger
from app.db.models import LlmUsageLedger, LlmUsagePeriod
from app.db.session import get_sessionmaker
from app.services.ai.provider import ChatResult, estimate_tokens, get_provider
from app.services.billing import entitlement_limit

logger = get_logger(__name__)

ENTITLEMENT_KEY = "monthly_llm_tokens"


def quota_applies() -> bool:
    """Local stub chat is not metered. Groq and Bedrock chat are."""
    return settings.ai_provider != "local" and settings.llm_provider != "local"


def current_period() -> tuple[datetime, datetime]:
    """UTC calendar month. Yearly plans still reset this allowance monthly."""
    today = datetime.now(UTC).date()
    start = today.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return (
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, tzinfo=UTC),
    )


def reservation_allowed(committed: int, reserved: int, estimate: int, limit: int) -> bool:
    return estimate >= 0 and committed + reserved + estimate <= limit


def apply_release(reserved: int, estimate: int) -> int:
    return max(0, reserved - estimate)


def apply_reconcile(committed: int, reserved: int, estimate: int, actual: int) -> tuple[int, int]:
    return committed + max(0, actual), apply_release(reserved, estimate)


def estimate_chat_tokens(system: str, messages: list[dict[str, Any]], max_tokens: int) -> int:
    parts = [system]
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(str(block))
        else:
            parts.append(str(content))
    return estimate_tokens("".join(parts)) + max(0, max_tokens)


@dataclass(slots=True)
class Reservation:
    active: bool
    ledger_id: str = ""
    period_id: str = ""
    estimated: int = 0

    @classmethod
    def inactive(cls) -> Reservation:
        return cls(active=False)


def _schema_missing(exc: Exception) -> bool:
    if isinstance(exc, ProgrammingError):
        return True
    text = str(exc).lower()
    return "llm_usage" in text and "does not exist" in text


async def _lock_period(session: AsyncSession, ctx: RequestContext) -> LlmUsagePeriod | None:
    start, end = current_period()
    period_id = f"{ctx.tenant_id}:{start.date().isoformat()}"
    await session.execute(
        pg_insert(LlmUsagePeriod)
        .values(
            id=period_id,
            tenant_id=ctx.tenant_id,
            period_start=start.date(),
            period_end=end.date(),
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            request_count=0,
            reserved_tokens=0,
        )
        .on_conflict_do_nothing(constraint="uq_llm_period_tenant")
    )
    return (
        await session.execute(
            select(LlmUsagePeriod)
            .where(
                LlmUsagePeriod.tenant_id == ctx.tenant_id,
                LlmUsagePeriod.period_start == start.date(),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _reserve(ctx: RequestContext, estimated: int, model: str) -> Reservation:
    if not quota_applies() or ctx.is_platform_admin:
        return Reservation.inactive()
    try:
        async with get_sessionmaker()() as session:
            limit = await entitlement_limit(session, ctx, ENTITLEMENT_KEY)
            if limit is None:
                return Reservation.inactive()
            row = await _lock_period(session, ctx)
            if row is None:
                return Reservation.inactive()
            used = row.total_tokens
            inflight = row.reserved_tokens
            if not reservation_allowed(used, inflight, estimated, limit):
                remaining = max(0, limit - used - inflight)
                raise TokenQuotaError(used=used, limit=limit, remaining=remaining)
            row.reserved_tokens = inflight + estimated
            start, _end = current_period()
            ledger = LlmUsageLedger(
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                period_id=row.id,
                period_start=start.date(),
                correlation_id=get_correlation_id() or ctx.correlation_id or None,
                model=model,
                reserved_tokens=estimated,
                status="reserved",
            )
            session.add(ledger)
            await session.flush()
            reservation = Reservation(
                active=True,
                ledger_id=ledger.id,
                period_id=row.id,
                estimated=estimated,
            )
            await session.commit()
            return reservation
    except TokenQuotaError:
        raise
    except Exception as exc:  # noqa: BLE001
        if _schema_missing(exc):
            logger.warning(
                "token_quota_unavailable",
                extra={"extra": {"error": type(exc).__name__}},
            )
            return Reservation.inactive()
        raise


async def _release(reservation: Reservation) -> None:
    if not reservation.active:
        return
    async with get_sessionmaker()() as session:
        ledger = await session.get(LlmUsageLedger, reservation.ledger_id)
        if ledger is None or ledger.status != "reserved":
            return
        row = (
            await session.execute(
                select(LlmUsagePeriod)
                .where(LlmUsagePeriod.id == reservation.period_id)
                .with_for_update()
            )
        ).scalar_one()
        row.reserved_tokens = apply_release(row.reserved_tokens, reservation.estimated)
        ledger.status = "released"
        ledger.reserved_tokens = 0
        ledger.request_count = 0
        await session.commit()


async def _reconcile(reservation: Reservation, result: ChatResult) -> None:
    if not reservation.active:
        return
    actual = max(0, result.input_tokens) + max(0, result.output_tokens)
    async with get_sessionmaker()() as session:
        ledger = await session.get(LlmUsageLedger, reservation.ledger_id)
        if ledger is None or ledger.status != "reserved":
            return
        row = (
            await session.execute(
                select(LlmUsagePeriod)
                .where(LlmUsagePeriod.id == reservation.period_id)
                .with_for_update()
            )
        ).scalar_one()
        row.reserved_tokens = apply_release(row.reserved_tokens, reservation.estimated)
        row.input_tokens += max(0, result.input_tokens)
        row.output_tokens += max(0, result.output_tokens)
        row.total_tokens, _reserved = apply_reconcile(
            row.total_tokens, reservation.estimated, reservation.estimated, actual
        )
        row.request_count += 1
        ledger.input_tokens = max(0, result.input_tokens)
        ledger.output_tokens = max(0, result.output_tokens)
        ledger.total_tokens = actual
        ledger.request_count = 1
        ledger.reserved_tokens = 0
        ledger.status = "reconciled"
        ledger.model = result.model_used or ledger.model
        await session.commit()


async def _release_safely(reservation: Reservation) -> None:
    if not reservation.active:
        return
    task = asyncio.create_task(_release(reservation))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    except Exception:  # noqa: BLE001
        logger.exception("token_quota_release_failed")


async def gated_chat(
    ctx: RequestContext,
    *,
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    provider: Any | None = None,
) -> ChatResult:
    """Call the configured chat model only when the tenant still has tokens."""

    async def call() -> ChatResult:
        backend = provider if provider is not None else get_provider()
        return await backend.chat(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    if not quota_applies() or ctx.is_platform_admin:
        return await call()
    estimated = estimate_chat_tokens(system, messages, max_tokens)
    reservation = await _reserve(ctx, estimated, settings.effective_chat_model_id)
    try:
        result = await call()
    except BaseException:
        await _release_safely(reservation)
        raise
    try:
        await _reconcile(reservation, result)
    except Exception:  # noqa: BLE001
        # The model already answered. Leave the reservation so the tokens
        # stay unavailable rather than disappearing.
        logger.exception("token_quota_reconcile_failed")
    return result


async def usage_snapshot(session: AsyncSession, ctx: RequestContext) -> dict[str, Any]:
    """Used / limit / remaining for the current month. Limit comes from the plan."""
    start, end = current_period()
    limit = await entitlement_limit(session, ctx, ENTITLEMENT_KEY)
    used = 0
    inflight = 0
    try:
        row = (
            await session.execute(
                select(LlmUsagePeriod).where(
                    LlmUsagePeriod.tenant_id == ctx.tenant_id,
                    LlmUsagePeriod.period_start == start.date(),
                )
            )
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        if not _schema_missing(exc):
            raise
        row = None
    if isinstance(row, LlmUsagePeriod):
        used = row.total_tokens
        inflight = row.reserved_tokens
    remaining = None if limit is None else max(0, limit - used - inflight)
    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "period_start": start.date().isoformat(),
        "period_end": end.date().isoformat(),
    }
