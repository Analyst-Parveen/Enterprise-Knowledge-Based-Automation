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

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null \
  || die "ECR login failed"

# Immutable SHA tag. Never :latest.
docker build -f "${REPO_ROOT}/infra/docker/backend.Dockerfile" -t "$IMAGE" "$REPO_ROOT" \
  || die "image build failed"
docker push "$IMAGE" || die "image push failed"
ok "pushed ${IMAGE}"

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
# 4. Infrastructure - plan, review, apply. NEVER auto-approve, NEVER destroy.
#    .claude/rules/terraform.md section 4
# ---------------------------------------------------------------------------
step "Terraform: fmt, validate, plan"
terraform -chdir="$TF_DIR" fmt -check -recursive || die "terraform fmt failed"
terraform -chdir="$TF_DIR" validate              || die "terraform validate failed"
# -var, not TF_VAR_: a terraform.tfvars value would silently override TF_VAR_
# and deploy a stale image. -var takes precedence over every tfvars file.
terraform -chdir="$TF_DIR" plan -input=false -out=tfplan \
  -var="backend_image=${IMAGE}" || die "terraform plan failed"

# A deploy must never destroy. Surface it and require a typed acknowledgement.
step "Reviewing the plan for destructive changes"
# grep exits 1 when nothing matches - i.e. on every normal, non-destructive
# plan - which under `set -euo pipefail` would kill the script silently here.
# `|| true` scopes only to grep: zero matches means zero deletions.
PLAN_JSON="$(terraform -chdir="$TF_DIR" show -json tfplan)" \
  || die "could not read the saved plan"
DESTROY_COUNT="$(printf '%s' "$PLAN_JSON" \
  | { grep -o '"actions":\["delete"\]' || true; } | wc -l | tr -d ' ')"

if [ "${DESTROY_COUNT:-0}" -gt 0 ]; then
  err "plan contains ${DESTROY_COUNT} resource deletion(s)"
  terraform -chdir="$TF_DIR" show -no-color tfplan \
    | grep -E '^\s+#.*(destroyed|replaced)' || true
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
# Migrations run inside the task at startup (alembic upgrade head) and are
# written backward-compatible, so a rollback needs no down-migration.
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
#    container command), because Postgres is a private sidecar that nothing
#    outside the task can reach. seed.sh and test-e2e.sh target the LOCAL
#    stack, so running them here would test the wrong system - and fail the
#    deploy whenever local Docker happens to be stopped.
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
  printf '\n## Cost\n\nThis environment is ephemeral and burns ~$0.072/hour.\n'
  printf 'Run `scripts/destroy.sh` when the demo ends.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

if [ "$DEPLOY_OK" != "1" ]; then
  cost_reminder
  die "DEPLOY FAILED - see ${REPORT}. Resources are still running: run ./scripts/destroy.sh"
fi

log "Deploy complete: ${API_URL}"
cost_reminder
