"""The HTTP surface of onboarding: who gets through, and what the contract allows.

These run with no database, Redis or Cognito, because every case here is decided
before any datastore is touched - which is the point. Authorization that needed
a database round-trip to say "no" would be authorization in the wrong place.

The full create-a-company-and-its-users flow against real PostgreSQL lives in
tests/e2e/test_onboarding_flow.py, run by scripts/test-e2e.sh.
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
from app.services.identity import IssuedSession, PendingChallenge


def make_token(
    *,
    tenant_id: str = "infinity-assurance",
    user_id: str = "user-1",
    role: str = "user",
) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "custom:tenant_id": tenant_id,
            "custom:role": role,
            "email": f"{user_id}@example.com",
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        },
        settings.dev_auth_secret.get_secret_value(),
        algorithm="HS256",
    )


PLATFORM_TOKEN = make_token(
    tenant_id=PLATFORM_TENANT_ID, user_id="platform-1", role="platform_admin"
)
TENANT_ADMIN_TOKEN = make_token(user_id="infinity-admin", role="admin")
USER_TOKEN = make_token(user_id="infinity-hr", role="user")
OTHER_ADMIN_TOKEN = make_token(tenant_id="prp-service", user_id="prp-admin", role="admin")


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _FakeSession:
    """Enough AsyncSession for audit writes, and nothing more.

    If a handler under test needs more than this, it is reaching the database
    on a path that should have been refused earlier.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def stubbed_client() -> Any:
    """A client whose database session is a stub, for the auth endpoints."""
    session = _FakeSession()

    async def _session_override() -> Any:
        yield session

    app.dependency_overrides[get_session] = _session_override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# who may create a company
# ---------------------------------------------------------------------------
class TestOnlyPlatformAdminCanCreateTenants:
    TENANT_BODY = {"name": "Acme Holdings", "tenant_id": "acme-holdings"}

    def test_unauthenticated_is_401(self, client: TestClient) -> None:
        assert client.post("/api/v1/platform/tenants", json=self.TENANT_BODY).status_code == 401

    def test_plain_user_is_403(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/platform/tenants", json=self.TENANT_BODY, headers=auth(USER_TOKEN)
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    def test_tenant_admin_is_403(self, client: TestClient) -> None:
        """The rule the whole hierarchy exists for."""
        response = client.post(
            "/api/v1/platform/tenants",
            json=self.TENANT_BODY,
            headers=auth(TENANT_ADMIN_TOKEN),
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/platform/tenants"),
            ("get", "/api/v1/platform/tenants/prp-service"),
            ("patch", "/api/v1/platform/tenants/prp-service"),
            ("post", "/api/v1/platform/tenants/prp-service/admins"),
            ("get", "/api/v1/platform/audit"),
        ],
    )
    def test_every_platform_route_refuses_a_tenant_admin(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = getattr(client, method)(
            path, headers=auth(TENANT_ADMIN_TOKEN), **({"json": {}} if method != "get" else {})
        )
        assert response.status_code == 403, f"{method.upper()} {path} let a tenant admin in"

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/platform/tenants"),
            ("post", "/api/v1/platform/tenants"),
            ("get", "/api/v1/platform/audit"),
        ],
    )
    def test_every_platform_route_refuses_a_plain_user(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = getattr(client, method)(
            path, headers=auth(USER_TOKEN), **({"json": {}} if method != "get" else {})
        )
        assert response.status_code == 403


class TestPlatformRoleCannotBeForged:
    def test_platform_role_outside_the_platform_tenant_is_401(self, client: TestClient) -> None:
        """A token claiming the platform role inside a company is not a session."""
        forged = make_token(tenant_id="infinity-assurance", role="platform_admin")
        assert client.get("/api/v1/platform/tenants", headers=auth(forged)).status_code == 401

    def test_tenant_role_inside_the_platform_tenant_is_401(self, client: TestClient) -> None:
        forged = make_token(tenant_id=PLATFORM_TENANT_ID, role="admin")
        assert client.get("/api/v1/me", headers=auth(forged)).status_code == 401


# ---------------------------------------------------------------------------
# who may manage a company's users
# ---------------------------------------------------------------------------
class TestUserManagementGate:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/admin/users"),
            ("post", "/api/v1/admin/users"),
            ("patch", "/api/v1/admin/users/someone"),
            ("post", "/api/v1/admin/users/someone/reset-password"),
        ],
    )
    def test_plain_user_is_refused(self, client: TestClient, method: str, path: str) -> None:
        response = getattr(client, method)(
            path, headers=auth(USER_TOKEN), **({"json": {}} if method != "get" else {})
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/admin/users"),
            ("post", "/api/v1/admin/users"),
        ],
    )
    def test_unauthenticated_is_refused(self, client: TestClient, method: str, path: str) -> None:
        response = getattr(client, method)(path, **({"json": {}} if method != "get" else {}))
        assert response.status_code == 401

    def test_platform_admin_is_kept_out_of_a_companys_user_list(self, client: TestClient) -> None:
        """Onboarding a company is not a key to its user directory."""
        assert client.get("/api/v1/admin/users", headers=auth(PLATFORM_TOKEN)).status_code == 403


