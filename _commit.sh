#!/usr/bin/env bash
set -euo pipefail
cd /d/infy__masters
git add -A
git -c core.safecrlf=false commit -q -F - <<'MSG'
Add the platform/tenant onboarding hierarchy and a real Cognito sign-in

Introduces a third role, platform_admin, living in a reserved "platform"
tenant, so a service provider can onboard companies without ever gaining
access to their data. Tenant admins manage users inside their own company
only and can assign neither another tenant nor platform_admin.

Backend
- platform_admin role, PLATFORM_TENANT_ID, and a mutual role/tenant binding
  checked at token verification: the platform role is valid only in the
  platform tenant, and no other role is valid there.
- New dependencies TenantAdminUser and PlatformAdminUser alongside AdminUser.
- app/api/v1/platform.py: tenant create/list/suspend, first-admin invite.
- app/api/v1/admin.py: own-company user CRUD, role and department changes,
  deactivate, password reset. Tenant comes from the token, never the body.
- app/api/v1/auth.py: sign-in, the NEW_PASSWORD_REQUIRED challenge, refresh,
  forgot/confirm password and global sign-out.
- app/services/identity.py wraps Cognito admin calls (and a local dev
  provider); app/services/onboarding.py holds the slug and role guards.
- app/db/control_plane.py is the only cross-tenant surface, and every call
  through it writes an audit event.
- TENANT_ASSIGNABLE_ROLES plus Literal schemas reject platform_admin with a
  422 before a handler ever runs.
- Per-account auth rate limiting, keyed by a hash of the identifier rather
  than the IP, which is shared behind CloudFront.

Database
- Alembic 0002: adds the enum label, the new tenant and user columns, and the
  reserved platform tenant. Additive only; existing rows are untouched.

Frontend
- Email/password sign-in with silent token refresh, a first-password
  challenge, password recovery and server-side sign-out. Pasting a token is
  now a localhost-only developer affordance.
- /platform: company registry, two-step onboarding, onboarding audit trail.
- /admin: own-company user management and a company overview.
- Role-aware navigation, with the gates mirroring the backend dependencies
  one-to-one.

Infrastructure and operations
- The ECS task role gains the Cognito admin actions it needs, scoped to the
  existing pool. No new user pool.
- scripts/bootstrap-platform-admin.sh creates or promotes the first platform
  operator; it verifies pool ownership and never prints credentials.
- scripts/build-handbook-pdf.mjs makes the handbook PDF reproducible.

Tests: 280 total (279 in the deploy gate), 29 Playwright journeys, including
the 23 mandatory security tests and an end-to-end onboarding journey.
MSG
git --no-pager log --oneline -1
git status --porcelain | head
