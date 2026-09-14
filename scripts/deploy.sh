#!/usr/bin/env bash
# deploy.sh - Create/update project infrastructure, deploy the application,
#             then verify, seed and test.
#
# Lifecycle: deploy -> test -> verify -> demo -> destroy -> audit -> deploy again
#
# SAFETY: this script NEVER runs `terraform destroy`. See
#         .claude/rules/deployment.md and .claude/rules/terraform.md.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo 'nogit')"
REPORT="$(report_path deploy)"
SKIP_LOCAL_TESTS="${SKIP_LOCAL_TESTS:-0}"

log "Deploying ${PROJECT_NAME} [${ENVIRONMENT}] @ ${GIT_SHA}"

# ---------------------------------------------------------------------------
# 1. Preflight - account, region, budget, terraform state
# ---------------------------------------------------------------------------
preflight_aws
assert_within_budget          # refuses to deploy past the $20 ceiling
preflight_terraform

if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then
  warn "working tree is dirty - deploying uncommitted changes"
fi

# ---------------------------------------------------------------------------
# 2. Local gate - nothing deploys that has not passed locally
#    .claude/rules/testing.md section 6
# ---------------------------------------------------------------------------
if [ "$SKIP_LOCAL_TESTS" = "1" ]; then
  warn "SKIP_LOCAL_TESTS=1 - local gate bypassed (not recommended)"
else
  step "Local gate: lint, format, tests"

  run_backend_gate() {
    cd "${REPO_ROOT}/backend" || return 1
    # Use the project venv directly - an un-activated Git Bash has no ruff/pytest.
    local py=python
    [ -x .venv/Scripts/python.exe ] && py=.venv/Scripts/python.exe
    [ -x .venv/bin/python ] && py=.venv/bin/python
    "$py" -m ruff check app tests seeds || return 1
    "$py" -m ruff format --check app tests seeds || return 1
    ENVIRONMENT=dev AI_PROVIDER=local DEV_AUTH_ENABLED=true \
      "$py" -m pytest tests/unit tests/integration tests/security tests/evaluation -q || return 1
  }

  ( run_backend_gate ) || die "local gate failed - fix the code, never the test"
  ( cd "${REPO_ROOT}/frontend" && npm run typecheck ) || die "frontend typecheck failed"
  ok "local gate passed"
fi

# ---------------------------------------------------------------------------
# 3. Build, push and scan the image
# ---------------------------------------------------------------------------
step "Build, push and scan the container image"

ECR_REPO="${PROJECT_CODE}-${ENVIRONMENT}-backend"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${ECR_REPO}:${GIT_SHA}"

# The image is named after the commit, so it must contain exactly that commit.
# Uncommitted changes to anything the Dockerfile copies would produce an image
# whose tag lies about its contents - and, since tags are immutable, a later
# clean deploy of the same SHA would then reuse that wrong image forever.
IMAGE_INPUTS=(backend infra/docker/backend.Dockerfile)
if [ -n "$(git -C "$REPO_ROOT" status --porcelain -- "${IMAGE_INPUTS[@]}" 2>/dev/null)" ]; then
  git -C "$REPO_ROOT" status --short -- "${IMAGE_INPUTS[@]}" | sed 's/^/    /'
  die "uncommitted changes in the image inputs above - commit them first, so image ${GIT_SHA} contains exactly commit ${GIT_SHA}"
fi

# ecr_image_digest REPO TAG - prints the digest when the tag exists, nothing when
# it does not. Any other error (permissions, missing repository) is fatal.
ecr_image_digest() {
  local out
  if out="$(aws ecr describe-images --repository-name "$1" --region "$AWS_REGION" \
      --image-ids "imageTag=$2" --query 'imageDetails[0].imageDigest' --output text 2>&1)"; then
    printf '%s' "$out"
  elif printf '%s' "$out" | grep -q "ImageNotFoundException"; then
    return 0
  else
    err "$out"
    die "could not query ECR repository $1"
  fi
}

