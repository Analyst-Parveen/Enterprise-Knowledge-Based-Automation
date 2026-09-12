"""The onboarding journey end to end, against real PostgreSQL.

    platform operator
        -> creates Infinity Assurance, invites its admin
        -> creates PRP Service, invites its admin
    each company's admin
        -> invites its own HR, Finance and Legal users
        -> cannot see, touch or join the other company
        -> cannot create a company, and cannot mint a platform operator

Run by scripts/test-e2e.sh against the local Docker Compose stack, and skipped
with a clear reason when no database is reachable so it never fails for the
wrong one.

It is one test, not sixteen, on purpose: every step depends on the one before
it, and running the whole journey inside a single event loop is what keeps the
asyncpg pool from being handed between loops. The authorization matrix is
covered case-by-case, without any database, in
tests/integration/test_onboarding_api.py.

The tenant ids are prefixed `e2etest-` and every row created here is removed
afterwards, so this is safe to run against a database that also holds demo data.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import delete, select, text

from app.core.config import settings
from app.core.context import PLATFORM_TENANT_ID
from app.db.models import AuditEvent, Tenant, User, UserRole
from app.db.session import dispose_engine, get_sessionmaker
from app.main import app

INFINITY = "e2etest-infinity-assurance"
INFINITY_NAME = "Infinity Assurance Solutions Private Limited"
PRP = "e2etest-prp-service"
PRP_NAME = "PRP Service Limited"
OPERATOR_ID = "e2etest-platform-operator"
DEPARTMENTS = ("hr", "finance", "legal")

DEV_PASSWORD = settings.dev_auth_password.get_secret_value()


def token_for(*, tenant_id: str, user_id: str, role: str) -> str:
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


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _reachable() -> bool:
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _cleanup() -> None:
    async with get_sessionmaker()() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_([INFINITY, PRP])))
        await session.execute(delete(User).where(User.id == OPERATOR_ID))
        # A tenant's users cascade with it.
        await session.execute(delete(Tenant).where(Tenant.id.in_([INFINITY, PRP])))
        await session.commit()


async def _ensure_operator() -> None:
    """A platform operator must already exist before it can onboard anybody.

    There is deliberately no API that mints the first one; on AWS it is created
    out-of-band by scripts/bootstrap-platform-admin.sh.
    """
    async with get_sessionmaker()() as session:
        if await session.get(Tenant, PLATFORM_TENANT_ID) is None:
            session.add(
                Tenant(
                    id=PLATFORM_TENANT_ID,
                    name="Platform Operations",
                    slug=PLATFORM_TENANT_ID,
                )
            )
            await session.flush()
        if await session.get(User, OPERATOR_ID) is None:
            session.add(
                User(
                    id=OPERATOR_ID,
                    cognito_sub=OPERATOR_ID,
                    tenant_id=PLATFORM_TENANT_ID,
                    email="e2e-operator@ekba.example",
                    display_name="E2E Platform Operator",
                    role=UserRole.PLATFORM_ADMIN,
                )
            )
        await session.commit()


async def _admin_subject(tenant_id: str) -> str:
    """The identity the invitation actually wrote - Cognito's `sub` on AWS."""
    async with get_sessionmaker()() as session:
        row = (
            (
                await session.execute(
                    select(User).where(User.tenant_id == tenant_id, User.role == UserRole.ADMIN)
                )
            )
            .scalars()
            .first()
        )
        assert row is not None, f"no admin was created for {tenant_id}"
        return row.cognito_sub or row.id


@pytest.fixture
async def api() -> AsyncIterator[httpx.AsyncClient]:
    if not settings.dev_auth_enabled:
        pytest.skip("DEV_AUTH_ENABLED=false - no local directory to sign in against")
    if not await _reachable():
        await dispose_engine()
        pytest.skip(
            "no database reachable - start the local stack with "
            "'docker compose --env-file .env -f infra/docker/docker-compose.yml up -d'"
        )

    await _cleanup()
    await _ensure_operator()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        try:
            yield client
        finally:
            await _cleanup()
            await dispose_engine()


