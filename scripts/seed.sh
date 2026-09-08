#!/usr/bin/env bash
# seed.sh - Seed development/demo data so dashboards are never empty.
#
# SAFETY: never seeds production. Never overwrites real user data. Idempotent -
#         re-running must not duplicate records. Never seeds real credentials.

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
#   tenants        two tenants, so cross-tenant isolation is demonstrable
#   departments    HR, Finance, Legal, Sales, Marketing, Operations, Technical
#   users          user + admin per tenant (Cognito test users; passwords from
#                  config/Secrets Manager - NEVER hardcoded here)
#   documents      a spread of modalities: PDF policy, DOCX SOP, XLSX/CSV table,
#                  diagram image, short audio clip - enough for real retrieval
#   ingestion_jobs completed jobs with chunk counts, plus one failed job so the
#                  failure path is visible in the UI
#   conversations  + messages, so "Recent queries" renders
#   request_usage  token/cost/latency rows so Usage and AI Metrics render
#   user_feedback  a few ratings so the Feedback page renders
#   prompt_releases the current prompt version
#   audit_events   auth, deletion and security events so Audit Logs renders
#
# Sample documents live in backend/seeds/. They must be synthetic - no real
# company policies, no real personal data.
# ---------------------------------------------------------------------------

step "Seeding database and knowledge base"
# TODO(phase-1): python -m backend.seeds.seed --environment "$ENVIRONMENT"
#   - idempotent upserts keyed by stable seed IDs
#   - every row carries a tenant_id
#   - documents ingested through the REAL ingestion pipeline so chunks,
#     embeddings and citations are genuine (not fabricated rows)
warn "seeding not yet implemented - backend does not exist until Phase 1"

step "Verifying dashboards are populated"
# TODO(phase-4): assert non-empty counts for documents, conversations,
#                request_usage, audit_events per tenant.
warn "dashboard population check not yet implemented (Phase 4)"

report_header "$REPORT" "Seed Report"
{
  printf '## Result\n\nSeed script executed for environment `%s`.\n\n' "$ENVIRONMENT"
  printf '## Note\n\nSeed data is synthetic. No real credentials or personal data.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"
log "Seed complete."