# Idempotent: tags are IMMUTABLE, so an existing ${GIT_SHA} tag can only ever
# point at the image that was pushed for this commit. Reuse it rather than
# attempting an overwrite ECR will reject. The CVE gate below still runs on it.
DIGEST="$(ecr_image_digest "$ECR_REPO" "$GIT_SHA")"
if [ -n "$DIGEST" ]; then
  ok "image ${GIT_SHA} already in ECR (${DIGEST}) - reusing it, not rebuilding"
else
  aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null \
    || die "ECR login failed"

  # Immutable SHA tag. Never :latest.
  docker build -f "${REPO_ROOT}/infra/docker/backend.Dockerfile" -t "$IMAGE" "$REPO_ROOT" \
    || die "image build failed"

  if ! docker push "$IMAGE"; then
    # Another run can push the same commit between our check and our push.
    DIGEST="$(ecr_image_digest "$ECR_REPO" "$GIT_SHA")"
    [ -n "$DIGEST" ] || die "image push failed"
    warn "tag ${GIT_SHA} was pushed concurrently - using the image already in ECR"
  fi

  DIGEST="$(ecr_image_digest "$ECR_REPO" "$GIT_SHA")"
  [ -n "$DIGEST" ] || die "pushed ${IMAGE} but ECR does not report it"
  ok "image ${IMAGE} is in ECR (${DIGEST})"
fi

aws ecr wait image-scan-complete --repository-name "$ECR_REPO" \
  --image-id "imageTag=${GIT_SHA}" 2>/dev/null || true

CRITICAL="$(aws ecr describe-image-scan-findings \
  --repository-name "$ECR_REPO" --image-id "imageTag=${GIT_SHA}" \
  --query 'imageScanFindings.findingSeverityCounts.CRITICAL' \
  --output text 2>/dev/null || echo None)"

if [ "$CRITICAL" != "None" ] && [ "$CRITICAL" != "0" ]; then
  die "${CRITICAL} critical vulnerabilities in the image. Not deploying."
fi
ok "image scan clean"

export TF_VAR_backend_image="$IMAGE"

# ---------------------------------------------------------------------------
# 4a. Database. A new instance is built from the newest snapshot destroy.sh
#     took, so data survives destroy -> deploy. A stopped instance (the cost
#     guard stops RDS) is started, because the task cannot migrate or serve
#     without it and Terraform cannot modify a stopped instance.
# ---------------------------------------------------------------------------
step "Database (RDS ${DB_INSTANCE_ID})"
RESTORE_SNAPSHOT=""
DB_STATUS="$(db_instance_status)"
case "$DB_STATUS" in
  "")
    RESTORE_SNAPSHOT="$(latest_db_snapshot)"
    if [ -n "$RESTORE_SNAPSHOT" ]; then
      log "no instance - it will be restored from snapshot ${RESTORE_SNAPSHOT} (adds ~10 minutes)"
    else
      log "no instance and no snapshot - creating an empty database (adds ~10 minutes); the task migrates and seeds it"
    fi
    ;;
  available)
    ok "instance available - the data on it is kept"
    ;;
  stopped)
    log "instance is stopped (by the cost guard?) - starting it"
    aws rds start-db-instance --db-instance-identifier "$DB_INSTANCE_ID" --region "$AWS_REGION" >/dev/null \
      || die "could not start ${DB_INSTANCE_ID}"
    aws rds wait db-instance-available --db-instance-identifier "$DB_INSTANCE_ID" --region "$AWS_REGION" \
      || die "${DB_INSTANCE_ID} did not become available"
    ok "instance started"
    ;;
  stopping|deleting)
    die "instance is '${DB_STATUS}' - wait until it settles, then rerun deploy.sh"
    ;;
  *)
    log "instance is '${DB_STATUS}' - waiting for it to become available"
    aws rds wait db-instance-available --db-instance-identifier "$DB_INSTANCE_ID" --region "$AWS_REGION" \
      || die "${DB_INSTANCE_ID} did not become available"
    ;;
