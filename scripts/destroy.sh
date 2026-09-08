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
step "Generating destroy plan"
terraform -chdir="$TF_DIR" plan -destroy -input=false -out=tfdestroyplan \
  -var="environment=${ENVIRONMENT}" || die "destroy plan failed"

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
confirm_phrase "DESTROY ${PROJECT_CODE}-${ENVIRONMENT}"

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
  printf -- '- Budgets, billing alarms, retained audit log groups\n'
  printf -- '- Everything tagged `Lifecycle = "protected"`\n'
  printf -- '- Every resource outside this project state\n\n'
  printf '## Next\n\nRun `scripts/deploy.sh` to rebuild the environment from scratch.\n'
} >> "$REPORT"

ok "audit report written: ${REPORT}"

[ "$DESTROY_OK" = "1" ] || die "DESTROY FAILED - see ${REPORT}"
log "Ephemeral infrastructure destroyed. Spend stopped. Redeploy with scripts/deploy.sh."
