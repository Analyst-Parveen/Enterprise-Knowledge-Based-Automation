"""The onboarding hierarchy, tested as rules rather than as HTTP.

    platform operator -> company -> that company's admin -> that company's users

Every assertion here is about who is *allowed* to do something, so none of it
needs a database, a token, or a network. The HTTP surface that enforces the same
rules is covered in tests/integration/test_onboarding_api.py.
"""

from __future__ import annotations

import pytest

from app.core.auth import (
    _claims_to_context,
    require_admin,
    require_platform_admin,
    require_tenant_admin,
)
from app.core.context import (
    PLATFORM_TENANT_ID,
    TENANT_ASSIGNABLE_ROLES,
    RequestContext,
)
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    TenantIsolationError,
    ValidationError,
)
from app.db.models import UserRole
from app.services.onboarding import (
    RESERVED_TENANT_IDS,
    assert_manages_tenant,
    assert_not_last_admin,
    assert_not_self,
    assert_role_assignable,
    slugify_tenant_id,
    validate_tenant_id,
)


@pytest.fixture
def platform_admin() -> RequestContext:
    return RequestContext(
        user_id="platform-1",
        tenant_id=PLATFORM_TENANT_ID,
        role="platform_admin",
        email="ops@example.com",
    )


@pytest.fixture
def infinity_admin() -> RequestContext:
    return RequestContext(
        user_id="infinity-admin",
        tenant_id="infinity-assurance",
        role="admin",
        email="admin@infinity.example",
    )


@pytest.fixture
def infinity_user() -> RequestContext:
    return RequestContext(
        user_id="infinity-hr",
        tenant_id="infinity-assurance",
        role="user",
        email="hr@infinity.example",
    )


@pytest.fixture
def prp_admin() -> RequestContext:
    return RequestContext(
        user_id="prp-admin",
        tenant_id="prp-service",
        role="admin",
        email="admin@prp.example",
    )


class TestRoleModel:
    def test_platform_role_exists_in_both_layers(self) -> None:
        """The token layer and the database layer must agree on the role set."""
        assert UserRole.PLATFORM_ADMIN.value == "platform_admin"

    def test_platform_admin_is_not_a_tenant_admin(self, platform_admin: RequestContext) -> None:
        """Rank is not reach. A platform operator is not a company's admin."""
        assert platform_admin.is_platform_admin
        assert not platform_admin.is_tenant_admin

    def test_tenant_admin_is_not_a_platform_admin(self, infinity_admin: RequestContext) -> None:
        assert infinity_admin.is_tenant_admin
        assert not infinity_admin.is_platform_admin

    def test_plain_user_is_neither(self, infinity_user: RequestContext) -> None:
        assert not infinity_user.is_admin
        assert not infinity_user.is_tenant_admin
        assert not infinity_user.is_platform_admin

    def test_platform_role_is_not_tenant_assignable(self) -> None:
        assert "platform_admin" not in TENANT_ASSIGNABLE_ROLES


class TestGates:
    def test_only_platform_admin_passes_the_platform_gate(
        self,
        platform_admin: RequestContext,
        infinity_admin: RequestContext,
        infinity_user: RequestContext,
    ) -> None:
        require_platform_admin(platform_admin)  # does not raise
        for principal in (infinity_admin, infinity_user):
            with pytest.raises(AuthorizationError):
                require_platform_admin(principal)

    def test_only_tenant_admin_passes_the_user_management_gate(
        self,
        infinity_admin: RequestContext,
        platform_admin: RequestContext,
        infinity_user: RequestContext,
    ) -> None:
        require_tenant_admin(infinity_admin)  # does not raise
        for principal in (platform_admin, infinity_user):
            with pytest.raises(AuthorizationError):
                require_tenant_admin(principal)

    def test_plain_user_passes_no_admin_gate(self, infinity_user: RequestContext) -> None:
        for gate in (require_admin, require_tenant_admin, require_platform_admin):
            with pytest.raises(AuthorizationError):
                gate(infinity_user)


class TestPrivilegeEscalation:
    """The single most important behaviour in this feature."""

    def test_tenant_admin_cannot_grant_the_platform_role(
        self, infinity_admin: RequestContext
    ) -> None:
        with pytest.raises(AuthorizationError):
            assert_role_assignable(infinity_admin, "platform_admin")

    def test_plain_user_cannot_grant_any_role(self, infinity_user: RequestContext) -> None:
        for role in ("user", "admin"):
            with pytest.raises(AuthorizationError):
                assert_role_assignable(infinity_user, role)

    def test_tenant_admin_can_grant_tenant_roles(self, infinity_admin: RequestContext) -> None:
        assert assert_role_assignable(infinity_admin, "user") == "user"
        assert assert_role_assignable(infinity_admin, "admin") == "admin"

    def test_unknown_roles_are_refused(self, infinity_admin: RequestContext) -> None:
        for role in ("superuser", "owner", "ADMIN", "platform-admin", ""):
            with pytest.raises((ValidationError, AuthorizationError)):
                assert_role_assignable(infinity_admin, role)


