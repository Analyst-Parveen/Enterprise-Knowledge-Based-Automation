"""The identity directory: Amazon Cognito, plus a local stand-in for dev.

Two things live here and nowhere else:

1. **Sign-in.** The browser posts credentials to this API, which forwards them
   to Cognito and hands back tokens. The alternative - shipping a Cognito SDK
   to the browser - would leave local development with no way to exercise the
   real sign-in screen, because Phase 0-4 runs at $0 with no user pool. One code
   path, two directories behind it.

2. **Administration.** Inviting a user, changing a role, disabling an account.
   These are `cognito-idp` admin calls, so they only ever run after the route
   has established that the caller is allowed to make them.

**The bearer token is the Cognito ID token, not the access token.** Cognito puts
custom attributes (`custom:tenant_id`, `custom:role`) in the ID token only, and
gives the access token no `aud` claim at all - so an access token both fails
audience verification and carries no tenant. See core/auth.py.

Passwords and tokens are never logged, never stored, and never returned in an
API response. See .claude/rules/secrets-management.md.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import ROLE_CLAIM, TENANT_CLAIM
from app.core.config import settings
from app.core.context import Role
from app.core.exceptions import (
    AuthenticationError,
    RateLimitError,
    UpstreamError,
    ValidationError,
)
from app.core.logging import get_logger, log_security_event
from app.db.models import User

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A signed-in session. `token` is the bearer token for this API."""

    token: str
    expires_in: int
    refresh_token: str | None = None


@dataclass(frozen=True, slots=True)
class PendingChallenge:
    """Sign-in stopped short of a session and needs one more step.

    `session` is an opaque Cognito continuation handle. It is short-lived, is
    useless without the matching challenge response, and is never logged.
    """

    challenge: str
    session: str | None = None


@dataclass(frozen=True, slots=True)
class InvitedIdentity:
    subject: str
    invitation_sent: bool


AuthOutcome = IssuedSession | PendingChallenge

# Cognito challenge names, not credentials - hence the noqa.
NEW_PASSWORD_REQUIRED = "NEW_PASSWORD_REQUIRED"  # noqa: S105
PASSWORD_RESET_REQUIRED = "PASSWORD_RESET_REQUIRED"  # noqa: S105


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------
class IdentityProvider(Protocol):
    name: str

    async def authenticate(self, email: str, password: str) -> AuthOutcome: ...

    async def complete_new_password(
        self, email: str, session: str, new_password: str
    ) -> AuthOutcome: ...

    async def refresh(self, refresh_token: str) -> IssuedSession: ...

    async def start_password_reset(self, email: str) -> None: ...

    async def confirm_password_reset(self, email: str, code: str, new_password: str) -> None: ...

    async def sign_out(self, subject: str) -> None: ...

    async def invite(
        self, *, email: str, tenant_id: str, role: Role, display_name: str | None
    ) -> InvitedIdentity: ...

    async def set_role(self, subject: str, role: Role) -> None: ...

    async def set_enabled(self, subject: str, enabled: bool) -> None: ...

    async def reset_to_temporary_password(self, subject: str) -> None: ...


# ---------------------------------------------------------------------------
# Amazon Cognito
# ---------------------------------------------------------------------------
_cognito: Any = None


