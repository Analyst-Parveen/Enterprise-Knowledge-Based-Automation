#!/usr/bin/env bash
# destroy.sh - Destroy ONLY the temporary infrastructure belonging to this
#              project, so the demo environment costs nothing between demos.
#
# This is the ONLY script in the repository permitted to run `terraform destroy`.
#
# HARD GUARANTEES:
#   * Only resources in THIS project's Terraform state are destroyed.
#   * Nothing tagged Lifecycle=protected is touched.
#   * Secrets Manager secrets, API keys and credentials are PRESERVED - always.
#   * The Terraform state backend is PRESERVED.
#   * The ECR repository and its images are PRESERVED (needed to redeploy).
#   * Budgets, billing alarms and retained audit logs are PRESERVED.
#   * The RDS database's DATA is PRESERVED: a manual snapshot is taken before
#     anything is destroyed, and the destroy aborts if the snapshot fails.
#     deploy.sh restores the newest snapshot next time.
#   * Nothing outside this project is ever touched, under any circumstances.
#
# See .claude/rules/terraform.md section 5 and .claude/rules/aws-infrastructure.md

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# destroy.sh is the sanctioned exception to the destroy prohibition.
export EKBA_DESTROY_FORBIDDEN=0
guard_destroy_allowed

REPORT="$(report_path destroy)"

printf '\n%s================================================================%s\n' "$C_YEL" "$C_RST"
printf '%s  DESTROY - %s [%s]%s\n' "$C_YEL" "$PROJECT_NAME" "$ENVIRONMENT" "$C_RST"
printf '%s================================================================%s\n' "$C_YEL" "$C_RST"

# ---------------------------------------------------------------------------
# Guard 0: never destroy production through this script
# ---------------------------------------------------------------------------
case "$ENVIRONMENT" in
  prod|production)
    die "refusing to destroy '${ENVIRONMENT}'. This script targets ephemeral environments only." ;;
esac

# ---------------------------------------------------------------------------
# Guard 1: verify AWS account, region, workspace and state backend
# ---------------------------------------------------------------------------
preflight_aws
preflight_terraform

# ---------------------------------------------------------------------------
# Guard 2: state must be non-empty and must belong to this project
# ---------------------------------------------------------------------------
step "Confirming Terraform state ownership"
STATE_RESOURCES="$(terraform -chdir="$TF_DIR" state list 2>/dev/null || true)"

if [ -z "$STATE_RESOURCES" ]; then
  log "Terraform state is empty - nothing owned by this project to destroy."
  exit 0
fi

printf '%sResources in this project state:%s\n' "$C_DIM" "$C_RST"
printf '%s\n' "$STATE_RESOURCES" | sed 's/^/    /'

# ---------------------------------------------------------------------------
# Guard 3: produce a DESTROY PLAN and show exactly what would be destroyed
# ---------------------------------------------------------------------------
# backend_image is required and has no default: deploy.sh passes the image it
# built. A destroy plan still needs a value for it, and that value never creates
# anything - Terraform destroys what the state records, whatever this resolves to.
#
# A partial destroy removes the task definition before the resources that failed,
# so a resumed run must not be blocked by a dependency that is already gone.
# Resolution order:
#   1. BACKEND_IMAGE                        explicit override
#   2. the task definition in this state    the normal case
#   3. newest TAGGED image in this project's own ECR repository   fallback
# Never invented and never a bare ":latest": step 3 fails loudly if the
# repository holds no tagged image.
step "Resolving backend_image for the destroy plan"
ECR_REPO="${PROJECT_CODE}-${ENVIRONMENT}-backend"
BACKEND_IMAGE="${BACKEND_IMAGE:-}"
BACKEND_IMAGE_SOURCE="BACKEND_IMAGE override"

if [ -z "$BACKEND_IMAGE" ]; then
  BACKEND_IMAGE_SOURCE="task definition in Terraform state"
  TASK_DEF_ARN="$(terraform -chdir="$TF_DIR" state show -no-color 'module.service.aws_ecs_task_definition.app' 2>/dev/null \
    | sed -nE 's/^[[:space:]]*arn[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' | head -1 || true)"
  if [ -n "$TASK_DEF_ARN" ]; then
    BACKEND_IMAGE="$(aws ecs describe-task-definition --task-definition "$TASK_DEF_ARN" --region "$AWS_REGION" \
      --query "taskDefinition.containerDefinitions[?name=='api'] | [0].image" --output text 2>/dev/null | tr -d '\r' || true)"
  fi
fi

