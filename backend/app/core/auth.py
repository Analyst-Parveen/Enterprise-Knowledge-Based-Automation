"""Cognito JWT verification and RBAC.

Signature, issuer, audience, and expiry are ALL verified against the Cognito
JWKS on every request. tenant_id is read from the verified token and from
nowhere else.

See .claude/rules/security.md section 2 and tenant-isolation.md section 2.
"""

from __future__ import annotations

import time
from typing import Any, cast

import httpx
import jwt
from jwt import PyJWKClient

from app.core.config import settings
from app.core.context import RequestContext, Role
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.logging import get_logger, log_security_event

logger = get_logger(__name__)

# Cognito custom attributes carrying our authorization data.
TENANT_CLAIM = "custom:tenant_id"
ROLE_CLAIM = "custom:role"

_jwk_client: PyJWKClient | None = None
_jwk_client_created_at: float = 0.0
_JWKS_TTL_SECONDS = 3600


def _get_jwk_client() -> PyJWKClient:
    """Cached JWKS client. Refreshed hourly so key rotation is picked up."""
    global _jwk_client, _jwk_client_created_at
    now = time.time()
    if _jwk_client is None or (now - _jwk_client_created_at) > _JWKS_TTL_SECONDS:
        if not settings.cognito_user_pool_id:
            raise AuthenticationError("Authentication is not configured.")
        _jwk_client = PyJWKClient(settings.cognito_jwks_url, cache_keys=True)
        _jwk_client_created_at = now
    return _jwk_client


def _claims_to_context(claims: dict[str, Any]) -> RequestContext:
    """Build the request context from VERIFIED claims only."""
    user_id = claims.get("sub")
    tenant_id = claims.get(TENANT_CLAIM) or claims.get("tenant_id")
    raw_role = (claims.get(ROLE_CLAIM) or claims.get("role") or "user").lower()

    if not user_id:
        raise AuthenticationError("Token is missing a subject.")
    if not tenant_id:
        # A token without a tenant cannot be authorized for any data.
        log_security_event("auth.missing_tenant", reason="token_has_no_tenant_claim")
        raise AuthenticationError("Token is missing a tenant assignment.")
    if raw_role not in ("user", "admin"):
        log_security_event("auth.unknown_role", reason="role_not_in_allowlist")
        raise AuthenticationError("Token carries an unrecognized role.")

    return RequestContext(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        role=cast(Role, raw_role),
        email=claims.get("email"),
    )


def _verify_dev_token(token: str) -> RequestContext:
    """Local development only, so the stack runs with no Cognito pool at $0.

    Hard-gated three ways: environment must be dev, the flag must be on, and
    a test asserts this path is unreachable outside dev.
    """
    if not settings.is_dev or not settings.dev_auth_enabled:
        raise AuthenticationError("Invalid token.")

    logger.warning(
        "dev_auth_used",
        extra={"extra": {"warning": "DEV AUTH IS ENABLED - never use outside local dev"}},
    )
    try:
        claims = jwt.decode(
            token,
            settings.dev_auth_secret.get_secret_value(),
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid token.") from exc
    return _claims_to_context(claims)


def verify_token(token: str) -> RequestContext:
    """Verify a Cognito access/ID token and return the authenticated principal."""
    if not token:
        raise AuthenticationError()

    if settings.is_dev and settings.dev_auth_enabled:
        return _verify_dev_token(token)

    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.cognito_client_id or None,
            issuer=settings.cognito_issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": bool(settings.cognito_client_id),
                "verify_iss": True,
                "require": ["exp", "iss", "sub"],
            },
        )
    except jwt.ExpiredSignatureError as exc:
        log_security_event("auth.token_expired", reason="expired_signature")
        raise AuthenticationError("Token has expired.") from exc
    except jwt.InvalidIssuerError as exc:
        log_security_event("auth.bad_issuer", reason="issuer_mismatch", severity="error")
        raise AuthenticationError("Invalid token.") from exc
    except jwt.InvalidAudienceError as exc:
        log_security_event("auth.bad_audience", reason="audience_mismatch", severity="error")
        raise AuthenticationError("Invalid token.") from exc
    except (jwt.PyJWTError, httpx.HTTPError) as exc:
        log_security_event("auth.invalid_token", reason=type(exc).__name__)
        raise AuthenticationError("Invalid token.") from exc

    return _claims_to_context(claims)


def require_admin(ctx: RequestContext) -> None:
    """Admin gate. Note: admin does NOT imply cross-tenant data access."""
    if not ctx.is_admin:
        log_security_event(
            "authz.admin_required",
            reason="non_admin_attempted_admin_endpoint",
            severity="error",
        )
        raise AuthorizationError()
