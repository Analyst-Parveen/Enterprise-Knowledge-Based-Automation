#!/usr/bin/env bash
# test-e2e.sh - Run complete end-to-end tests across frontend, API, RAG pipeline
#               and agentic workflows.
#
# SAFETY: never targets production. Never runs terraform destroy. Never weakens
#         or skips a failing test to go green.
#         See .claude/skills/e2e-testing/SKILL.md

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

REPORT="$(report_path e2e)"
PASS=0
FAIL=0
declare -a RESULTS=()

record() {
  local name="$1" status="$2"
  RESULTS+=("${status}|${name}")
  if [ "$status" = "PASS" ]; then PASS=$((PASS + 1)); ok "$name"
  else FAIL=$((FAIL + 1)); err "$name"; fi
}

skip() { warn "SKIP $1 ${2:+- $2}"; }

log "E2E tests: ${PROJECT_NAME} [${ENVIRONMENT}]"

# ---------------------------------------------------------------------------
# Guard: never run E2E against production
# ---------------------------------------------------------------------------
case "$ENVIRONMENT" in
  prod|production)
    die "refusing to run E2E against '${ENVIRONMENT}'. Use the ephemeral demo environment." ;;
esac

PYTHON_BIN="${PYTHON_BIN:-python}"
if [ -x "${REPO_ROOT}/backend/.venv/Scripts/python.exe" ]; then
  PYTHON_BIN="${REPO_ROOT}/backend/.venv/Scripts/python.exe"
elif [ -x "${REPO_ROOT}/backend/.venv/bin/python" ]; then
  PYTHON_BIN="${REPO_ROOT}/backend/.venv/bin/python"
fi

export ENVIRONMENT AI_PROVIDER="${AI_PROVIDER:-local}" DEV_AUTH_ENABLED="${DEV_AUTH_ENABLED:-true}"

# ---------------------------------------------------------------------------
# Backend suites
# ---------------------------------------------------------------------------
if [ -d "${REPO_ROOT}/backend/tests" ]; then
  step "Integration tests"
  if ( cd "${REPO_ROOT}/backend" && "$PYTHON_BIN" -m pytest tests/integration -q ); then
    record "integration tests" PASS
  else
    record "integration tests" FAIL
  fi

  # Release-blocking: tenant isolation, injection, guardrails, authz, uploads.
  step "Security tests (release-blocking)"
  if ( cd "${REPO_ROOT}/backend" && "$PYTHON_BIN" -m pytest tests/security -q ); then
    record "security tests" PASS
  else
    record "security tests" FAIL
    err "SECURITY TESTS FAILED - this blocks release regardless of anything else"
  fi

  step "Evaluation harness"
  if ( cd "${REPO_ROOT}/backend" && "$PYTHON_BIN" -m pytest tests/evaluation -q ); then
    record "evaluation harness" PASS
  else
    record "evaluation harness" FAIL
  fi

  # The onboarding journey drives the real hierarchy against a real database:
  # platform operator -> two companies -> their admins -> their users, then
  # proves each company is invisible to the other. It needs PostgreSQL, so it
  # is reported as skipped rather than failed when the stack is not up.
  step "Onboarding hierarchy journey (needs PostgreSQL)"
  if ( cd "${REPO_ROOT}/backend" && "$PYTHON_BIN" -m pytest tests/e2e -q ); then
    record "onboarding hierarchy journey" PASS
  else
    if ( cd "${REPO_ROOT}/backend" && "$PYTHON_BIN" -c "
import asyncio, sys
from app.db.session import engine
from sqlalchemy import text
async def main():
    async with engine.connect() as conn:
        await conn.execute(text('select 1'))
asyncio.run(main())
" >/dev/null 2>&1 ); then
      record "onboarding hierarchy journey" FAIL
    else
      skip "onboarding hierarchy journey" "PostgreSQL is not reachable - start docker compose"
    fi
  fi
else
  skip "pytest suites" "backend/tests not found"
fi

# ---------------------------------------------------------------------------
# Frontend journeys (Playwright)
#
# Journeys: auth (5, incl. the password gate and recovery) | dashboard | chat
# with the full envelope | documents | departments | workflows | feedback |
# non-admin blocked from admin and platform | sign out | admin: nav, metrics,
# audit, security, deployments, own-tenant user management | platform: company
# registry, onboarding, trail, and exclusion from a company's user directory
# ---------------------------------------------------------------------------
if [ -f "${REPO_ROOT}/frontend/playwright.config.ts" ] && [ -d "${REPO_ROOT}/frontend/node_modules" ]; then
  step "Playwright end-to-end journeys"

  # Mint tokens so the authenticated journeys actually run.
  if [ -z "${E2E_USER_TOKEN:-}" ]; then
    E2E_USER_TOKEN="$( ( cd "${REPO_ROOT}/backend" && "$PYTHON_BIN" -m seeds.dev_token 2>/dev/null ) || echo "")"
    export E2E_USER_TOKEN
  fi
  if [ -z "${E2E_ADMIN_TOKEN:-}" ]; then
    E2E_ADMIN_TOKEN="$( ( cd "${REPO_ROOT}/backend" && "$PYTHON_BIN" -m seeds.dev_token --role admin --user seed-admin-a 2>/dev/null ) || echo "")"
    export E2E_ADMIN_TOKEN
  fi
  # The platform operator lives in the reserved 'platform' tenant; dev_token
  # pairs the tenant with the role so the two can never disagree.
  if [ -z "${E2E_PLATFORM_TOKEN:-}" ]; then
    E2E_PLATFORM_TOKEN="$( ( cd "${REPO_ROOT}/backend" && "$PYTHON_BIN" -m seeds.dev_token --role platform_admin 2>/dev/null ) || echo "")"
    export E2E_PLATFORM_TOKEN
  fi

  [ -n "$E2E_USER_TOKEN" ] || warn "no user token - authenticated journeys will be skipped"

  if ( cd "${REPO_ROOT}/frontend" && npx playwright test ); then
    record "playwright journeys" PASS
  else
    record "playwright journeys" FAIL
  fi
else
  skip "playwright journeys" "run 'npm install' and 'npx playwright install chromium' in frontend/"
fi

# ---------------------------------------------------------------------------
# Cleanup - remove test-created data, keep seeded demo data intact
# ---------------------------------------------------------------------------
step "Cleanup"
# Playwright journeys create only feedback rows, which are harmless and useful
# to see in the UI. Nothing to remove. Seeded demo data is never deleted.
ok "no orphaned test data to clean up"

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
report_header "$REPORT" "End-to-End Test Report"
{
  printf '## Summary\n\n- Suites passed: %s\n- Suites failed: %s\n\n' "$PASS" "$FAIL"
  printf '## Suites\n\n| Suite | Result |\n|---|---|\n'
  for entry in "${RESULTS[@]}"; do
    printf '| %s | %s |\n' "${entry#*|}" "${entry%%|*}"
  done
  printf '\n## Note\n\nA partially passing run is not a passing run.\n'
  printf 'Tenant-isolation failures are release-blocking.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

if [ "$FAIL" -gt 0 ]; then
  die "E2E FAILED: ${FAIL} suite(s) failed - see ${REPORT}"
fi
log "E2E passed (${PASS} suites)."