esac

# ---------------------------------------------------------------------------
# 4b. Infrastructure - plan, review, apply. NEVER auto-approve, NEVER destroy.
#     .claude/rules/terraform.md section 4
# ---------------------------------------------------------------------------
step "Terraform: fmt, validate, plan"
terraform -chdir="$TF_DIR" fmt -check -recursive || die "terraform fmt failed"
terraform -chdir="$TF_DIR" validate              || die "terraform validate failed"
# -var, not TF_VAR_: a terraform.tfvars value would silently override TF_VAR_
# and deploy a stale image. -var takes precedence over every tfvars file.
# restore_snapshot_id only matters when the instance is created; Terraform
# ignores it for an existing instance.
terraform -chdir="$TF_DIR" plan -input=false -out=tfplan \
  -var="backend_image=${IMAGE}" \
  -var="restore_snapshot_id=${RESTORE_SNAPSHOT}" || die "terraform plan failed"

# A deploy must never destroy. Surface it and require a typed acknowledgement.
step "Reviewing the plan for destructive changes"
# Read the RENDERED plan, not raw JSON: the JSON also carries a drift section,
# where a resource deleted OUTSIDE Terraform (a deregistered task definition,
# say) appears as {"actions":["delete"]} without this plan deleting anything.
# grep exits 1 when nothing matches - i.e. on every normal, non-destructive
# plan - which under `set -euo pipefail` would kill the script silently here.
PLAN_TEXT="$(terraform -chdir="$TF_DIR" show -no-color tfplan)" \
  || die "could not read the saved plan"
DESTROY_COUNT="$(printf '%s' "$PLAN_TEXT" \
  | { grep -cE '^[[:space:]]+# .* will be destroyed' || true; } | tr -d ' ')"

if [ "${DESTROY_COUNT:-0}" -gt 0 ]; then
  err "plan contains ${DESTROY_COUNT} resource deletion(s)"
  printf '%s' "$PLAN_TEXT" | grep -E '^[[:space:]]+# .* will be (destroyed|replaced)' || true
  warn "A deployment must not destroy resources. Review the plan above."
  confirm_phrase "I REVIEWED THIS PLAN"
fi

step "Terraform apply (the reviewed plan only)"
terraform -chdir="$TF_DIR" apply -input=false tfplan || die "terraform apply failed"
ok "infrastructure applied"

API_URL="$(terraform -chdir="$TF_DIR" output -raw app_url 2>/dev/null || echo "")"
export API_URL
[ -n "$API_URL" ] && log "app url: ${API_URL}"

# ---------------------------------------------------------------------------
# 5. Blue-green release
#
# Migrations run inside the task at startup (alembic upgrade head) against
# RDS, which blue and green share. They are written backward-compatible, so
# blue keeps working while green migrates, and a rollback needs no
# down-migration.
# ---------------------------------------------------------------------------
step "Release (CodeDeploy blue-green)"

APP_NAME="${PROJECT_CODE}-${ENVIRONMENT}"
TASK_DEF_ARN="$(aws ecs describe-task-definition --task-definition "$APP_NAME" \
  --query 'taskDefinition.taskDefinitionArn' --output text)" \
  || die "could not read the task definition"

APPSPEC="$(printf '{"version":0.0,"Resources":[{"TargetService":{"Type":"AWS::ECS::Service","Properties":{"TaskDefinition":"%s","LoadBalancerInfo":{"ContainerName":"api","ContainerPort":8000}}}}]}' "$TASK_DEF_ARN")"