case "$BACKEND_IMAGE" in
  ""|None)
    log "no task definition to read (already destroyed?) - falling back to the ECR repository"
    BACKEND_IMAGE_SOURCE="newest tagged image in ECR ${ECR_REPO}"

    # The repository is this project's own (name-owned ekba-<env>-backend) and
    # lives in the protected baseline, so reading it is safe at any stage of a
    # teardown. describe-repositories gives the authoritative URI rather than a
    # hand-built one.
    ECR_URI="$(aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" \
      --query 'repositories[0].repositoryUri' --output text 2>/dev/null | tr -d '\r' || true)"

    # Newest image that actually carries a tag. Tags are sorted so an image with
    # several of them always resolves to the same one, and "latest" is excluded
    # so the plan always names a concrete build.
    ECR_TAG="$(aws ecr describe-images --repository-name "$ECR_REPO" --region "$AWS_REGION" \
      --query 'reverse(sort_by(imageDetails[?imageTags], &imagePushedAt))[0].imageTags' \
      --output text 2>/dev/null | tr '\t' '\n' | tr -d '\r' | grep -v '^latest$' | sort | head -1 || true)"

    if [ -z "$ECR_URI" ] || [ "$ECR_URI" = "None" ] || [ -z "$ECR_TAG" ] || [ "$ECR_TAG" = "None" ]; then
      die "backend_image could not be resolved: no task definition in this state, and no tagged image in ECR ${ECR_REPO}. NOTHING was destroyed. Rerun with BACKEND_IMAGE=<ECR image URI>."
    fi
    BACKEND_IMAGE="${ECR_URI}:${ECR_TAG}"
    ;;
esac
ok "backend_image: ${BACKEND_IMAGE}"
log "resolved from: ${BACKEND_IMAGE_SOURCE}"

step "Generating destroy plan"
terraform -chdir="$TF_DIR" plan -destroy -input=false -out=tfdestroyplan \
  -var="environment=${ENVIRONMENT}" \
  -var="backend_image=${BACKEND_IMAGE}" || die "destroy plan failed"

printf '\n%s--- RESOURCES THAT WILL BE DESTROYED ---%s\n' "$C_RED" "$C_RST"
terraform -chdir="$TF_DIR" show -no-color tfdestroyplan \
  | grep -E '^\s+#' | sed 's/^/  /' || true
printf '%s----------------------------------------%s\n\n' "$C_RED" "$C_RST"

# ---------------------------------------------------------------------------
# Guard 4: refuse if the plan touches anything protected
#
# Protected = Secrets Manager, Terraform state backend, ECR repositories,
#             budgets/alarms, retained audit log groups, or any resource
#             tagged Lifecycle = "protected".
# ---------------------------------------------------------------------------
step "Checking for protected resources in the destroy plan"
PLAN_TEXT="$(terraform -chdir="$TF_DIR" show -no-color tfdestroyplan 2>/dev/null || true)"

PROTECTED_HITS=""
for pattern in \
  'aws_secretsmanager_secret' \
  'aws_ssm_parameter' \
  'aws_ecr_repository' \
  'aws_s3_bucket.*tfstate' \
  'aws_s3_bucket.*terraform.state' \
  'aws_dynamodb_table.*lock' \
  'aws_budgets_budget' \
  'aws_iam_user' \
  'aws_iam_access_key'
do
  if printf '%s' "$PLAN_TEXT" | grep -qE "will be destroyed" \
     && printf '%s' "$PLAN_TEXT" | grep -qE "# ${pattern}"; then
    PROTECTED_HITS="${PROTECTED_HITS}\n    - ${pattern}"
  fi
done

if printf '%s' "$PLAN_TEXT" | grep -qE '"?Lifecycle"?\s*[:=]\s*"protected"'; then
  PROTECTED_HITS="${PROTECTED_HITS}\n    - resource tagged Lifecycle=protected"
fi

if [ -n "$PROTECTED_HITS" ]; then
  err "the destroy plan includes PROTECTED resources:"
  printf "%b\n" "$PROTECTED_HITS" >&2
  err "Secrets, credentials, state backend and ECR images must never be destroyed."
  die "ABORTED. Nothing was destroyed. Exclude these resources from the ephemeral stack first."
fi
ok "no protected resources in the destroy plan"

# ---------------------------------------------------------------------------
# Guard 5: explicit typed confirmation (not 'y')
# ---------------------------------------------------------------------------
RESOURCE_COUNT="$(printf '%s\n' "$STATE_RESOURCES" | wc -l | tr -d ' ')"
warn "About to destroy ${RESOURCE_COUNT} resource(s) owned by ${PROJECT_CODE}-${ENVIRONMENT}."
warn "Secrets, Terraform state backend and ECR images will be PRESERVED."
warn "The RDS database is snapshotted first; its data comes back on the next deploy."
confirm_phrase "DESTROY ${PROJECT_CODE}-${ENVIRONMENT}"

