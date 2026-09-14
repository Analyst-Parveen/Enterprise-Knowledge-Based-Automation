"""Sign-in, session renewal, password recovery and sign-out.

These are the only endpoints in the application that are reachable without a
token, so they are the only ones that have to defend themselves:

* Every response is shaped so it cannot be used to discover whether an account
  exists. A wrong password and an unknown address return the same 401; a reset
  request always returns 202.
* Attempts are rate limited per account before the directory is touched.
* Nothing here logs a password, a token, or a Cognito challenge session.

The token handed back is the Cognito **ID token**, because that is the only
Cognito token carrying `custom:tenant_id` and `custom:role`. See
services/identity.py.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.core import ratelimit
from app.core.auth import verify_token
from app.core.logging import get_logger, log_security_event
from app.db import repositories as repo
from app.schemas import (
    AcknowledgedResponse,
    ConfirmPasswordResetRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    NewPasswordRequest,
    RefreshRequest,
    SessionResponse,
)
from app.services.identity import IssuedSession, get_identity_provider

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _describe(session: IssuedSession) -> SessionResponse:
    """Turn an issued session into a response, including who it belongs to.

    The principal is read back out of the freshly issued token rather than from
    the request, so the client is told exactly what the server will enforce on
    the next call - including a tenant it never got to choose.
    """
    ctx = verify_token(session.token)
    return SessionResponse(
        token=session.token,
        expires_in=session.expires_in,
        refresh_token=session.refresh_token,
        user=MeResponse(
            user_id=ctx.user_id, tenant_id=ctx.tenant_id, role=ctx.role, email=ctx.email
        ),
    )


@router.post("/login", response_model=SessionResponse)
async def login(payload: LoginRequest, session: DbSession) -> SessionResponse:
    await ratelimit.enforce_anonymous(payload.email, "auth")

    provider = get_identity_provider(session)
    outcome = await provider.authenticate(payload.email, payload.password)

    if isinstance(outcome, IssuedSession):
        response = _describe(outcome)
        if response.user:
            await repo.mark_login(session, subject=response.user.user_id)
            await repo.record_audit(
                session,
                event_type="auth.login_succeeded",
                tenant_id=response.user.tenant_id,
                resource_type="user",
                resource_id=response.user.user_id,
                reason="password_grant",
                role=response.user.role,
                provider=provider.name,
            )
        return response

    # An invited account signing in for the first time, or one an admin has
    # sent through a reset. Not a failure - the next step is a new password.
    return SessionResponse(challenge=outcome.challenge, challenge_session=outcome.session)


@router.post("/new-password", response_model=SessionResponse)
async def complete_new_password(payload: NewPasswordRequest, session: DbSession) -> SessionResponse:
    """Replace the one-time invitation password with the user's own."""
    await ratelimit.enforce_anonymous(payload.email, "auth")

    provider = get_identity_provider(session)
    outcome = await provider.complete_new_password(
        payload.email, payload.challenge_session, payload.new_password
    )
    if isinstance(outcome, IssuedSession):
        response = _describe(outcome)
        if response.user:
            await repo.mark_login(session, subject=response.user.user_id)
            await repo.record_audit(
                session,
                event_type="auth.first_sign_in_completed",
                tenant_id=response.user.tenant_id,
                resource_type="user",
                resource_id=response.user.user_id,
                reason="invitation_password_replaced",
            )
        return response
    return SessionResponse(challenge=outcome.challenge, challenge_session=outcome.session)


@router.post("/refresh", response_model=SessionResponse)
async def refresh(payload: RefreshRequest, session: DbSession) -> SessionResponse:
    """Renew an expiring session without asking for the password again."""
    provider = get_identity_provider(session)
    return _describe(await provider.refresh(payload.refresh_token))


@router.post(
    "/forgot-password",
    response_model=AcknowledgedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: ForgotPasswordRequest, session: DbSession
) -> AcknowledgedResponse:
    """Start a self-service reset.

    Always 202, whatever happened. An endpoint that says "no such user" is a
    free list of valid addresses.
    """
    await ratelimit.enforce_anonymous(payload.email, "auth")
    provider = get_identity_provider(session)
    await provider.start_password_reset(payload.email)
    return AcknowledgedResponse()


@router.post("/confirm-password-reset", response_model=AcknowledgedResponse)
async def confirm_password_reset(
    payload: ConfirmPasswordResetRequest, session: DbSession
) -> AcknowledgedResponse:
    await ratelimit.enforce_anonymous(payload.email, "auth")
    provider = get_identity_provider(session)
    await provider.confirm_password_reset(payload.email, payload.code, payload.new_password)
    return AcknowledgedResponse(
        status="reset", message="Your password has been changed. Sign in with it."
    )


@router.post("/logout", response_model=AcknowledgedResponse)
async def logout(ctx: CurrentUser, session: DbSession) -> AcknowledgedResponse:
    """Revoke every token issued to this identity, not just the browser's copy.

    Clearing client storage alone leaves a stolen token valid until it expires.
    """
    provider = get_identity_provider(session)
    await provider.sign_out(ctx.user_id)

    await repo.record_audit(
        session,
        event_type="auth.logout",
        ctx=ctx,
        resource_type="user",
        resource_id=ctx.user_id,
        reason="global_sign_out",
    )
    log_security_event("auth.logout", reason="global_sign_out", severity="info")
    return AcknowledgedResponse(status="signed_out", message="You have been signed out.")
