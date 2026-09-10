#!/usr/bin/env bash
# Shared helpers and safety guards for all lifecycle scripts.
# Sourced by deploy.sh, verify.sh, test-e2e.sh, seed.sh, rollback.sh, destroy.sh.
#
# Rules enforced here:
#   .claude/rules/aws-infrastructure.md  - account/region/ownership preflight
#   .claude/rules/terraform.md           - state + tag ownership, destroy restrictions
#   .claude/rules/secrets-management.md  - never print secret values

set -euo pipefail

# ---------------------------------------------------------------------------
# Project identity
# ---------------------------------------------------------------------------
export PROJECT_NAME="enterprise-knowledge-based-automation"
export PROJECT_CODE="ekba"
export ENVIRONMENT="${ENVIRONMENT:-dev}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT
export TF_DIR="${REPO_ROOT}/infra/terraform/envs/${ENVIRONMENT}"
export REPORT_DIR="${REPO_ROOT}/docs/reports"
export RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"

# Windows: the AWS CLI installer does not always reach Git Bash's PATH.
if ! command -v aws >/dev/null 2>&1 && [ -x "/c/Program Files/Amazon/AWSCLIV2/aws.exe" ]; then
  export PATH="$PATH:/c/Program Files/Amazon/AWSCLIV2"
fi

# Default the AWS identity from the operator's own (git-ignored) baseline
# tfvars, so the same values are not typed twice. An explicit export still wins,
# and preflight_aws still refuses to run if the connected account differs.
_BASELINE_TFVARS="${REPO_ROOT}/infra/terraform/envs/baseline/terraform.tfvars"
_tfvar() {
  [ -f "$_BASELINE_TFVARS" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$_BASELINE_TFVARS" | head -1
}
export EXPECTED_AWS_ACCOUNT_ID="${EXPECTED_AWS_ACCOUNT_ID:-$(_tfvar expected_aws_account_id)}"
export AWS_REGION="${AWS_REGION:-$(_tfvar aws_region)}"
export EXPECTED_AWS_REGION="${EXPECTED_AWS_REGION:-${AWS_REGION}}"

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
  C_BLU=$'\033[34m'; C_DIM=$'\033[2m';  C_RST=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YEL=""; C_BLU=""; C_DIM=""; C_RST=""
fi

log()      { printf '%s[ekba]%s %s\n' "$C_BLU" "$C_RST" "$*"; }
ok()       { printf '%s  OK  %s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn()     { printf '%s WARN %s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }
err()      { printf '%s FAIL %s %s\n' "$C_RED" "$C_RST" "$*" >&2; }
step()     { printf '\n%s==> %s%s\n' "$C_BLU" "$*" "$C_RST"; }
die()      { err "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Tool checks
# ---------------------------------------------------------------------------
require_cmd() {
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "required command not found: $c"
  done
}

# ---------------------------------------------------------------------------
# SAFETY: AWS account / region / state preflight
#
# No script may mutate AWS until this passes. See:
#   .claude/rules/aws-infrastructure.md section 1
# ---------------------------------------------------------------------------
preflight_aws() {
  step "Preflight: AWS identity and region"
  require_cmd aws

  local account
  account="$(aws sts get-caller-identity --query Account --output text)" \
    || die "unable to read AWS caller identity - are credentials configured?"

  if [ -z "${EXPECTED_AWS_ACCOUNT_ID:-}" ]; then
    die "EXPECTED_AWS_ACCOUNT_ID is not set. Refusing to touch AWS without an expected account."
  fi

  if [ "$account" != "$EXPECTED_AWS_ACCOUNT_ID" ]; then
    die "AWS account mismatch: connected to ${account}, expected ${EXPECTED_AWS_ACCOUNT_ID}. STOPPING."
  fi
  ok "AWS account ${account}"

  local region="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
  [ -n "$region" ] || die "AWS_REGION is not set. Refusing to proceed."
  if [ -n "${EXPECTED_AWS_REGION:-}" ] && [ "$region" != "$EXPECTED_AWS_REGION" ]; then
    die "AWS region mismatch: ${region}, expected ${EXPECTED_AWS_REGION}. STOPPING."
  fi
  ok "AWS region ${region}"
}

preflight_terraform() {
  step "Preflight: Terraform state and workspace"
  require_cmd terraform
  [ -d "$TF_DIR" ] || die "terraform environment directory not found: ${TF_DIR}"

  [ -f "${TF_DIR}/backend.hcl" ] \
    || die "no backend.hcl in ${TF_DIR} - run ./scripts/bootstrap-state.sh first"
  terraform -chdir="$TF_DIR" init -input=false -backend-config=backend.hcl >/dev/null \
    || die "terraform init failed"

  local ws
  ws="$(terraform -chdir="$TF_DIR" workspace show)"
  ok "terraform workspace: ${ws}"

  # `terraform state list` exits 1 with "No state file was found!" before the
  # first apply. Under `set -euo pipefail` that used to kill the script silently
  # here, so a first deploy could never start. An empty state is expected and
  # reported as 0; any OTHER failure is still fatal and shown.
  local count state_out
  if state_out="$(terraform -chdir="$TF_DIR" state list 2>&1)"; then
    count="$(printf '%s\n' "$state_out" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
  elif printf '%s' "$state_out" | grep -q "No state file was found"; then
    count=0
  else
    err "$state_out"
    die "could not read terraform state for ${ENVIRONMENT}"
  fi
  ok "terraform state holds ${count} resources for ${ENVIRONMENT}"
}

# ---------------------------------------------------------------------------
# SAFETY: ownership check
#
# A resource belongs to this project only if it is in this Terraform state,
# name-prefixed ekba-<env>-, AND tagged ProjectCode=ekba.
# ---------------------------------------------------------------------------
assert_project_owned() {
  local arn="$1"
  case "$arn" in
    *":${PROJECT_CODE}-${ENVIRONMENT}-"*|*"/${PROJECT_CODE}-${ENVIRONMENT}-"*) ;;
    *) die "refusing to act on resource that is not name-owned by this project: ${arn}" ;;
  esac
  local tag
  tag="$(aws resourcegroupstaggingapi get-resources \
          --resource-arn-list "$arn" \
          --query "ResourceTagMappingList[0].Tags[?Key=='ProjectCode'].Value | [0]" \
          --output text 2>/dev/null || echo "None")"
  [ "$tag" = "$PROJECT_CODE" ] \
    || die "refusing to act on resource without ProjectCode=${PROJECT_CODE} tag: ${arn}"
}

