#!/usr/bin/env bash
# test-e2e.sh - Run complete end-to-end tests across frontend, API, RAG pipeline
#               and agentic workflows.
#
# SAFETY: never targets production or shared environments. Never runs terraform
#         destroy. Never weakens or skips a failing test to go green.
#         See .claude/skills/e2e-testing/SKILL.md

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

REPORT="$(report_path e2e)"
PASS=0; FAIL=0

run_suite() {
  local name="$1"; shift
  step "$name"
  if "$@"; then ok "$name"; PASS=$((PASS+1))
  else err "$name"; FAIL=$((FAIL+1)); fi
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

# ---------------------------------------------------------------------------
# Backend suites
# ---------------------------------------------------------------------------
if command -v pytest >/dev/null 2>&1 && [ -d "${REPO_ROOT}/backend/tests" ]; then
  # Unit/integration mock the model. Real AI calls belong to evaluation + smoke only.
  run_suite "integration tests" \
    pytest "${REPO_ROOT}/backend/tests/integration" -q || true

  # Release-blocking: tenant isolation, injection, guardrails, authz, rate limits
  run_suite "security tests" \
    pytest "${REPO_ROOT}/backend/tests/security" -q || true
else
  skip "pytest suites" "backend tests not present yet (Phase 1+)"
fi

# ---------------------------------------------------------------------------
# Frontend journeys (Playwright)
#
# Required journeys - see .claude/skills/e2e-testing/SKILL.md step 2:
#   auth (4) | document lifecycle (6) | knowledge chat (4)
#   tenant isolation (1, release-blocking) | agentic workflow (1)
#   dashboards user+admin (3) | resilience: rate limit + correlation ID (2)
# ---------------------------------------------------------------------------
if [ -f "${REPO_ROOT}/frontend/playwright.config.ts" ]; then
  run_suite "playwright e2e" \
    npx --prefix "${REPO_ROOT}/frontend" playwright test || true
else
  skip "playwright journeys" "frontend not scaffolded yet (Phase 4)"
fi

# ---------------------------------------------------------------------------
# Cleanup - remove test-created data, keep seeded demo data intact
# ---------------------------------------------------------------------------
step "Cleanup"
# TODO(phase-4): delete test tenants/users/documents; confirm no orphaned
#                S3 objects or Qdrant points. Never delete seeded demo data.
skip "test data cleanup" "Phase 4"

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
report_header "$REPORT" "End-to-End Test Report"
{
  printf '## Summary\n\n- Suites passed: %s\n- Suites failed: %s\n\n' "$PASS" "$FAIL"
  printf '## Note\n\nA partially passing run is not a passing run.\n'
  printf 'Tenant-isolation failures are release-blocking.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

if [ "$FAIL" -gt 0 ]; then
  die "E2E FAILED: ${FAIL} suite(s) failed - see ${REPORT}"
fi
log "E2E passed (${PASS} suites)."
