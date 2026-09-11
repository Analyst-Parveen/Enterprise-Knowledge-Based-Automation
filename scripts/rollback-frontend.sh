#!/usr/bin/env bash
# rollback-frontend.sh - Put an earlier frontend build back live on Amplify.
#
# Usage:
#   ./scripts/rollback-frontend.sh             redeploy the last successful build before the live one
#   ./scripts/rollback-frontend.sh --to <sha>  redeploy a specific commit of the branch
#
# APPLICATION-LEVEL ONLY: it starts an Amplify RELEASE build of an older commit.
# It never touches Terraform, CloudFront, the backend, secrets or data, and it
# never runs terraform destroy.
#
# The next git push to the branch deploys the branch head again (auto-build).
# To make a rollback permanent, revert the commit in git and push.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

TO_SHA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --to)      [ $# -ge 2 ] || die "--to needs a commit SHA"; TO_SHA="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         die "unknown option: $1 (see --help)" ;;
  esac
done

FE_TF="${REPO_ROOT}/infra/terraform/envs/frontend"
APP_NAME="${PROJECT_CODE}-${ENVIRONMENT}-frontend"
BUILD_TIMEOUT_MIN="${FRONTEND_BUILD_TIMEOUT_MIN:-25}"
BRANCH="$(sed -n 's/^[[:space:]]*branch_name[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${FE_TF}/terraform.tfvars" 2>/dev/null | head -1)"
BRANCH="${BRANCH:-aws-deployment}"

log "Frontend rollback: ${APP_NAME} [${ENVIRONMENT}], branch ${BRANCH}"
preflight_aws
require_cmd curl git

# ---------------------------------------------------------------------------
# 1. The app, the live build, and the target
# ---------------------------------------------------------------------------
step "Current state"
APP_IDS="$(aws amplify list-apps --query "apps[?name=='${APP_NAME}'].appId" --output text)" \
  || die "could not list Amplify apps"
set -- $APP_IDS
[ $# -eq 1 ] || die "expected exactly one Amplify app named ${APP_NAME}, found $#"
APP_ID="$1"
SITE_URL="https://${BRANCH}.$(aws amplify get-app --app-id "$APP_ID" --query 'app.defaultDomain' --output text)"

IN_FLIGHT="$(aws amplify list-jobs --app-id "$APP_ID" --branch-name "$BRANCH" --max-items 10 \
  --query "jobSummaries[?status=='PENDING' || status=='PROVISIONING' || status=='RUNNING'] | [0].jobId" \
  --output text)"
[ -z "$IN_FLIGHT" ] || [ "$IN_FLIGHT" = "None" ] \
  || die "build ${IN_FLIGHT} is still running - wait for it to finish, then roll back"

# Successful builds, newest first: commit <TAB> job id
OK_BUILDS="$(aws amplify list-jobs --app-id "$APP_ID" --branch-name "$BRANCH" --max-items 50 \
  --query "jobSummaries[?status=='SUCCEED'].[commitId,jobId]" --output text)"
[ -n "$OK_BUILDS" ] || die "no successful build of ${BRANCH} exists - nothing to roll back to"
LIVE_COMMIT="$(printf '%s\n' "$OK_BUILDS" | head -1 | cut -f1)"
ok "live build: ${LIVE_COMMIT:0:7}"

if [ -n "$TO_SHA" ]; then
  # Accept a short SHA: resolve it locally, or against the commits Amplify built.
  TARGET="$(git -C "$REPO_ROOT" rev-parse --verify --quiet "${TO_SHA}^{commit}" 2>/dev/null || true)"
  [ -n "$TARGET" ] || TARGET="$(printf '%s\n' "$OK_BUILDS" | cut -f1 | grep -m1 "^${TO_SHA}" || true)"
  [ -n "$TARGET" ] || die "cannot resolve ${TO_SHA} to a full commit (git fetch, or use a SHA Amplify has built)"
else
  TARGET="$(printf '%s\n' "$OK_BUILDS" | cut -f1 | grep -v -m1 "^${LIVE_COMMIT}$" || true)"
  [ -n "$TARGET" ] || die "no earlier successful build with a different commit - nothing to roll back to"
fi
[ "$TARGET" != "$LIVE_COMMIT" ] || die "${TARGET:0:7} is already the live build"

SUBJECT="$(git -C "$REPO_ROOT" log -1 --format='%s' "$TARGET" 2>/dev/null || echo 'commit not in the local clone')"
log "rollback target: ${TARGET:0:7}  ${SUBJECT}"
warn "The next push to ${BRANCH} redeploys the branch head. Revert in git to make this permanent."

if [ "${ROLLBACK_ASSUME_YES:-0}" != "1" ]; then
  confirm_phrase "ROLLBACK FRONTEND ${ENVIRONMENT}"
fi

# ---------------------------------------------------------------------------
# 2. Rebuild the target commit
# ---------------------------------------------------------------------------
step "Amplify build of ${TARGET:0:7}"
JOB_ID="$(aws amplify start-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-type RELEASE \
  --commit-id "$TARGET" --commit-message "rollback to ${TARGET:0:7}" \
  --job-reason "rollback-frontend.sh" --query 'jobSummary.jobId' --output text)" \
  || die "could not start the rollback build"

STATUS=""; LAST=""
DEADLINE=$(( $(date +%s) + BUILD_TIMEOUT_MIN * 60 ))
while :; do
  STATUS="$(aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" \
              --query 'job.summary.status' --output text 2>/dev/null || echo UNKNOWN)"
  [ "$STATUS" != "$LAST" ] && log "job ${JOB_ID}: ${STATUS}" && LAST="$STATUS"
  case "$STATUS" in SUCCEED|FAILED|CANCELLED) break ;; esac
  [ "$(date +%s)" -lt "$DEADLINE" ] || { STATUS="TIMEOUT"; break; }
  sleep 15
done

# ---------------------------------------------------------------------------
# 3. Confirm and report
# ---------------------------------------------------------------------------
SITE_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "${SITE_URL}/" || true)"
ROLLBACK_OK=0
[ "$STATUS" = "SUCCEED" ] && [ "$SITE_CODE" = "200" ] && ROLLBACK_OK=1

REPORT="$(report_path frontend-rollback)"
report_header "$REPORT" "Frontend Rollback Report"
{
  printf '## Result\n\n%s\n\n' "$([ "$ROLLBACK_OK" = 1 ] && echo 'Rollback completed and verified' || echo 'ROLLBACK FAILED')"
  printf '| Item | Value |\n|---|---|\n'
  printf '| Amplify app / branch | %s / %s |\n' "$APP_ID" "$BRANCH"
  printf '| Rolled back from | %s |\n' "$LIVE_COMMIT"
  printf '| Rolled back to | %s |\n' "$TARGET"
  printf '| Amplify job | %s (%s) |\n' "$JOB_ID" "$STATUS"
  printf '| Site | %s -> HTTP %s |\n\n' "$SITE_URL" "$SITE_CODE"
  printf '## Scope\n\nApplication-level only: an Amplify rebuild of an older commit. No infrastructure, backend, data or secrets were changed.\n\n'
  printf 'The next push to `%s` redeploys the branch head; revert in git to make this permanent.\n' "$BRANCH"
} >> "$REPORT"
ok "report written: ${REPORT}"

[ "$ROLLBACK_OK" = 1 ] || die "ROLLBACK FAILED (job ${STATUS}, site HTTP ${SITE_CODE}) - see ${REPORT}"
log "Frontend rolled back to ${TARGET:0:7}: ${SITE_URL}"
