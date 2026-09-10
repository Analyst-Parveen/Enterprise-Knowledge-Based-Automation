"""Mint a local development token so the frontend can sign in without Cognito.

    python -m seeds.dev_token                 # user in the seeded tenant
    python -m seeds.dev_token --role admin
    python -m seeds.dev_token --tenant seed-tenant-contoso --user seed-user-b

This ONLY works when ENVIRONMENT=dev and DEV_AUTH_ENABLED=true. The backend
refuses these tokens in any other environment - a test asserts that.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import jwt

from app.core.config import settings


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
    parser.add_argument("--tenant", default="seed-tenant-northwind")
    parser.add_argument("--user", default="seed-user-a")
    parser.add_argument("--role", default="user", choices=["user", "admin"])
    parser.add_argument("--hours", type=int, default=12)
    args = parser.parse_args()

    token = mint(args.tenant, args.user, args.role, args.hours)

    print(token)
    print(
        f"\n  tenant : {args.tenant}\n  user   : {args.user}\n  role   : {args.role}"
        f"\n  expires: {args.hours}h\n\nPaste this into the sign-in box at http://localhost:3000",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
