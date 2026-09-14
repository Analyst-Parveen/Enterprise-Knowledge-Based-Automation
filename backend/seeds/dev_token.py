"""Mint a local development token so the frontend can sign in without Cognito.

    python -m seeds.dev_token                 # user in the seeded tenant
    python -m seeds.dev_token --role admin
    python -m seeds.dev_token --tenant seed-tenant-contoso --user seed-user-b
    python -m seeds.dev_token --role platform_admin     # service provider

This ONLY works when ENVIRONMENT=dev and DEV_AUTH_ENABLED=true. The backend
refuses these tokens in any other environment - a test asserts that.

Since the frontend gained a real sign-in screen, this is a debugging tool
rather than the way in: locally, sign in as any seeded user with the password
in DEV_AUTH_PASSWORD.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import jwt

from app.core.config import settings
from app.core.context import PLATFORM_TENANT_ID


def mint(tenant_id: str, user_id: str, role: str, hours: int) -> str:
    if not settings.is_dev or not settings.dev_auth_enabled:
        raise SystemExit(
            "Refusing to mint a dev token: requires ENVIRONMENT=dev and DEV_AUTH_ENABLED=true."
        )

    now = dt.datetime.now(dt.UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "custom:tenant_id": tenant_id,
            "custom:role": role,
            "email": f"{user_id}@example.com",
            "iat": now,
            "exp": now + dt.timedelta(hours=hours),
        },
        settings.dev_auth_secret.get_secret_value(),
        algorithm="HS256",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a local dev JWT.")
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--role", default="user", choices=["user", "admin", "platform_admin"])
    parser.add_argument("--hours", type=int, default=12)
    args = parser.parse_args()

    # The platform role only exists inside the platform tenant - the backend
    # rejects any other pairing - so defaulting them together stops the most
    # likely mistake before it reaches the API.
    if args.role == "platform_admin":
        tenant = args.tenant or PLATFORM_TENANT_ID
        user = args.user or "seed-platform-admin"
    else:
        tenant = args.tenant or "seed-tenant-northwind"
        user = args.user or "seed-user-a"
    args.tenant, args.user = tenant, user

    token = mint(args.tenant, args.user, args.role, args.hours)

    print(token)
    print(
        f"\n  tenant : {args.tenant}\n  user   : {args.user}\n  role   : {args.role}"
        f"\n  expires: {args.hours}h\n\nPaste this into the sign-in box at http://localhost:3000",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
