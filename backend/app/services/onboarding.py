"""Authorization rules for the tenant onboarding hierarchy.

    Platform operator  ->  creates a company
                       ->  invites that company's first admin
    Company admin      ->  invites users inside its own company only

Every rule here is a pure function over the verified RequestContext, so the
hierarchy can be tested exhaustively without a database, a token, or a network.
Routes call these; they never re-derive the rules inline.

See .claude/rules/tenant-isolation.md section 2 and security.md section 2.
"""

from __future__ import annotations

import re
import unicodedata

from app.core.context import (
    PLATFORM_TENANT_ID,
    TENANT_ASSIGNABLE_ROLES,
    RequestContext,
    Role,
)
from app.core.exceptions import AuthorizationError, TenantIsolationError, ValidationError
from app.core.logging import log_security_event

# A tenant id appears in S3 prefixes, Qdrant filters, Redis keys and URLs, so it
# is restricted to what is unambiguous in all four.
TENANT_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])$")

# Names that would collide with the platform tenant, a route prefix, or a
# hostname label. Claiming one would be a privilege-escalation primitive.
RESERVED_TENANT_IDS: frozenset[str] = frozenset(
    {
        PLATFORM_TENANT_ID,
        "admin",
        "administrator",
        "api",
        "app",
        "auth",
        "console",
        "health",
        "internal",
        "login",
        "public",
        "root",
        "static",
        "system",
        "tenant",
        "www",
    }
)


def slugify_tenant_id(name: str) -> str:
    """Derive a candidate tenant id from a company name.

    Used only to prefill the onboarding form. The value that is actually stored
    still goes through validate_tenant_id.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return slug[:64].strip("-")


def validate_tenant_id(candidate: str) -> str:
    """Normalize and check a tenant id, or refuse the request."""
    value = candidate.strip().lower()

    if not TENANT_ID_PATTERN.match(value):
        raise ValidationError(
            "A tenant id must be 2-64 characters of lowercase letters, digits and "
            "hyphens, starting and ending with a letter or digit."
        )
    if value in RESERVED_TENANT_IDS:
        log_security_event(
            "tenant.reserved_id_rejected",
            reason="reserved_tenant_id",
            severity="error",
        )
        raise ValidationError("That tenant id is reserved. Choose another.")
    return value


def assert_role_assignable(actor: RequestContext, target_role: str) -> Role:
    """The escalation gate: who may hand out which role.

    A tenant admin can only ever create peers and users inside its own company.
    It can never mint a platform operator - that is the whole point of the
    hierarchy, and it is enforced here rather than by the shape of a form.
    """
    if target_role == "platform_admin":
        log_security_event(
            "authz.platform_role_escalation_blocked",
            reason="non_platform_actor_attempted_platform_role_grant",
            severity="critical",
        )
        raise AuthorizationError("The platform role cannot be granted here.")

    if target_role not in TENANT_ASSIGNABLE_ROLES:
        raise ValidationError("Unknown role.")

    if not actor.is_admin:
        raise AuthorizationError()

    return target_role  # type: ignore[return-value]


def assert_manages_tenant(actor: RequestContext, target_tenant_id: str) -> None:
    """A tenant admin manages exactly one company: the one in its own token."""
    if target_tenant_id != actor.tenant_id:
        log_security_event(
            "tenant.cross_tenant_user_management_blocked",
            reason="actor_tenant_does_not_match_target_tenant",
            severity="critical",
        )
        raise TenantIsolationError()


def assert_not_self(actor: RequestContext, target_user_id: str, *, action: str) -> None:
    """Stop an admin locking itself out.

    Self-demotion and self-deactivation are the two ways a company can end a
    session with nobody able to administer it. Both are refused, so recovery
    never depends on the platform operator being awake.
    """
    if target_user_id == actor.user_id:
        raise ValidationError(f"You cannot {action} your own account.")


def assert_not_last_admin(
    *, target_is_admin: bool, remaining_active_admins: int, action: str
) -> None:
    """Refuse the change that would leave a company with no active admin."""
    if target_is_admin and remaining_active_admins < 1:
        raise ValidationError(
            f"This company would be left with no active administrator. "
            f"Promote another user before you {action} this one."
        )
