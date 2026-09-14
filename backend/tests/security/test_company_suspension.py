"""A suspended company is blocked server-side; platform operators are not.

Runs with no database or Redis: the session is a stub that reports the
company's is_active flag, and rate limiting is a no-op.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.context import PLATFORM_TENANT_ID
from app.db.session import get_session
from app.main import app


def _token(*, tenant_id: str, role: str, user_id: str = "user-1") -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": user_id,
            "custom:tenant_id": tenant_id,
            "custom:role": role,
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        },
        settings.dev_auth_secret.get_secret_value(),
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


USER = _token(tenant_id="acme", role="user")
PLATFORM = _token(tenant_id=PLATFORM_TENANT_ID, role="platform_admin", user_id="op-1")


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _TenantSession:
    """Answers the suspension lookup with a fixed is_active value."""

    def __init__(self, is_active: bool | None) -> None:
        self.is_active = is_active
        self.lookups = 0

    async def execute(self, _statement: Any) -> _Result:
        self.lookups += 1
        return _Result(self.is_active)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def _no_limit(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _no_documents(*_args: Any, **_kwargs: Any) -> tuple[list[Any], int]:
        return [], 0

    monkeypatch.setattr("app.core.ratelimit.enforce", _no_limit)
    monkeypatch.setattr("app.db.repositories.list_documents", _no_documents)

    def _build(is_active: bool | None) -> tuple[TestClient, _TenantSession]:
        session = _TenantSession(is_active)

        async def _override() -> Any:
            yield session

        app.dependency_overrides[get_session] = _override
        return TestClient(app, raise_server_exceptions=False), session

    yield _build
    app.dependency_overrides.pop(get_session, None)


def test_active_company_is_allowed(make_client: Any) -> None:
    client, _ = make_client(True)
    assert client.get("/api/v1/documents", headers=USER).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/v1/documents", {}),
        ("post", "/api/v1/documents", {"files": {"file": ("a.txt", b"hello", "text/plain")}}),
        ("delete", "/api/v1/documents/doc-1", {}),
        ("post", "/api/v1/chat", {"json": {"question": "What is the leave policy?"}}),
    ],
)
def test_suspended_company_is_403(make_client: Any, method: str, path: str, kwargs: Any) -> None:
    client, _ = make_client(False)
    response = getattr(client, method)(path, headers=USER, **kwargs)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "company_suspended"


def test_platform_admin_is_not_blocked(make_client: Any) -> None:
    client, session = make_client(False)
    assert client.get("/api/v1/documents", headers=PLATFORM).status_code == 200
    assert session.lookups == 0