class TestRoleEscalationIsUnrequestable:
    """The contract refuses the escalation before a handler ever runs."""

    def test_platform_role_in_an_invite_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/admin/users",
            json={"email": "new@infinity.example", "role": "platform_admin"},
            headers=auth(TENANT_ADMIN_TOKEN),
        )
        assert response.status_code == 422

    def test_platform_role_in_an_update_is_rejected(self, client: TestClient) -> None:
        response = client.patch(
            "/api/v1/admin/users/someone",
            json={"role": "platform_admin"},
            headers=auth(TENANT_ADMIN_TOKEN),
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("role", ["superuser", "owner", "root", "PLATFORM_ADMIN"])
    def test_invented_roles_are_rejected(self, client: TestClient, role: str) -> None:
        response = client.post(
            "/api/v1/admin/users",
            json={"email": "new@infinity.example", "role": role},
            headers=auth(TENANT_ADMIN_TOKEN),
        )
        assert response.status_code == 422


class TestNoTenantIdIsAcceptedFromClients:
    def test_the_invite_contract_has_no_tenant_field(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        properties = schema["components"]["schemas"]["UserInviteRequest"]["properties"]
        assert "tenant_id" not in properties

    def test_the_invite_role_enum_excludes_the_platform_role(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        role = schema["components"]["schemas"]["UserInviteRequest"]["properties"]["role"]
        assert "platform_admin" not in str(role)

    def test_the_tenant_admin_invite_contract_has_no_role_field(self, client: TestClient) -> None:
        """A platform operator issues exactly one kind of identity here."""
        schema = client.get("/openapi.json").json()
        properties = schema["components"]["schemas"]["TenantAdminInviteRequest"]["properties"]
        assert "role" not in properties

    def test_a_tenant_id_header_does_not_move_a_caller(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/platform/tenants",
            headers={**auth(OTHER_ADMIN_TOKEN), "X-Tenant-Id": PLATFORM_TENANT_ID},
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# sign-in
# ---------------------------------------------------------------------------
class _FakeProvider:
    name = "fake"

    def __init__(self, outcome: Any = None, raises: Exception | None = None) -> None:
        self._outcome = outcome
        self._raises = raises
        self.signed_out: list[str] = []

    async def authenticate(self, email: str, password: str) -> Any:
        if self._raises:
            raise self._raises
        return self._outcome

    async def start_password_reset(self, email: str) -> None:
        if self._raises:
            raise self._raises

    async def sign_out(self, subject: str) -> None:
        self.signed_out.append(subject)


@pytest.fixture
def no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis is not running in this suite; the limiter is covered elsewhere."""

    async def _noop(identifier: str, bucket: str = "auth") -> None:
        return None

    monkeypatch.setattr("app.core.ratelimit.enforce_anonymous", _noop)


class TestLogin:
    def test_credentials_are_exchanged_for_a_session(
        self, stubbed_client: TestClient, no_rate_limit: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        issued = IssuedSession(token=USER_TOKEN, expires_in=3600, refresh_token="r1")
        monkeypatch.setattr(
            "app.api.v1.auth.get_identity_provider", lambda _s: _FakeProvider(issued)
        )

        response = stubbed_client.post(
            "/api/v1/auth/login",
            json={"email": "hr@infinity.example", "password": "CorrectHorse!2026"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token"] == USER_TOKEN
        # The principal is read back out of the issued token, not the request.
        assert body["user"]["tenant_id"] == "infinity-assurance"
        assert body["user"]["role"] == "user"

    def test_a_first_sign_in_returns_a_challenge_and_no_token(
        self, stubbed_client: TestClient, no_rate_limit: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pending = PendingChallenge(challenge="NEW_PASSWORD_REQUIRED", session="opaque")
        monkeypatch.setattr(
            "app.api.v1.auth.get_identity_provider", lambda _s: _FakeProvider(pending)
        )
        body = stubbed_client.post(
            "/api/v1/auth/login",
            json={"email": "new@infinity.example", "password": "TempPass!2026x"},
        ).json()
        assert body["challenge"] == "NEW_PASSWORD_REQUIRED"
        assert body["token"] is None

    def test_the_password_never_comes_back(
        self, stubbed_client: TestClient, no_rate_limit: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        password = "CorrectHorse!2026"
        issued = IssuedSession(token=USER_TOKEN, expires_in=3600)
        monkeypatch.setattr(
            "app.api.v1.auth.get_identity_provider", lambda _s: _FakeProvider(issued)
        )
        response = stubbed_client.post(
            "/api/v1/auth/login", json={"email": "hr@infinity.example", "password": password}
        )
        assert password not in response.text

    @pytest.mark.parametrize(
        "body",
        [
            {"email": "not-an-email", "password": "CorrectHorse!2026"},
            {"email": "hr@infinity.example", "password": "short"},
            {"email": "hr@infinity.example"},
            {"password": "CorrectHorse!2026"},
        ],
    )
    def test_malformed_credentials_are_422(
        self, stubbed_client: TestClient, no_rate_limit: None, body: dict[str, str]
    ) -> None:
        assert stubbed_client.post("/api/v1/auth/login", json=body).status_code == 422

    def test_login_accepts_no_tenant_or_role(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        properties = schema["components"]["schemas"]["LoginRequest"]["properties"]
        assert set(properties) == {"email", "password"}


class TestPasswordResetDoesNotEnumerate:
    def test_an_unknown_address_still_returns_202(
        self, stubbed_client: TestClient, no_rate_limit: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.api.v1.auth.get_identity_provider", lambda _s: _FakeProvider())
        response = stubbed_client.post(
            "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
        )
        assert response.status_code == 202
        assert "exist" in response.json()["message"].lower()


class TestLogout:
    def test_logout_requires_a_session(self, client: TestClient) -> None:
        assert client.post("/api/v1/auth/logout").status_code == 401

    def test_logout_revokes_tokens_server_side(
        self, stubbed_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clearing browser storage alone leaves a stolen token usable."""
        provider = _FakeProvider()
        monkeypatch.setattr("app.api.v1.auth.get_identity_provider", lambda _s: provider)

        response = stubbed_client.post("/api/v1/auth/logout", headers=auth(USER_TOKEN))
        assert response.status_code == 200
        assert provider.signed_out == ["infinity-hr"]


# ---------------------------------------------------------------------------
# nothing above broke anything below
# ---------------------------------------------------------------------------
class TestExistingContractsUnchanged:
    def test_chat_still_refuses_a_client_supplied_tenant(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "tenant_id" not in schema["components"]["schemas"]["ChatRequest"]["properties"]

    def test_me_still_reports_the_token_tenant(self, client: TestClient) -> None:
        response = client.get("/api/v1/me", headers=auth(USER_TOKEN))
        assert response.status_code == 200
        assert response.json()["tenant_id"] == "infinity-assurance"

    def test_admin_metrics_still_refuses_a_plain_user(self, client: TestClient) -> None:
        assert client.get("/api/v1/admin/metrics", headers=auth(USER_TOKEN)).status_code == 403

    def test_health_is_still_public(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").status_code == 200
