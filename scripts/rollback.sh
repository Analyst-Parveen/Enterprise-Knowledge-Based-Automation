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

require_cmd aws

CLUSTER="$APP_NAME"
SERVICE="$APP_NAME"
DEPLOY_GROUP="${APP_NAME}-dg"
CONTAINER_NAME="api"

# image_tag_of TASK_DEF - the api container's image tag, or nothing.
image_tag_of() {
  local image
  image="$(aws ecs describe-task-definition --task-definition "$1" \
      --query "taskDefinition.containerDefinitions[?name=='${CONTAINER_NAME}'] | [0].image" \
      --output text 2>/dev/null | tr -d '\r')" || return 0
  case "$image" in *:*) printf '%s' "${image##*:}" ;; esac
}

# task_def_of_deployment ID - the task definition a deployment released. The
# AppSpec is a JSON string inside the response, so neither --query nor jq can
# reach into it; the ARN is matched out of the string instead.
task_def_of_deployment() {
  aws deploy get-deployment --deployment-id "$1" \
      --query 'deploymentInfo.revision.appSpecContent.content' --output text 2>/dev/null \
    | grep -oE 'arn:aws:ecs:[^"]+:task-definition/[^"]+' | head -1 || true
}

# The environment is ephemeral. After destroy.sh the CodeDeploy application and
# the ECS service are gone, so there is nothing to roll back to - and rebuilding
# is deploy.sh's job, never this script's.
step "Current deployment state"
aws deploy get-application --application-name "$APP_NAME" >/dev/null 2>&1 \
  || die "CodeDeploy application ${APP_NAME} does not exist - this environment is not deployed. Run ./scripts/deploy.sh. NOTHING was changed."

SERVICE_STATUS="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
    --query 'services[0].status' --output text 2>/dev/null | tr -d '\r')"
[ "$SERVICE_STATUS" = "ACTIVE" ] \
  || die "ECS service ${SERVICE} is '${SERVICE_STATUS:-missing}', not ACTIVE - run ./scripts/deploy.sh. NOTHING was changed."

aws deploy list-deployments --application-name "$APP_NAME" \
    --deployment-group-name "$DEPLOY_GROUP" --max-items 5 --output table 2>/dev/null \
  || warn "could not list deployments for ${APP_NAME}"

CURRENT_TASK_DEF="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
    --query 'services[0].taskSets[?status==`PRIMARY`] | [0].taskDefinition' --output text | tr -d '\r')"
case "$CURRENT_TASK_DEF" in
  ''|None) die "the service has no PRIMARY task set - nothing is serving traffic. Run ./scripts/deploy.sh. NOTHING was changed." ;;
esac
CURRENT_SHA="${CURRENT_SHA:-$(image_tag_of "$CURRENT_TASK_DEF")}"

# A deployment that is still in flight is rolled back by stopping it: CodeDeploy
# returns traffic to the task set that was already serving.
IN_FLIGHT="$(aws deploy list-deployments --application-name "$APP_NAME" \
    --deployment-group-name "$DEPLOY_GROUP" \
    --include-only-statuses Created Queued InProgress Baking Ready \
    --query 'deployments[0]' --output text 2>/dev/null | tr -d '\r')"
[ "$IN_FLIGHT" = "None" ] && IN_FLIGHT=""

TARGET_TASK_DEF=""
TARGET_SHA="${ROLLBACK_TO_SHA:-unknown}"

if [ -n "$IN_FLIGHT" ]; then
  warn "deployment ${IN_FLIGHT} is still in flight - stopping it returns traffic to the running version"
  TARGET_SHA="${CURRENT_SHA:-unknown}"
else
  # The newest SUCCEEDED deployment whose task definition is not the one serving
  # now, is still ACTIVE, and belongs to this project's own family.
  step "Resolving the previous known-good revision"
  for PAST_DEPLOY_ID in $(aws deploy list-deployments --application-name "$APP_NAME" \
      --deployment-group-name "$DEPLOY_GROUP" --include-only-statuses Succeeded \
      --query 'deployments[:10]' --output text 2>/dev/null | tr '\t' '\n'); do
    CANDIDATE="$(task_def_of_deployment "$PAST_DEPLOY_ID")"
    [ -n "$CANDIDATE" ] || continue
    [ "$CANDIDATE" != "$CURRENT_TASK_DEF" ] || continue
    case "$CANDIDATE" in
      *":task-definition/${APP_NAME}:"*) ;;
      *) warn "ignoring ${CANDIDATE} - not this project's task definition family"; continue ;;
    esac
    [ "$(aws ecs describe-task-definition --task-definition "$CANDIDATE" \
          --query 'taskDefinition.status' --output text 2>/dev/null | tr -d '\r')" = "ACTIVE" ] || continue

    CANDIDATE_SHA="$(image_tag_of "$CANDIDATE")"
    # ROLLBACK_TO_SHA pins the rollback to one image tag instead of the newest
    # previous revision.
    if [ -n "${ROLLBACK_TO_SHA:-}" ] && [ "$CANDIDATE_SHA" != "$ROLLBACK_TO_SHA" ]; then
      continue
    fi

    TARGET_TASK_DEF="$CANDIDATE"
    TARGET_SHA="${CANDIDATE_SHA:-unknown}"
    break
  done

  [ -n "$TARGET_TASK_DEF" ] \
    || die "no previous successful revision${ROLLBACK_TO_SHA:+ for image tag ${ROLLBACK_TO_SHA}} is available to roll back to. NOTHING was changed."
