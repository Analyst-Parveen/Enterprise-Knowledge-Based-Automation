#!/usr/bin/env bash
# bootstrap-state.sh - ONE-TIME: create the Terraform remote-state backend.
#
#   ./scripts/bootstrap-state.sh          (run from Git Bash)
#
# 1. S3 bucket for state      (versioned, encrypted, public access blocked)
# 2. DynamoDB table for locks
# 3. backend.hcl in envs/baseline and envs/dev, then `terraform init` in both
#
# Both resources are tagged Lifecycle=protected. destroy.sh never touches them.
# Safe to re-run: anything that already exists is left as it is.
# Cost at rest: a few cents a month (S3 storage + on-demand DynamoDB).
#
# Account, region, bucket and table names are read from
# infra/terraform/envs/baseline/terraform.tfvars.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

BUCKET="$(_tfvar tfstate_bucket)"
BUCKET="${BUCKET:-${PROJECT_CODE}-tfstate-${EXPECTED_AWS_ACCOUNT_ID}}"
TABLE="$(_tfvar tfstate_lock_table)"
TABLE="${TABLE:-${PROJECT_CODE}-tfstate-lock}"

log "Bootstrapping the Terraform state backend"
preflight_aws          # the connected account/region must match before anything is created
require_cmd terraform

# ---------------------------------------------------------------------------
step "S3 state bucket: ${BUCKET}"
# ---------------------------------------------------------------------------
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  ok "already exists - not recreated"
else
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
  fi || die "could not create ${BUCKET} (S3 names are global - is it taken?)"

  aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging \
    "TagSet=[{Key=Project,Value=${PROJECT_NAME}},{Key=ProjectCode,Value=${PROJECT_CODE}},{Key=ManagedBy,Value=bootstrap-state.sh},{Key=Lifecycle,Value=protected}]"
  ok "created"
fi

# Idempotent hardening - each call only ever makes the bucket stricter.
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
ok "versioning on, AES256 encryption, all public access blocked"

# ---------------------------------------------------------------------------
step "DynamoDB lock table: ${TABLE}"
# ---------------------------------------------------------------------------
if aws dynamodb describe-table --table-name "$TABLE" --region "$AWS_REGION" >/dev/null 2>&1; then
  ok "already exists - not recreated"
else
  aws dynamodb create-table --table-name "$TABLE" --region "$AWS_REGION" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --tags "Key=Project,Value=${PROJECT_NAME}" "Key=ProjectCode,Value=${PROJECT_CODE}" \
           "Key=ManagedBy,Value=bootstrap-state.sh" "Key=Lifecycle,Value=protected" \
    >/dev/null || die "could not create ${TABLE}"
  log "waiting for the table to become ACTIVE..."
  aws dynamodb wait table-exists --table-name "$TABLE" --region "$AWS_REGION"
  ok "created"
fi

# ---------------------------------------------------------------------------
step "backend.hcl + terraform init"
# ---------------------------------------------------------------------------
for env in baseline dev; do
  dir="${REPO_ROOT}/infra/terraform/envs/${env}"

  # Never silently abandon local state: it must be migrated on purpose.
  if [ -f "${dir}/terraform.tfstate" ]; then
    die "${dir}/terraform.tfstate exists - migrate it deliberately with: terraform init -migrate-state -backend-config=backend.hcl"
  fi

  cat > "${dir}/backend.hcl" <<EOF
bucket         = "${BUCKET}"
key            = "${env}/terraform.tfstate"
region         = "${AWS_REGION}"
dynamodb_table = "${TABLE}"
encrypt        = true
EOF

  terraform -chdir="$dir" init -input=false -reconfigure -backend-config=backend.hcl >/dev/null \
    || die "terraform init failed in envs/${env}"
  ok "envs/${env} -> s3://${BUCKET}/${env}/terraform.tfstate"
done

# ---------------------------------------------------------------------------
REPORT="$(report_path bootstrap-state)"
report_header "$REPORT" "State Backend Bootstrap"
{
  printf '## Result\n\nState backend ready.\n\n'
  printf '| Resource | Name | Lifecycle |\n|---|---|---|\n'
  printf '| S3 bucket | `%s` | protected |\n' "$BUCKET"
  printf '| DynamoDB table | `%s` | protected |\n\n' "$TABLE"
  printf 'Neither is ever touched by `destroy.sh`.\n'
} >> "$REPORT"

ok "report written: ${REPORT}"
log "State backend ready. Next: apply the baseline (RUNBOOK Part B, Step 3)."
