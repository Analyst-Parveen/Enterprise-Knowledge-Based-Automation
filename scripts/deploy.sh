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
# 1. Preflight - account, region, terraform state
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
  step "Local gate: lint, types, unit, integration, security tests"
  # TODO(phase-0+): enable as the backend lands
  #   ruff check backend && ruff format --check backend
  #   mypy backend
  #   pytest backend/tests/unit backend/tests/integration backend/tests/security
  warn "local gate not yet implemented - backend does not exist until Phase 1"
fi

# ---------------------------------------------------------------------------
# 3. Build, scan, push image
# ---------------------------------------------------------------------------
step "Build and scan container image"
# TODO(phase-5): build, push to ECR tagged with GIT_SHA, block on critical findings.
#   docker build -t "${PROJECT_CODE}-backend:${GIT_SHA}" -f infra/docker/backend.Dockerfile .
#   aws ecr describe-image-scan-findings --repository-name "${PROJECT_CODE}-backend" \
#       --image-id "imageTag=${GIT_SHA}"
warn "image build/scan not yet implemented (Phase 5)"

# ---------------------------------------------------------------------------
# 4. Infrastructure - plan, review, apply. NEVER auto-approve, NEVER destroy.
#    .claude/rules/terraform.md section 4
# ---------------------------------------------------------------------------
step "Terraform: fmt, validate, plan"
terraform -chdir="$TF_DIR" fmt -check -recursive || die "terraform fmt failed"
terraform -chdir="$TF_DIR" validate              || die "terraform validate failed"

terraform -chdir="$TF_DIR" plan -input=false -out=tfplan \
  -var="environment=${ENVIRONMENT}" || die "terraform plan failed"

# Refuse to apply a plan that destroys or replaces anything unexpectedly.
step "Reviewing plan for destructive changes"
DESTROY_COUNT="$(terraform -chdir="$TF_DIR" show -json tfplan 2>/dev/null \
  | grep -o '"actions":\["delete"\]' | wc -l | tr -d ' ')"

if [ "${DESTROY_COUNT:-0}" -gt 0 ]; then
  err "plan contains ${DESTROY_COUNT} resource deletion(s)"
  terraform -chdir="$TF_DIR" show tfplan | grep -E '^\s+#.*(destroyed|replaced)' || true
  warn "Deployment must not destroy resources. Review the plan above."
  confirm_phrase "I REVIEWED THIS PLAN"
fi

step "Terraform apply (reviewed plan only)"
terraform -chdir="$TF_DIR" apply -input=false tfplan || die "terraform apply failed"
ok "infrastructure applied"

# ---------------------------------------------------------------------------
# 5. Migrations then blue-green release
#    Migrations must be backward compatible so rollback needs no down-migration.
# ---------------------------------------------------------------------------
step "Database migrations"
# TODO(phase-1): alembic upgrade head
warn "migrations not yet implemented (Phase 1)"

step "Release (CodeDeploy blue-green)"
# TODO(phase-5): create deployment, wait for health checks, shift traffic.
warn "CodeDeploy release not yet implemented (Phase 5)"

# ---------------------------------------------------------------------------
# 6. Post-deploy: health -> verify -> seed -> e2e
#    A deploy that fails verification is a FAILED deploy.
# ---------------------------------------------------------------------------
DEPLOY_OK=1

step "Post-deploy verification"
if ! "${REPO_ROOT}/scripts/verify.sh"; then
  err "verification failed"
  DEPLOY_OK=0
fi

if [ "$DEPLOY_OK" = "1" ]; then
  step "Seeding demo data"
  "${REPO_ROOT}/scripts/seed.sh" || { err "seed failed"; DEPLOY_OK=0; }
fi

if [ "$DEPLOY_OK" = "1" ]; then
  step "End-to-end tests"
  "${REPO_ROOT}/scripts/test-e2e.sh" || { err "e2e failed"; DEPLOY_OK=0; }
fi

# ---------------------------------------------------------------------------
# 7. Rollback on failure (application-level only - never destroys anything)
# ---------------------------------------------------------------------------
if [ "$DEPLOY_OK" != "1" ]; then
  err "deploy failed verification - rolling back application"
  "${REPO_ROOT}/scripts/rollback.sh" || err "rollback also failed - ESCALATE"
fi

# ---------------------------------------------------------------------------
# 8. Report
# ---------------------------------------------------------------------------
report_header "$REPORT" "Deployment Report"
{
  printf '## Result\n\n%s\n\n' "$([ "$DEPLOY_OK" = 1 ] && echo 'SUCCESS' || echo 'FAILED - rolled back')"
  printf '## Stages\n\n'
  printf -- '- Preflight (account/region/state): passed\n'
  printf -- '- Terraform apply: passed\n'
  printf -- '- Verification: %s\n' "$([ "$DEPLOY_OK" = 1 ] && echo passed || echo failed)"
  printf '\n## Cost note\n\nThis environment is ephemeral. Run `scripts/destroy.sh` when the demo ends.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"

if [ "$DEPLOY_OK" != "1" ]; then
  cost_reminder
  die "DEPLOY FAILED - see ${REPORT}. Resources may still be running: consider ./scripts/destroy.sh"
fi

log "Deploy complete."
cost_reminder