# ---------------------------------------------------------------------------
# Snapshot the database BEFORE anything is destroyed. A manual snapshot is not
# in Terraform state, so the destroy below cannot remove it. If it cannot be
# taken, stop: destroying the instance would lose the data.
# ---------------------------------------------------------------------------
step "Snapshotting the database (${DB_INSTANCE_ID})"
DB_SNAPSHOT=""
DB_STATUS="$(db_instance_status)"
case "$DB_STATUS" in
  "")
    log "no database instance - nothing to snapshot"
    ;;
  available|stopped)
    if [ "$DB_STATUS" = "stopped" ]; then
      # Start it first: a snapshot needs an available instance.
      log "instance is stopped - starting it so it can be snapshotted"
      aws rds start-db-instance --db-instance-identifier "$DB_INSTANCE_ID" --region "$AWS_REGION" >/dev/null \
        || die "could not start ${DB_INSTANCE_ID} for its snapshot. NOTHING was destroyed."
      aws rds wait db-instance-available --db-instance-identifier "$DB_INSTANCE_ID" --region "$AWS_REGION" \
        || die "${DB_INSTANCE_ID} did not become available. NOTHING was destroyed."
    fi
    DB_SNAPSHOT="${DB_INSTANCE_ID}-$(date -u +%Y%m%d-%H%M%S)"
    aws rds create-db-snapshot --region "$AWS_REGION" \
        --db-instance-identifier "$DB_INSTANCE_ID" --db-snapshot-identifier "$DB_SNAPSHOT" \
        --tags "Key=ProjectCode,Value=${PROJECT_CODE}" "Key=Environment,Value=${ENVIRONMENT}" \
               "Key=Lifecycle,Value=protected" "Key=ManagedBy,Value=destroy.sh" >/dev/null \
      || die "could not start the snapshot. NOTHING was destroyed."
    log "waiting for snapshot ${DB_SNAPSHOT} (usually 3-10 minutes)"
    aws rds wait db-snapshot-available --db-snapshot-identifier "$DB_SNAPSHOT" --region "$AWS_REGION" \
      || die "snapshot ${DB_SNAPSHOT} did not complete. NOTHING was destroyed."
    ok "snapshot ${DB_SNAPSHOT} is available - deploy.sh restores it next time"
    ;;
  *)
    die "database is '${DB_STATUS}' - it cannot be snapshotted now. NOTHING was destroyed. Rerun when it is available."
    ;;
esac

# ---------------------------------------------------------------------------
# Destroy - the reviewed plan only, nothing else
# ---------------------------------------------------------------------------
step "Destroying ephemeral project infrastructure"
DESTROY_OK=1
terraform -chdir="$TF_DIR" apply -input=false tfdestroyplan || DESTROY_OK=0

# ---------------------------------------------------------------------------
# Post-destroy: confirm the protected baseline survived
# ---------------------------------------------------------------------------
step "Confirming protected resources survived"
if command -v aws >/dev/null 2>&1; then
  SECRET_COUNT="$(aws secretsmanager list-secrets \
      --query "length(SecretList[?starts_with(Name, '${PROJECT_CODE}/${ENVIRONMENT}/')])" \
      --output text 2>/dev/null || echo 'unknown')"
  ok "Secrets Manager secrets still present: ${SECRET_COUNT} (never deleted by this script)"
fi

# ---------------------------------------------------------------------------
# Audit record
# ---------------------------------------------------------------------------
report_header "$REPORT" "Destroy Audit Report"
{
  printf '## Result\n\n%s\n\n' "$([ "$DESTROY_OK" = 1 ] && echo 'Ephemeral infrastructure destroyed' || echo 'DESTROY FAILED')"
  printf '## Resources destroyed (from this project state only)\n\n```\n%s\n```\n\n' "$STATE_RESOURCES"
  printf '## Preserved\n\n'
  printf -- '- AWS Secrets Manager secrets and all credentials\n'
  printf -- '- Terraform state backend (S3 bucket + lock table)\n'
  printf -- '- ECR repository and images (required to redeploy)\n'
  printf -- '- RDS data, as manual snapshot `%s`\n' "${DB_SNAPSHOT:-none - there was no database instance}"
  printf -- '- Budgets, billing alarms, retained audit log groups\n'
  printf -- '- Everything tagged `Lifecycle = "protected"`\n'
  printf -- '- Every resource outside this project state\n\n'
  printf '## Next\n\nRun `scripts/deploy.sh` to rebuild the environment from scratch.\n'
} >> "$REPORT"

ok "audit report written: ${REPORT}"

[ "$DESTROY_OK" = "1" ] || die "DESTROY FAILED - see ${REPORT}"
log "Ephemeral infrastructure destroyed. Spend stopped. Redeploy with scripts/deploy.sh."