# ---------------------------------------------------------------------------
# Confirmation for irreversible actions. Requires a typed phrase, not "y".
# ---------------------------------------------------------------------------
confirm_phrase() {
  local phrase="$1" reply
  printf '\n%sType exactly "%s" to continue (anything else aborts): %s' "$C_YEL" "$phrase" "$C_RST"
  read -r reply || true
  [ "$reply" = "$phrase" ] || die "confirmation not given - aborted. Nothing was changed."
}

# ---------------------------------------------------------------------------
# Reporting. Reports never contain secret values.
# ---------------------------------------------------------------------------
report_path() {
  mkdir -p "$REPORT_DIR"
  printf '%s/%s-%s-%s.md' "$REPORT_DIR" "$RUN_TS" "$ENVIRONMENT" "$1"
}

report_header() {
  local file="$1" title="$2"
  {
    printf '# %s\n\n' "$title"
    printf '| Field | Value |\n|---|---|\n'
    printf '| Timestamp (UTC) | %s |\n' "$RUN_TS"
    printf '| Project | %s |\n' "$PROJECT_NAME"
    printf '| Environment | %s |\n' "$ENVIRONMENT"
    printf '| Git SHA | %s |\n' "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo 'n/a')"
    printf '| AWS account | %s |\n' "${EXPECTED_AWS_ACCOUNT_ID:-n/a}"
    printf '| AWS region | %s |\n' "${AWS_REGION:-n/a}"
    printf '\n'
  } > "$file"
}

# ---------------------------------------------------------------------------
# COST GUARD - $20 hard ceiling.
#
# Reads month-to-date spend from Cost Explorer and refuses to deploy once the
# ceiling is crossed. See .claude/rules/aws-infrastructure.md section 5.
#
# Note: ce:GetCostAndUsage costs $0.01 per request. Called once per deploy.
# ---------------------------------------------------------------------------
MAX_MONTHLY_SPEND_USD="${MAX_MONTHLY_SPEND_USD:-20}"

month_to_date_spend() {
  local start end
  start="$(date -u +%Y-%m-01)"
  end="$(date -u +%Y-%m-%d)"
  [ "$start" = "$end" ] && end="$(date -u -d '+1 day' +%Y-%m-%d 2>/dev/null || echo "$end")"

  aws ce get-cost-and-usage \
      --time-period "Start=${start},End=${end}" \
      --granularity MONTHLY --metrics UnblendedCost \
      --query 'ResultsByTime[0].Total.UnblendedCost.Amount' \
      --output text 2>/dev/null || echo "unknown"
}

assert_within_budget() {
  step "Cost guard (ceiling: \$${MAX_MONTHLY_SPEND_USD})"
  command -v aws >/dev/null 2>&1 || { warn "aws CLI unavailable - cost guard skipped"; return 0; }

  local spend
  spend="$(month_to_date_spend)"

  if [ "$spend" = "unknown" ] || [ -z "$spend" ]; then
    warn "could not read month-to-date spend (Cost Explorer may not be enabled)"
    warn "proceeding - but verify spend manually in the Billing console"
    return 0
  fi

  local over
  over="$(awk -v s="$spend" -v m="$MAX_MONTHLY_SPEND_USD" 'BEGIN{print (s+0 >= m+0) ? 1 : 0}')"

  if [ "$over" = "1" ]; then
    err "month-to-date spend is \$${spend}, ceiling is \$${MAX_MONTHLY_SPEND_USD}"
    die "REFUSING TO DEPLOY. Run scripts/destroy.sh and review spend in the Billing console."
  fi

  ok "month-to-date spend \$${spend} of \$${MAX_MONTHLY_SPEND_USD}"
}

# Print what is running billable right now, and the reminder to destroy.
cost_reminder() {
  printf '\n%s---------------------------------------------------------------%s\n' "$C_YEL" "$C_RST"
  printf '%s  Estimated burn: ~$0.072/hour  (ALB + 1 Fargate task)%s\n' "$C_YEL" "$C_RST"
  printf '%s  A 4-hour demo costs about $0.30.%s\n' "$C_YEL" "$C_RST"
  printf '%s  RUN ./scripts/destroy.sh WHEN THE DEMO ENDS - idle time is wasted budget.%s\n' "$C_YEL" "$C_RST"
  printf '%s---------------------------------------------------------------%s\n\n' "$C_YEL" "$C_RST"
}

# ---------------------------------------------------------------------------
# GUARD: terraform destroy is forbidden outside destroy.sh.
# Scripts other than destroy.sh set this to block accidental use.
# ---------------------------------------------------------------------------
forbid_destroy() {
  export EKBA_DESTROY_FORBIDDEN=1
}

guard_destroy_allowed() {
  if [ "${EKBA_DESTROY_FORBIDDEN:-0}" = "1" ]; then
    die "terraform destroy is forbidden in this workflow. Use scripts/destroy.sh only."
  fi
}