class TestTenantBoundary:
    def test_admin_manages_only_its_own_company(self, infinity_admin: RequestContext) -> None:
        assert_manages_tenant(infinity_admin, "infinity-assurance")  # does not raise
        with pytest.raises(TenantIsolationError):
            assert_manages_tenant(infinity_admin, "prp-service")

    def test_two_companies_cannot_reach_each_other(
        self, infinity_admin: RequestContext, prp_admin: RequestContext
    ) -> None:
        with pytest.raises(TenantIsolationError):
            assert_manages_tenant(infinity_admin, prp_admin.tenant_id)
        with pytest.raises(TenantIsolationError):
            assert_manages_tenant(prp_admin, infinity_admin.tenant_id)


class TestPlatformRoleIsBoundToPlatformTenant:
    """A platform role sitting inside a customer tenant is rejected outright."""

    def _claims(self, tenant: str, role: str) -> dict[str, str]:
        return {"sub": "u1", "custom:tenant_id": tenant, "custom:role": role}

    def test_platform_role_in_a_customer_tenant_is_rejected(self) -> None:
        with pytest.raises(AuthenticationError):
            _claims_to_context(self._claims("infinity-assurance", "platform_admin"))

    def test_tenant_role_in_the_platform_tenant_is_rejected(self) -> None:
        for role in ("user", "admin"):
            with pytest.raises(AuthenticationError):
                _claims_to_context(self._claims(PLATFORM_TENANT_ID, role))

    def test_the_valid_pairings_are_accepted(self) -> None:
        assert (
            _claims_to_context(self._claims(PLATFORM_TENANT_ID, "platform_admin")).role
            == "platform_admin"
        )
        assert _claims_to_context(self._claims("infinity-assurance", "admin")).role == "admin"

    def test_an_invented_role_is_still_rejected(self) -> None:
        with pytest.raises(AuthenticationError):
            _claims_to_context(self._claims("infinity-assurance", "superuser"))


class TestLockoutProtection:
    def test_an_admin_cannot_deactivate_itself(self, infinity_admin: RequestContext) -> None:
        with pytest.raises(ValidationError):
            assert_not_self(infinity_admin, infinity_admin.user_id, action="deactivate")

    def test_an_admin_can_act_on_somebody_else(self, infinity_admin: RequestContext) -> None:
        assert_not_self(infinity_admin, "another-user", action="deactivate")

    def test_the_last_admin_cannot_be_removed(self) -> None:
        with pytest.raises(ValidationError):
            assert_not_last_admin(target_is_admin=True, remaining_active_admins=0, action="demote")

    def test_removing_an_admin_is_fine_when_others_remain(self) -> None:
        assert_not_last_admin(target_is_admin=True, remaining_active_admins=1, action="demote")

    def test_removing_a_plain_user_is_never_a_lockout(self) -> None:
        assert_not_last_admin(target_is_admin=False, remaining_active_admins=0, action="deactivate")


class TestTenantIdValidation:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            (
                "Infinity Assurance Solutions Private Limited",
                "infinity-assurance-solutions-private-limited",
            ),
            ("PRP Service Limited", "prp-service-limited"),
            ("Acme & Co.", "acme-co"),
            ("  Spaced   Out  ", "spaced-out"),
        ],
    )
    def test_slug_derivation(self, name: str, expected: str) -> None:
        assert slugify_tenant_id(name) == expected

    @pytest.mark.parametrize(
        "candidate", ["infinity-assurance", "prp-service", "ab", "a1", "tenant-42"]
    )
    def test_valid_ids_are_accepted(self, candidate: str) -> None:
        assert validate_tenant_id(candidate) == candidate

    def test_case_and_whitespace_are_normalized(self) -> None:
        assert validate_tenant_id("  Infinity-Assurance  ") == "infinity-assurance"

    @pytest.mark.parametrize(
        "candidate",
        [
            "a",  # too short
            "-leading",
            "trailing-",
            "has space",
            "has_underscore",
            "has/slash",  # would escape an S3 prefix
            "has.dot",
            "x" * 65,
            "",
        ],
    )
    def test_malformed_ids_are_refused(self, candidate: str) -> None:
        with pytest.raises(ValidationError):
            validate_tenant_id(candidate)

    def test_reserved_ids_are_refused(self) -> None:
        assert PLATFORM_TENANT_ID in RESERVED_TENANT_IDS
        for reserved in RESERVED_TENANT_IDS:
            with pytest.raises(ValidationError):
                validate_tenant_id(reserved)
