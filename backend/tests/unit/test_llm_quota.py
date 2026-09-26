"""Token-quota arithmetic and the guarantee that an exhausted plan never calls the model."""

from __future__ import annotations

import pytest

from app.core.context import RequestContext
from app.core.exceptions import TokenQuotaError
from app.services import llm_quota


def test_parallel_reservations_cannot_oversubscribe() -> None:
    committed, reserved, limit = 0, 0, 100
    first = 60
    assert llm_quota.reservation_allowed(committed, reserved, first, limit)
    reserved += first
    assert not llm_quota.reservation_allowed(committed, reserved, 60, limit)
    assert llm_quota.reservation_allowed(committed, reserved, 40, limit)


def test_release_returns_the_reserved_tokens() -> None:
    reserved = llm_quota.apply_release(60, 60)
    assert reserved == 0
    assert llm_quota.reservation_allowed(0, reserved, 60, 100)


def test_reconcile_counts_actual_tokens_not_the_estimate() -> None:
    committed, reserved = llm_quota.apply_reconcile(10, 80, 80, 25)
    assert committed == 35
    assert reserved == 0


def test_reconcile_does_not_go_negative_when_the_estimate_was_already_released() -> None:
    _committed, reserved = llm_quota.apply_reconcile(0, 0, 40, 10)
    assert reserved == 0


def test_quota_error_uses_the_upgrade_message() -> None:
    err = TokenQuotaError(used=50000, limit=50000, remaining=0)
    assert err.status_code == 429
    assert err.code == "token_quota_exceeded"
    assert err.message == (
        "Your monthly AI usage limit has been reached. "
        "Please upgrade your plan to continue using AI services."
    )
    assert err.details == {"used": 50000, "limit": 50000, "remaining": 0}


@pytest.mark.asyncio
async def test_exhausted_quota_does_not_call_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_quota.settings, "ai_provider", "hybrid")
    monkeypatch.setattr(llm_quota.settings, "llm_provider", "groq")

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise TokenQuotaError(used=50000, limit=50000, remaining=0)

    def explode() -> None:
        raise AssertionError("the model was called")

    monkeypatch.setattr(llm_quota, "_reserve", refuse)
    monkeypatch.setattr(llm_quota, "get_provider", explode)

    ctx = RequestContext(user_id="u", tenant_id="acme", role="user", email="u@example.com")
    with pytest.raises(TokenQuotaError):
        await llm_quota.gated_chat(
            ctx,
            system="You are a helper.",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=32,
            temperature=0.0,
        )


@pytest.mark.asyncio
async def test_a_failed_call_releases_the_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_quota.settings, "ai_provider", "hybrid")
    monkeypatch.setattr(llm_quota.settings, "llm_provider", "groq")
    released: list[llm_quota.Reservation] = []

    async def reserve(*_args: object, **_kwargs: object) -> llm_quota.Reservation:
        return llm_quota.Reservation(active=True, ledger_id="led", period_id="per", estimated=40)

    async def fail_chat(**_kwargs: object) -> None:
        raise RuntimeError("timeout")

    async def release(reservation: llm_quota.Reservation) -> None:
        released.append(reservation)

    class Provider:
        chat = staticmethod(fail_chat)

    monkeypatch.setattr(llm_quota, "_reserve", reserve)
    monkeypatch.setattr(llm_quota, "_release", release)
    monkeypatch.setattr(llm_quota, "get_provider", lambda: Provider())

    ctx = RequestContext(user_id="u", tenant_id="acme", role="user")
    with pytest.raises(RuntimeError, match="timeout"):
        await llm_quota.gated_chat(
            ctx,
            system="s",
            messages=[{"role": "user", "content": "q"}],
            max_tokens=8,
            temperature=0.0,
        )
    assert len(released) == 1
    assert released[0].estimated == 40