def _client() -> Any:
    global _cognito
    if _cognito is None:
        import boto3
        from botocore.config import Config as BotoConfig

        _cognito = boto3.client(
            "cognito-idp",
            config=BotoConfig(
                region_name=settings.cognito_region,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
    return _cognito


# Cognito error code -> what the caller is told. The mapping is deliberately
# lossy for anything that would reveal whether an account exists.
_GENERIC_SIGNIN_FAILURE = "Incorrect email or password."


def _translate(exc: Exception) -> Exception:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")

    if code in ("NotAuthorizedException", "UserNotFoundException", "UserNotConfirmedException"):
        return AuthenticationError(_GENERIC_SIGNIN_FAILURE)
    if code in (
        "TooManyRequestsException",
        "LimitExceededException",
        "TooManyFailedAttemptsException",
    ):
        return RateLimitError()
    if code == "InvalidPasswordException":
        # The policy text is not a secret, and hiding it makes the form unusable.
        return ValidationError(
            "That password does not meet the policy: at least 12 characters with "
            "upper case, lower case, a number and a symbol."
        )
    if code in ("CodeMismatchException", "ExpiredCodeException"):
        return ValidationError("That verification code is not valid or has expired.")
    if code == "UsernameExistsException":
        return ValidationError("An account with that email address already exists.")
    if code == "InvalidParameterException":
        return ValidationError("The request was rejected by the identity provider.")

    logger.error(
        "cognito_call_failed",
        extra={
            "extra": {
                "error_code": code or "unknown",
                "exc_type": type(exc).__name__,
            }
        },
    )
    return UpstreamError("The identity provider is unavailable.")


class CognitoIdentityProvider:
    """Amazon Cognito, the single user pool created in the baseline stack."""

    name = "cognito"

    def __init__(self) -> None:
        if not settings.cognito_user_pool_id or not settings.cognito_client_id:
            raise UpstreamError("Authentication is not configured.")
        self._pool = settings.cognito_user_pool_id
        self._app_client = settings.cognito_client_id

    # -- sign-in ---------------------------------------------------------
    def _session_from(self, auth_result: dict[str, Any]) -> IssuedSession:
        return IssuedSession(
            token=auth_result["IdToken"],
            expires_in=int(auth_result.get("ExpiresIn", 3600)),
            refresh_token=auth_result.get("RefreshToken"),
        )

    async def authenticate(self, email: str, password: str) -> AuthOutcome:
        def _call() -> Any:
            return _client().initiate_auth(
                ClientId=self._app_client,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": email, "PASSWORD": password},
            )

        try:
            response = await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001 - translated, never surfaced raw
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code == "PasswordResetRequiredException":
                return PendingChallenge(challenge=PASSWORD_RESET_REQUIRED)
            raise _translate(exc) from None

        if "AuthenticationResult" in response:
            return self._session_from(response["AuthenticationResult"])

        challenge = response.get("ChallengeName", "")
        if challenge == NEW_PASSWORD_REQUIRED:
            return PendingChallenge(challenge=challenge, session=response.get("Session"))

        # MFA and the SRP challenges are not enabled on this pool. Anything else
        # arriving here is a pool misconfiguration, not a user error.
        log_security_event(
            "auth.unsupported_challenge", reason=challenge or "no_challenge", severity="error"
        )
        raise UpstreamError("This account requires a sign-in step this app cannot complete.")

    async def complete_new_password(
        self, email: str, session: str, new_password: str
    ) -> AuthOutcome:
        def _call() -> Any:
            return _client().respond_to_auth_challenge(
                ClientId=self._app_client,
                ChallengeName=NEW_PASSWORD_REQUIRED,
                Session=session,
                ChallengeResponses={"USERNAME": email, "NEW_PASSWORD": new_password},
            )

        try:
            response = await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from None

        if "AuthenticationResult" not in response:
            raise UpstreamError("The password was changed but no session was issued.")
        return self._session_from(response["AuthenticationResult"])

    async def refresh(self, refresh_token: str) -> IssuedSession:
        def _call() -> Any:
            return _client().initiate_auth(
                ClientId=self._app_client,
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters={"REFRESH_TOKEN": refresh_token},
            )

        try:
            response = await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from None

        result = response.get("AuthenticationResult")
        if not result:
            raise AuthenticationError("That session could not be renewed.")
        # A refresh returns no new refresh token; the original stays valid.
        return self._session_from(result)

    async def start_password_reset(self, email: str) -> None:
        def _call() -> None:
            _client().forgot_password(ClientId=self._app_client, Username=email)

        try:
            await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            # Never let this endpoint reveal whether the address is registered.
            if code in ("UserNotFoundException", "InvalidParameterException"):
                logger.info("password_reset_ignored", extra={"extra": {"error_code": code}})
                return
            raise _translate(exc) from None

    async def confirm_password_reset(self, email: str, code: str, new_password: str) -> None:
        def _call() -> None:
            _client().confirm_forgot_password(
                ClientId=self._app_client,
                Username=email,
                ConfirmationCode=code,
                Password=new_password,
            )

        try:
            await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from None

    async def sign_out(self, subject: str) -> None:
        """Revoke every issued token for this identity, on every device.

        Uses the admin API with the subject from the verified token, so the
        caller never has to hand us an access token to be signed out.
        """

        def _call() -> None:
            _client().admin_user_global_sign_out(UserPoolId=self._pool, Username=subject)

        try:
            await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            # A sign-out must always look like it worked; the client has already
            # discarded its tokens either way.
            logger.warning("global_sign_out_failed", extra={"extra": {"error_code": code}})

    # -- administration --------------------------------------------------
    async def invite(
        self, *, email: str, tenant_id: str, role: Role, display_name: str | None
    ) -> InvitedIdentity:
        """Create the account and let Cognito email the temporary password.

        No password is chosen here, so none can be leaked, logged, or shared in
        a chat message. The invitee sets their own on first sign-in.
        """
        attributes = [
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
            {"Name": TENANT_CLAIM, "Value": tenant_id},
            {"Name": ROLE_CLAIM, "Value": role},
        ]
        if display_name:
            attributes.append({"Name": "name", "Value": display_name})

        def _call() -> Any:
            return _client().admin_create_user(
                UserPoolId=self._pool,
                Username=email,
                UserAttributes=attributes,
                DesiredDeliveryMediums=["EMAIL"],
            )

        try:
            response = await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from None

        subject = next(
            (a["Value"] for a in response["User"].get("Attributes", []) if a["Name"] == "sub"),
            response["User"]["Username"],
        )
        return InvitedIdentity(subject=subject, invitation_sent=True)

    async def set_role(self, subject: str, role: Role) -> None:
        def _call() -> None:
            _client().admin_update_user_attributes(
                UserPoolId=self._pool,
                Username=subject,
                UserAttributes=[{"Name": ROLE_CLAIM, "Value": role}],
            )

        try:
            await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from None

    async def set_enabled(self, subject: str, enabled: bool) -> None:
        def _call() -> None:
            client = _client()
            if enabled:
                client.admin_enable_user(UserPoolId=self._pool, Username=subject)
            else:
                # Disable, then revoke live tokens - otherwise a disabled user
                # keeps working until their current token expires.
                client.admin_disable_user(UserPoolId=self._pool, Username=subject)
                client.admin_user_global_sign_out(UserPoolId=self._pool, Username=subject)

        try:
            await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from None

    async def reset_to_temporary_password(self, subject: str) -> None:
        """Re-send the invitation. Cognito generates and emails the password."""

        def _call() -> None:
            _client().admin_reset_user_password(UserPoolId=self._pool, Username=subject)

        try:
            await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from None


# ---------------------------------------------------------------------------
# local development
# ---------------------------------------------------------------------------
class LocalIdentityProvider:
    """Dev-only directory so the real sign-in screen works with no user pool.

    Hard-gated the same three ways as the dev token path in core/auth.py: the
    environment must be dev, DEV_AUTH_ENABLED must be on, and a security test
    asserts this class refuses to construct anywhere else.

    The `users` table is the directory here, and every seeded account shares one
    configured password. No password is ever stored or verified against a hash,
    because none of this exists outside a laptop.
    """

    name = "local"

    def __init__(self, session: AsyncSession) -> None:
        if not settings.is_dev or not settings.dev_auth_enabled:
            raise UpstreamError("Authentication is not configured.")
        self._session = session

    async def _lookup(self, email: str) -> User | None:
        rows = await self._session.execute(
            select(User).where(User.email == email.strip().lower(), User.is_active.is_(True))
        )
        return rows.scalars().first()

    def _mint(self, user: User) -> IssuedSession:
        ttl = settings.dev_auth_token_ttl_seconds
        token = jwt.encode(
            {
                "sub": user.cognito_sub or user.id,
                TENANT_CLAIM: user.tenant_id,
                ROLE_CLAIM: user.role.value,
                "email": user.email,
                "exp": dt.datetime.now(dt.UTC) + dt.timedelta(seconds=ttl),
            },
            settings.dev_auth_secret.get_secret_value(),
            algorithm="HS256",
        )
        return IssuedSession(token=token, expires_in=ttl, refresh_token=f"local-refresh:{user.id}")

    async def authenticate(self, email: str, password: str) -> AuthOutcome:
        user = await self._lookup(email)
        if user is None or password != settings.dev_auth_password.get_secret_value():
            raise AuthenticationError(_GENERIC_SIGNIN_FAILURE)
        return self._mint(user)

    async def complete_new_password(
        self, email: str, session: str, new_password: str
    ) -> AuthOutcome:
        user = await self._lookup(email)
        if user is None:
            raise AuthenticationError(_GENERIC_SIGNIN_FAILURE)
        return self._mint(user)

    async def refresh(self, refresh_token: str) -> IssuedSession:
        if not refresh_token.startswith("local-refresh:"):
            raise AuthenticationError("That session could not be renewed.")
        user_id = refresh_token.split(":", 1)[1]
        rows = await self._session.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )
        user = rows.scalar_one_or_none()
        if user is None:
            raise AuthenticationError("That session could not be renewed.")
        return self._mint(user)

    async def start_password_reset(self, email: str) -> None:
        logger.info("local_password_reset_noop")

    async def confirm_password_reset(self, email: str, code: str, new_password: str) -> None:
        raise ValidationError("Password reset is not available in local development.")

    async def sign_out(self, subject: str) -> None:
        logger.info("local_sign_out")

    async def invite(
        self, *, email: str, tenant_id: str, role: Role, display_name: str | None
    ) -> InvitedIdentity:
        # No email is sent locally. The account is usable immediately with the
        # shared dev password, which is what makes the flow demonstrable at $0.
        return InvitedIdentity(subject=f"local-{uuid.uuid4()}", invitation_sent=False)

    async def set_role(self, subject: str, role: Role) -> None:
        return None

    async def set_enabled(self, subject: str, enabled: bool) -> None:
        return None

    async def reset_to_temporary_password(self, subject: str) -> None:
        return None


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def get_identity_provider(session: AsyncSession) -> IdentityProvider:
    """Cognito everywhere; the local directory only when dev auth is on."""
    if settings.is_dev and settings.dev_auth_enabled:
        return LocalIdentityProvider(session)
    return CognitoIdentityProvider()