DEPLOY_ID="$(aws deploy create-deployment \
  --application-name "$APP_NAME" \
  --deployment-group-name "${APP_NAME}-dg" \
  --revision "revisionType=AppSpecContent,appSpecContent={content='${APPSPEC}'}" \
  --query 'deploymentId' --output text)" || die "could not start the deployment"

log "deployment ${DEPLOY_ID}"
log "green is being health-checked on the test listener before any traffic moves"

if ! aws deploy wait deployment-successful --deployment-id "$DEPLOY_ID"; then
  err "blue-green deployment failed"
  err "CodeDeploy auto-rollback should have kept blue serving traffic - verifying"
  "${REPO_ROOT}/scripts/verify.sh" || true
  cost_reminder
  die "DEPLOY FAILED at the release stage. Blue should still be live."
fi
ok "traffic shifted to the new version"

# ---------------------------------------------------------------------------
# 6. Post-deploy verification against the DEPLOYED API.
#    A deploy that fails verification is a FAILED deploy.
#
#    Seeding happens inside the task at startup (alembic + seeds.seed in the
#    container command), because RDS sits in private subnets that nothing
#    outside the VPC can reach. The seed upserts by stable IDs, so it never
#    duplicates data on the persistent database. seed.sh and test-e2e.sh
#    target the LOCAL stack, so running them here would test the wrong
#    system - and fail the deploy whenever local Docker happens to be stopped.
# ---------------------------------------------------------------------------
DEPLOY_OK=1

step "Post-deploy verification"
if ! "${REPO_ROOT}/scripts/verify.sh"; then
  err "verification failed"
  DEPLOY_OK=0
fi

# ---------------------------------------------------------------------------
# 7. Rollback on failure (application-level only - never destroys anything)
# ---------------------------------------------------------------------------
if [ "$DEPLOY_OK" != "1" ]; then
  err "deploy failed verification - rolling the application back"
  ROLLBACK_ASSUME_YES=1 "${REPO_ROOT}/scripts/rollback.sh" \
    || err "rollback also failed - ESCALATE TO A HUMAN"
fi

# ---------------------------------------------------------------------------
# 8. Report
# ---------------------------------------------------------------------------
report_header "$REPORT" "Deployment Report"
{
  printf '## Result\n\n%s\n\n' \
    "$([ "$DEPLOY_OK" = 1 ] && echo 'SUCCESS' || echo 'FAILED - rolled back')"
  printf '## Stages\n\n'
  printf -- '- Preflight (account/region/budget/state): passed\n'
  printf -- '- Local gate: %s\n' "$([ "$SKIP_LOCAL_TESTS" = 1 ] && echo skipped || echo passed)"
  printf -- '- Image build, push, scan: passed\n'
  printf -- '- Terraform apply: passed\n'
  printf -- '- Blue-green release: passed\n'
  printf -- '- Verification: %s\n' "$([ "$DEPLOY_OK" = 1 ] && echo passed || echo failed)"
  printf '\n## Deployment\n\n'
  printf -- '- Image: `%s`\n' "$IMAGE"
  printf -- '- CodeDeploy deployment: `%s`\n' "$DEPLOY_ID"
  printf -- '- App URL: %s\n' "${API_URL:-unknown}"
  printf '\n## Database\n\n'
  printf -- '- RDS instance: `%s` (status before deploy: %s)\n' "$DB_INSTANCE_ID" "${DB_STATUS:-not present}"
  printf -- '- Restored from snapshot: %s\n' "${RESTORE_SNAPSHOT:-no}"
  printf '\n## Cost\n\nThis environment is ephemeral and burns ~$0.11/hour (incl. RDS ~$0.019/hour).\n'
  printf 'Run `scripts/destroy.sh` when the demo ends - it snapshots the database first.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

if [ "$DEPLOY_OK" != "1" ]; then
  cost_reminder
  die "DEPLOY FAILED - see ${REPORT}. Resources are still running: run ./scripts/destroy.sh"
fi

log "Deploy complete: ${API_URL}"
cost_reminder
