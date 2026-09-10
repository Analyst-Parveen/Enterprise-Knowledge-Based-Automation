#!/usr/bin/env bash
# verify.sh - Verify AWS infrastructure, services, application and AI pipeline.
#
# SAFETY: strictly READ-ONLY. Uses describe-*/list-*/get-* and application read
#         endpoints only. Never mutates. Never runs terraform apply or destroy.
#         See .claude/skills/verify/SKILL.md

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

REPORT="$(report_path verify)"
PASS=0; FAIL=0
API_URL="${API_URL:-http://localhost:8000}"

check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$name"; PASS=$((PASS+1)); return 0
  else err "$name"; FAIL=$((FAIL+1)); return 1; fi
}

skip() { warn "SKIP $1 ${2:+- $2}"; }

log "Verifying ${PROJECT_NAME} [${ENVIRONMENT}] (read-only)"

# ---------------------------------------------------------------------------
# 1. Identity and ownership
# ---------------------------------------------------------------------------
if [ "${VERIFY_AWS:-1}" = "1" ] && command -v aws >/dev/null 2>&1 \
   && [ -n "${EXPECTED_AWS_ACCOUNT_ID:-}" ]; then
  preflight_aws

  step "Resource ownership (ProjectCode=${PROJECT_CODE})"
  aws resourcegroupstaggingapi get-resources \
      --tag-filters "Key=ProjectCode,Values=${PROJECT_CODE}" \
      --query 'ResourceTagMappingList[].ResourceARN' --output text \
    | tr '\t' '\n' | sed '/^$/d' | sed 's/^/    /' || true
else
  skip "AWS checks" "EXPECTED_AWS_ACCOUNT_ID unset or aws CLI unavailable"
fi

# ---------------------------------------------------------------------------
# 2. Infrastructure (drift detection only - plan never applied here)
# ---------------------------------------------------------------------------
step "Infrastructure"
# TODO(phase-5): terraform plan -detailed-exitcode for drift; report, never apply.
#   S3 public access blocked / encrypted / versioned
#   DB + Qdrant in private subnets, no 0.0.0.0/0 ingress
#   IAM roles scoped, no unjustified wildcards
#   Secrets Manager secrets PRESENT (metadata only - never read a value)
skip "terraform drift + AWS posture" "Phase 5"

# ---------------------------------------------------------------------------
# 3. Services
# ---------------------------------------------------------------------------
step "Services"
# TODO(phase-1): PostgreSQL reachable + migrations at head
#                Qdrant reachable + collection dimension matches embedding model
#                Redis reachable (cache + rate limiting)
#                containers running non-root at expected image SHA
skip "PostgreSQL / Qdrant / Redis" "Phase 1"

# ---------------------------------------------------------------------------
# 4. Application
# ---------------------------------------------------------------------------
step "Application"
if command -v curl >/dev/null 2>&1; then
  check "health endpoint (${API_URL}/api/v1/health)" curl -fsS --max-time 10 "${API_URL}/api/v1/health" || true
else
  skip "health endpoint" "curl unavailable"
fi
# TODO(phase-1): unauthenticated request returns 401
#                security headers: HSTS, nosniff, X-Frame-Options, Referrer-Policy, CSP
#                CORS reflects allow-list only; trusted-host validation active
#                rate limits trip at 20 / 10 / 5 per minute
skip "auth, headers, CORS, rate limits" "Phase 1"

# ---------------------------------------------------------------------------
# 5. AI pipeline - one real smoke query against seeded data
# ---------------------------------------------------------------------------
step "AI pipeline"
# TODO(phase-2/3): assert the full response envelope is populated:
#   answer, citations, retrieved_chunks, model_used, input_tokens, output_tokens,
#   estimated_cost, latency_ms, cache_hit, tenant_id, confidence
# and that:
#   - citations resolve to chunks actually retrieved, in the caller's tenant
#   - a repeated query reports cache_hit: true
#   - a cross-tenant probe returns nothing from the other tenant
#   - Bedrock + Transcribe reachable; embedding dim matches the collection
#   - LangSmith traces carry the correlation ID
skip "RAG smoke query + tenant probe" "Phase 2/3"

# ---------------------------------------------------------------------------
# 6. Observability and cost
# ---------------------------------------------------------------------------
step "Observability and cost"
# TODO(phase-5): CloudWatch log groups receiving structured logs w/ correlation ID
#                metrics: requests, latency, tokens, cost, cache hits, security events
#                alarms incl. estimated spend
#                report currently running billable resources and cost at rest
skip "CloudWatch metrics, alarms, spend" "Phase 5"

# ---------------------------------------------------------------------------
# 7. Report
# ---------------------------------------------------------------------------
report_header "$REPORT" "Verification Report"
{
  printf '## Summary\n\n- Passed: %s\n- Failed: %s\n\n' "$PASS" "$FAIL"
  printf 'Verification is read-only. No resources were modified.\n\n'
  printf '## Note\n\nMany checks are placeholders until the corresponding phase lands.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

if [ "$FAIL" -gt 0 ]; then
  die "VERIFICATION FAILED: ${FAIL} check(s) failed - see ${REPORT}"
fi
log "Verification passed (${PASS} checks)."
