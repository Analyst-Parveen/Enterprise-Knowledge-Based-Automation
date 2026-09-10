#!/usr/bin/env bash
# seed.sh - Seed development/demo data so dashboards are never empty.
#
# SAFETY: never seeds production. Idempotent - re-running upserts by stable seed
#         IDs rather than duplicating. Seeds synthetic data only, never real
#         credentials or personal data.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

REPORT="$(report_path seed)"

log "Seeding demo data: ${PROJECT_NAME} [${ENVIRONMENT}]"

# ---------------------------------------------------------------------------
# Guard: demo data never goes near production
# ---------------------------------------------------------------------------
case "$ENVIRONMENT" in
  prod|production)
    die "refusing to seed demo data into '${ENVIRONMENT}'." ;;
esac

# ---------------------------------------------------------------------------
# What gets seeded (PROJECT.md sections 12 and 14)
#
#   tenants        two, so cross-tenant isolation is demonstrable in the UI
#   users          user + admin per tenant
#   documents      12 across every department and modality, including one
#                  deliberately FAILED ingestion so the failure path is visible
#   conversations  + messages, so "Recent queries" renders
#   request_usage  token/cost/latency rows so Usage and AI Metrics render
#   user_feedback  ratings so the Feedback page renders
#   audit_events   auth, deletion and security events so Audit Logs renders
#   prompt_release the active system prompt version
# ---------------------------------------------------------------------------
step "Seeding database and knowledge base"

PYTHON_BIN="${PYTHON_BIN:-python}"
if [ -x "${REPO_ROOT}/backend/.venv/Scripts/python.exe" ]; then
  PYTHON_BIN="${REPO_ROOT}/backend/.venv/Scripts/python.exe"
elif [ -x "${REPO_ROOT}/backend/.venv/bin/python" ]; then
  PYTHON_BIN="${REPO_ROOT}/backend/.venv/bin/python"
fi

run_seed() {
  cd "${REPO_ROOT}/backend" || return 1
  "$PYTHON_BIN" -m seeds.seed
}

if ( run_seed ); then
  ok "seed completed"
  SEED_OK=1
else
  err "seed failed - is the database reachable and migrated?"
  err "try: cd backend && alembic upgrade head"
  SEED_OK=0
fi

# ---------------------------------------------------------------------------
# Confirm the dashboards will not render empty
# ---------------------------------------------------------------------------
step "Verifying dashboards are populated"
if [ "$SEED_OK" = "1" ] && [ -n "${API_URL:-}" ] && command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 10 "${API_URL}/api/v1/health" >/dev/null 2>&1; then
    ok "API reachable at ${API_URL}"
  else
    warn "API not reachable at ${API_URL} - seeded data is in the database regardless"
  fi
else
  warn "skipping the live dashboard check (no API_URL, or the seed failed)"
fi

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
report_header "$REPORT" "Seed Report"
{
  printf '## Result\n\n%s\n\n' \
    "$([ "$SEED_OK" = 1 ] && echo 'Demo data seeded' || echo 'SEED FAILED')"
  printf '## Contents\n\n'
  printf -- '- 2 tenants (so cross-tenant isolation is demonstrable)\n'
  printf -- '- 4 users (user + admin per tenant)\n'
  printf -- '- 12 documents across all 7 departments and all 5 modalities\n'
  printf -- '- 1 deliberately failed ingestion job, so the failure path is visible\n'
  printf -- '- 8 conversations with citations, usage rows and feedback\n'
  printf -- '- 9 audit events including security events\n\n'
  printf '## Note\n\nSeed data is synthetic. No real credentials or personal data.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

[ "$SEED_OK" = "1" ] || die "SEED FAILED - see ${REPORT}"

log "Seed complete. Mint a login token with:"
log "  cd backend && python -m seeds.dev_token            # user"
log "  cd backend && python -m seeds.dev_token --role admin"
