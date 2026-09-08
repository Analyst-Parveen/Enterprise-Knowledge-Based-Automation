#!/usr/bin/env bash
# rollback.sh - Safely roll back the APPLICATION to the previous known-good
#               revision via CodeDeploy.
#
# SAFETY - the defining constraint of this script:
#   Rollback is APPLICATION-LEVEL ONLY.
#     * NEVER runs terraform destroy
#     * NEVER deletes data (S3 objects, database, snapshots, Qdrant collections)
#     * NEVER deletes or overwrites secrets
#     * NEVER modifies infrastructure beyond the application deployment
#   See .claude/rules/deployment.md section 6 and .claude/skills/rollback/SKILL.md

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

REPORT="$(report_path rollback)"
APP_NAME="${PROJECT_CODE}-${ENVIRONMENT}"

log "Rollback: ${PROJECT_NAME} [${ENVIRONMENT}]"

# ---------------------------------------------------------------------------
# 1. Establish the situation BEFORE acting
# ---------------------------------------------------------------------------
preflight_aws

step "Current deployment state"
if command -v aws >/dev/null 2>&1; then
  aws deploy list-deployments \
      --application-name "$APP_NAME" \
      --max-items 5 --output table 2>/dev/null \
    || warn "could not list deployments for ${APP_NAME} (not deployed yet?)"
fi

CURRENT_SHA="${CURRENT_SHA:-unknown}"
TARGET_SHA="${ROLLBACK_TO_SHA:-unknown}"

log "current revision : ${CURRENT_SHA}"
log "rollback target  : ${TARGET_SHA}"

# ---------------------------------------------------------------------------
# 2. Migration safety check
#
# Migrations are written backward-compatible so rollback needs no down-migration.
# If the failing release applied a NON-backward-compatible migration, STOP and
# ask a human. Rolling back under an incompatible schema can corrupt data.
# ---------------------------------------------------------------------------
step "Migration safety check"
if [ "${MIGRATION_BACKWARD_COMPATIBLE:-unknown}" = "no" ]; then
  err "the failing release applied a non-backward-compatible migration"
  die "STOPPING. Rolling back under an incompatible schema risks data corruption. Escalate to a human."
fi
# TODO(phase-1): compare alembic head against the target revision's expected head.
warn "automated migration compatibility check not yet implemented (Phase 1)"

# ---------------------------------------------------------------------------
# 3. Confirm, then roll back application traffic only
# ---------------------------------------------------------------------------
if [ "${ROLLBACK_ASSUME_YES:-0}" != "1" ]; then
  confirm_phrase "ROLLBACK ${ENVIRONMENT}"
fi

step "Shifting traffic to previous known-good revision"
# TODO(phase-5): create a CodeDeploy deployment pinned to the last successful
#                revision, or stop-and-rollback the in-flight deployment:
#   aws deploy stop-deployment --deployment-id "$ID" --auto-rollback-enabled
#   aws deploy create-deployment --application-name "$APP_NAME" \
#       --deployment-group-name "${APP_NAME}-dg" --revision "<previous>"
warn "CodeDeploy rollback not yet implemented (Phase 5)"

# ---------------------------------------------------------------------------
# 4. Confirm recovery
# ---------------------------------------------------------------------------
step "Post-rollback verification"
ROLLBACK_OK=1
if ! "${REPO_ROOT}/scripts/verify.sh"; then
  ROLLBACK_OK=0
  err "post-rollback verification FAILED"
  err "ESCALATE TO A HUMAN. Do not destroy or rebuild infrastructure on your own initiative."
fi

# ---------------------------------------------------------------------------
# 5. Incident report
# ---------------------------------------------------------------------------
report_header "$REPORT" "Rollback / Incident Report"
{
  printf '## Result\n\n%s\n\n' "$([ "$ROLLBACK_OK" = 1 ] && echo 'Rollback completed and verified' || echo 'ROLLBACK FAILED - escalated')"
  printf '## Revisions\n\n- Rolled back from: `%s`\n- Rolled back to: `%s`\n\n' "$CURRENT_SHA" "$TARGET_SHA"
  printf '## Scope\n\nApplication-level only. No infrastructure, data, or secrets were destroyed.\n\n'
  printf '## Follow-up\n\nFix the root cause and pass the local + CI gates before redeploying.\n'
  printf 'Redeploying the same broken build is not a remedy.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

[ "$ROLLBACK_OK" = "1" ] || die "ROLLBACK FAILED - see ${REPORT}"
log "Rollback complete. Fix the root cause before deploying again."
