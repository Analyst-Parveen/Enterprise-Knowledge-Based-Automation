"""API smoke tests using FastAPI's test client.

These exercise the real request path - middleware, auth, RBAC, error handling -
with the datastores stubbed out. Full-stack integration against real Postgres,
Qdrant and Redis runs from docker compose via scripts/test-e2e.sh.
"""

from __future__ import annotations

import datetime as dt

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def make_token(
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "user-a",
    role: str = "user",
    expired: bool = False,
) -> str:
    now = dt.datetime.now(dt.UTC)
    exp = now - dt.timedelta(hours=1) if expired else now + dt.timedelta(hours=1)
    return jwt.encode(
        {
            "sub": user_id,
            "custom:tenant_id": tenant_id,
            "custom:role": role,
            "email": f"{user_id}@example.com",
            "exp": exp,
        },
        settings.dev_auth_secret.get_secret_value(),
        algorithm="HS256",
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestHealth:
    def test_liveness_is_public(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_root_responds(self, client: TestClient) -> None:
        assert client.get("/").status_code == 200


class TestSecurityHeaders:
    def test_headers_present_on_every_response(self, client: TestClient) -> None:
        headers = client.get("/api/v1/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "Content-Security-Policy" in headers

    def test_correlation_id_returned(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").headers.get("X-Correlation-ID")

    def test_supplied_correlation_id_is_echoed(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"X-Correlation-ID": "abc123def456"})
        assert response.headers["X-Correlation-ID"] == "abc123def456"

    def test_malformed_correlation_id_is_replaced(self, client: TestClient) -> None:
        """Never reflect arbitrary client input back in a header."""
        response = client.get(
            "/api/v1/health", headers={"X-Correlation-ID": "<script>alert(1)</script>"}
        )
        assert "<script>" not in response.headers["X-Correlation-ID"]


class TestAuthentication:
    def test_unauthenticated_rejected(self, client: TestClient) -> None:
        assert client.get("/api/v1/me").status_code == 401

    def test_malformed_token_rejected(self, client: TestClient) -> None:
        assert client.get("/api/v1/me", headers=auth("not.a.token")).status_code == 401

    def test_expired_token_rejected(self, client: TestClient) -> None:
        token = make_token(expired=True)
        assert client.get("/api/v1/me", headers=auth(token)).status_code == 401

    def test_wrong_signature_rejected(self, client: TestClient) -> None:
        forged = jwt.encode(
            {
                "sub": "u",
                "custom:tenant_id": "tenant-b",
                "custom:role": "admin",
                "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
            },
            "the-wrong-secret",
            algorithm="HS256",
        )
        assert client.get("/api/v1/me", headers=auth(forged)).status_code == 401

    def test_valid_token_accepted(self, client: TestClient) -> None:
        response = client.get("/api/v1/me", headers=auth(make_token()))
        assert response.status_code == 200
        body = response.json()
        assert body["tenant_id"] == "tenant-a"
        assert body["role"] == "user"

    def test_token_without_tenant_rejected(self, client: TestClient) -> None:
        token = jwt.encode(
            {"sub": "u1", "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)},
            settings.dev_auth_secret.get_secret_value(),
            algorithm="HS256",
        )
        assert client.get("/api/v1/me", headers=auth(token)).status_code == 401


class TestTenantBinding:
    def test_tenant_comes_from_token_not_body(self, client: TestClient) -> None:
        """The token says tenant-a; the header claims tenant-b. Token wins."""
        response = client.get(
            "/api/v1/me",
            headers={**auth(make_token(tenant_id="tenant-a")), "X-Tenant-Id": "tenant-b"},
        )
        assert response.json()["tenant_id"] == "tenant-a"

    def test_each_token_gets_its_own_tenant(self, client: TestClient) -> None:
        a = client.get("/api/v1/me", headers=auth(make_token(tenant_id="tenant-a")))
        b = client.get(
            "/api/v1/me", headers=auth(make_token(tenant_id="tenant-b", user_id="user-b"))
        )
        assert a.json()["tenant_id"] != b.json()["tenant_id"]


class TestRBAC:
    def test_non_admin_denied_admin_endpoint(self, client: TestClient) -> None:
        response = client.get("/api/v1/admin/metrics", headers=auth(make_token()))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    def test_unauthenticated_admin_endpoint_is_401(self, client: TestClient) -> None:
        assert client.get("/api/v1/admin/metrics").status_code == 401


class TestDependencyOrdering:
    """Auth must resolve BEFORE the database session.

    If the session dependency is declared first, an unauthenticated request
    opens a DB connection before being rejected - wasting connections and
    turning a clean 401 into a 500 when the database is unreachable.
    """

    def test_auth_precedes_session_in_every_authenticated_route(self) -> None:
        import inspect

        from app.api.v1 import admin, chat, documents

        principals = {"CurrentUser", "AdminUser", "RateLimitedUser", "UploadUser", "ServerUser"}

        for module in (admin, chat, documents):
            for name, fn in vars(module).items():
                if not callable(fn) or not hasattr(fn, "__annotations__"):
                    continue
                try:
                    params = list(inspect.signature(fn).parameters.items())
                except (ValueError, TypeError):
                    continue

                order = [
                    "auth"
                    if getattr(p.annotation, "__name__", str(p.annotation)) in principals
                    else "session"
                    for _, p in params
                    if getattr(p.annotation, "__name__", str(p.annotation)) in principals
                    or getattr(p.annotation, "__name__", str(p.annotation)) == "DbSession"
                ]
                if "auth" in order and "session" in order:
                    assert order.index("auth") < order.index("session"), (
                        f"{module.__name__}.{name}: the DB session is resolved before "
                        "authentication - swap the parameters"
                    )


class TestErrorEnvelope:
    def test_errors_carry_correlation_id_and_no_internals(self, client: TestClient) -> None:
        body = client.get("/api/v1/me").json()
        assert "correlation_id" in body
        assert set(body["error"]) == {"code", "message"}
        # No stack traces or internal paths leak to the client.
        assert "Traceback" not in str(body)
        assert "app/" not in str(body)


class TestOpenAPIContract:
    def test_chat_schema_has_no_tenant_id(self, client: TestClient) -> None:
        """A client must have no way to assert a tenant."""
        schema = client.get("/openapi.json").json()
        chat_schema = schema["components"]["schemas"]["ChatRequest"]
        assert "tenant_id" not in chat_schema["properties"]

    def test_chat_response_has_full_envelope(self, client: TestClient) -> None:
        """PROJECT.md section 7 - all 11 fields must be in the contract."""
        schema = client.get("/openapi.json").json()
        props = schema["components"]["schemas"]["ChatResponseOut"]["properties"]
        for field in (
            "answer",
            "citations",
            "retrieved_chunks",
            "model_used",
            "input_tokens",
            "output_tokens",
            "estimated_cost",
            "latency_ms",
            "cache_hit",
            "tenant_id",
            "confidence",
        ):
            assert field in props, f"response envelope is missing {field}"