async def _onboard(api: httpx.AsyncClient, operator: str, tenant_id: str, name: str) -> Any:
    created = await api.post(
        "/api/v1/platform/tenants",
        json={
            "name": name,
            "tenant_id": tenant_id,
            "contact_email": f"admin@{tenant_id}.example",
        },
        headers=auth(operator),
    )
    assert created.status_code == 201, created.text

    invited = await api.post(
        f"/api/v1/platform/tenants/{tenant_id}/admins",
        json={
            "email": f"admin@{tenant_id}.example",
            "display_name": f"{name} Admin",
            "department": "technical",
        },
        headers=auth(operator),
    )
    assert invited.status_code == 201, invited.text
    return invited.json()


async def test_two_companies_are_onboarded_and_stay_apart(api: httpx.AsyncClient) -> None:
    operator = token_for(tenant_id=PLATFORM_TENANT_ID, user_id=OPERATOR_ID, role="platform_admin")

    # -- 1. the operator onboards two companies --------------------------
    infinity_invite = await _onboard(api, operator, INFINITY, INFINITY_NAME)
    prp_invite = await _onboard(api, operator, PRP, PRP_NAME)

    for invite in (infinity_invite, prp_invite):
        assert invite["user"]["role"] == "admin"
        # No credential is ever chosen or returned - not the dev password, and
        # no field that could carry a temporary one.
        assert DEV_PASSWORD not in str(invite)
        assert not [k for k in invite["user"] if "password" in k or "secret" in k]
        assert not [k for k in invite if "password" in k or "secret" in k]
    assert infinity_invite["user"]["tenant_id"] == INFINITY
    assert prp_invite["user"]["tenant_id"] == PRP

    registry = (await api.get("/api/v1/platform/tenants", headers=auth(operator))).json()
    by_id = {t["id"]: t for t in registry["items"]}
    assert by_id[INFINITY]["name"] == INFINITY_NAME
    assert by_id[INFINITY]["admin_count"] == 1 and by_id[PRP]["admin_count"] == 1

    # -- 2. the registry refuses duplicates and reserved ids -------------
    duplicate = await api.post(
        "/api/v1/platform/tenants",
        json={"name": INFINITY_NAME, "tenant_id": INFINITY},
        headers=auth(operator),
    )
    assert duplicate.status_code == 422, "a duplicate company was accepted"

    impostor = await api.post(
        "/api/v1/platform/tenants",
        json={"name": "Platform Impostor", "tenant_id": PLATFORM_TENANT_ID},
        headers=auth(operator),
    )
    assert impostor.status_code == 422, "the reserved platform id was accepted"

    # -- 3. the invited admin signs in through the normal front door -----
    signin = await api.post(
        "/api/v1/auth/login",
        json={"email": f"admin@{INFINITY}.example", "password": DEV_PASSWORD},
    )
    if signin.status_code >= 500:
        pytest.skip("Redis is not running - the sign-in rate limiter needs it")
    assert signin.status_code == 200, signin.text
    assert signin.json()["user"]["tenant_id"] == INFINITY
    assert signin.json()["user"]["role"] == "admin"
    assert DEV_PASSWORD not in signin.text

    wrong = await api.post(
        "/api/v1/auth/login",
        json={"email": f"admin@{INFINITY}.example", "password": "WrongPassword!99"},
    )
    unknown = await api.post(
        "/api/v1/auth/login",
        json={"email": "nobody@nowhere.example", "password": "WrongPassword!99"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"] == unknown.json()["error"], "the 401s are distinguishable"

    # -- 4. each admin populates its own departments ---------------------
    infinity_admin = token_for(
        tenant_id=INFINITY, user_id=await _admin_subject(INFINITY), role="admin"
    )
    prp_admin = token_for(tenant_id=PRP, user_id=await _admin_subject(PRP), role="admin")

    for token, tenant in ((infinity_admin, INFINITY), (prp_admin, PRP)):
        for dept in DEPARTMENTS:
            created = await api.post(
                "/api/v1/admin/users",
                json={
                    "email": f"{dept}@{tenant}.example",
                    "display_name": f"{dept.upper()} User",
                    "role": "user",
                    "department": dept,
                },
                headers=auth(token),
            )
            assert created.status_code == 201, created.text
            assert created.json()["user"]["department"] == dept
            assert created.json()["user"]["tenant_id"] == tenant

    # -- 5. neither company can see the other ----------------------------
    infinity_users = (await api.get("/api/v1/admin/users", headers=auth(infinity_admin))).json()
    prp_users = (await api.get("/api/v1/admin/users", headers=auth(prp_admin))).json()

    assert infinity_users["total"] == 4, "expected the admin plus HR, Finance and Legal"
    assert prp_users["total"] == 4
    assert {u["tenant_id"] for u in infinity_users["items"]} == {INFINITY}
    assert {u["tenant_id"] for u in prp_users["items"]} == {PRP}
    assert not {u["email"] for u in infinity_users["items"]} & {
        u["email"] for u in prp_users["items"]
    }

    # -- 6. tenant A cannot touch tenant B, by a valid id ----------------
    victim = prp_users["items"][0]["id"]
    stolen_patch = await api.patch(
        f"/api/v1/admin/users/{victim}",
        json={"is_active": False},
        headers=auth(infinity_admin),
    )
    assert stolen_patch.status_code == 403, "a cross-tenant modification succeeded"

    stolen_reset = await api.post(
        f"/api/v1/admin/users/{victim}/reset-password", headers=auth(infinity_admin)
    )
    assert stolen_reset.status_code == 403, "a cross-tenant password reset succeeded"

    still_active = (await api.get("/api/v1/admin/users", headers=auth(prp_admin))).json()
    assert all(u["is_active"] for u in still_active["items"]), "the other company was modified"

    # -- 7. a tenant admin cannot escalate -------------------------------
    assert (
        await api.post(
            "/api/v1/platform/tenants",
            json={"name": "Sneaky Holdings"},
            headers=auth(infinity_admin),
        )
    ).status_code == 403, "a tenant admin created a company"

    assert (
        await api.post(
            "/api/v1/admin/users",
            json={"email": "escalate@infinity.example", "role": "platform_admin"},
            headers=auth(infinity_admin),
        )
    ).status_code == 422, "a tenant admin minted a platform operator"

    # -- 8. a plain user manages nothing, but still has a workspace ------
    plain = next(u for u in infinity_users["items"] if u["role"] == "user")
    plain_token = token_for(tenant_id=INFINITY, user_id=plain["id"], role="user")

    assert (await api.get("/api/v1/admin/users", headers=auth(plain_token))).status_code == 403
    assert (await api.get("/api/v1/platform/tenants", headers=auth(plain_token))).status_code == 403
    assert (await api.get("/api/v1/me", headers=auth(plain_token))).status_code == 200

    # -- 9. a company cannot be left with nobody to administer it --------
    admin_row = next(u for u in infinity_users["items"] if u["role"] == "admin")
    assert (
        await api.patch(
            f"/api/v1/admin/users/{admin_row['id']}",
            json={"is_active": False},
            headers=auth(infinity_admin),
        )
    ).status_code == 422, "the last admin deactivated itself"

    promoted = await api.patch(
        f"/api/v1/admin/users/{plain['id']}",
        json={"role": "admin"},
        headers=auth(infinity_admin),
    )
    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"

    demoted = await api.patch(
        f"/api/v1/admin/users/{plain['id']}",
        json={"role": "user", "is_active": False},
        headers=auth(infinity_admin),
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "user" and demoted.json()["is_active"] is False

    # -- 10. suspension is reversible and destroys nothing ---------------
    suspended = await api.patch(
        f"/api/v1/platform/tenants/{PRP}",
        json={"is_active": False},
        headers=auth(operator),
    )
    assert suspended.status_code == 200 and suspended.json()["is_active"] is False
    assert (
        await api.post(
            f"/api/v1/platform/tenants/{PRP}/admins",
            json={"email": "second-admin@prp.example"},
            headers=auth(operator),
        )
    ).status_code == 422, "a suspended company accepted a new admin"

    restored = await api.patch(
        f"/api/v1/platform/tenants/{PRP}",
        json={"is_active": True},
        headers=auth(operator),
    )
    assert restored.status_code == 200 and restored.json()["user_count"] == 4

    # -- 11. every step left a trail -------------------------------------
    events = (await api.get("/api/v1/platform/audit?limit=500", headers=auth(operator))).json()
    recorded = {e["event_type"] for e in events}
    for required in (
        "tenant.created",
        "tenant.updated",
        "user.tenant_admin_invited",
        "user.created",
        "user.role_changed",
        "user.deactivated",
    ):
        assert required in recorded, f"{required} was not audited"

    # Reason codes and identifiers only - never a credential.
    assert not any("password" in str(e.get("details", {})).lower() for e in events)