fi

log "current revision : ${CURRENT_TASK_DEF} (${CURRENT_SHA:-unknown})"
log "rollback target  : ${TARGET_TASK_DEF:-stop in-flight deployment ${IN_FLIGHT}} (${TARGET_SHA})"

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
if [ -n "$IN_FLIGHT" ]; then
  aws deploy stop-deployment --deployment-id "$IN_FLIGHT" --auto-rollback-enabled >/dev/null \
    || die "could not stop deployment ${IN_FLIGHT}. NOTHING was changed by this script."
  ROLLBACK_DEPLOY_ID="$IN_FLIGHT"
  ok "deployment ${IN_FLIGHT} stopped - CodeDeploy is keeping ${CURRENT_TASK_DEF} in service"
else
  CONTAINER_PORT="$(aws ecs describe-task-definition --task-definition "$TARGET_TASK_DEF" \
      --query "taskDefinition.containerDefinitions[?name=='${CONTAINER_NAME}'] | [0].portMappings[0].containerPort" \
      --output text 2>/dev/null | tr -d '\r')"
  case "$CONTAINER_PORT" in
    ''|None) die "target task definition has no ${CONTAINER_NAME} container port mapping - refusing to build an AppSpec by guesswork. NOTHING was changed." ;;
  esac

  # The same AppSpec shape deploy.sh releases with; only the task definition
  # differs. Blue-green still applies: CodeDeploy health-checks the previous
  # version on the test listener before any traffic moves back to it.
  APPSPEC="$(printf '{"version":0.0,"Resources":[{"TargetService":{"Type":"AWS::ECS::Service","Properties":{"TaskDefinition":"%s","LoadBalancerInfo":{"ContainerName":"%s","ContainerPort":%s}}}}]}' \
    "$TARGET_TASK_DEF" "$CONTAINER_NAME" "$CONTAINER_PORT")"

  ROLLBACK_DEPLOY_ID="$(aws deploy create-deployment \
      --application-name "$APP_NAME" \
      --deployment-group-name "$DEPLOY_GROUP" \
      --description "rollback to ${TARGET_SHA} via scripts/rollback.sh" \
      --revision "revisionType=AppSpecContent,appSpecContent={content='${APPSPEC}'}" \
      --query 'deploymentId' --output text | tr -d '\r')" \
    || die "could not start the rollback deployment. NOTHING was changed."

  log "rollback deployment ${ROLLBACK_DEPLOY_ID}"
  log "the previous version is health-checked on the test listener before traffic moves"

  if ! aws deploy wait deployment-successful --deployment-id "$ROLLBACK_DEPLOY_ID"; then
    err "the rollback deployment did not succeed"
    err "CodeDeploy auto-rollback leaves the currently healthy task set serving"
    err "no infrastructure, data, secrets or images were touched"
    die "ROLLBACK FAILED at the release stage - escalate to a human."
  fi
  ok "traffic shifted to ${TARGET_TASK_DEF}"
fi

# ---------------------------------------------------------------------------
# 4. Confirm recovery
# ---------------------------------------------------------------------------
step "Post-rollback verification"
# verify.sh defaults to localhost. Point it at the ALB so it checks what was
# just rolled back. Read from AWS rather than Terraform, so no init is needed.
ALB_DNS="$(aws elbv2 describe-load-balancers --names "${APP_NAME}-alb" \
    --query 'LoadBalancers[0].DNSName' --output text 2>/dev/null | tr -d '\r')"
case "$ALB_DNS" in
  ''|None) warn "could not read the ALB DNS name - verify.sh will use ${API_URL:-http://localhost:8000}" ;;
  *) export API_URL="http://${ALB_DNS}" ;;
esac

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
  printf '## Revisions\n\n'
  printf -- '- Rolled back from: `%s` (image tag `%s`)\n' "$CURRENT_TASK_DEF" "${CURRENT_SHA:-unknown}"
  if [ -n "$TARGET_TASK_DEF" ]; then
    printf -- '- Rolled back to: `%s` (image tag `%s`)\n' "$TARGET_TASK_DEF" "$TARGET_SHA"
  else
    printf -- '- In-flight deployment stopped with auto-rollback; the running version stayed in service\n'
  fi
  printf -- '- CodeDeploy deployment: `%s`\n\n' "${ROLLBACK_DEPLOY_ID:-none}"
  printf '## Scope\n\nApplication-level only. No infrastructure, data, or secrets were destroyed.\n\n'
  printf '## Follow-up\n\nFix the root cause and pass the local + CI gates before redeploying.\n'
  printf 'Redeploying the same broken build is not a remedy.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

[ "$ROLLBACK_OK" = "1" ] || die "ROLLBACK FAILED - see ${REPORT}"
log "Rollback complete. Fix the root cause before deploying again."
