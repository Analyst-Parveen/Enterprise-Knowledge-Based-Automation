#!/usr/bin/env bash
# bootstrap-platform-admin.sh - Create the service provider's first operator.
#
# The platform role is the root of the onboarding hierarchy, so there is
# deliberately NO API that grants it: an API able to mint a platform operator
# would be the single most valuable target in the product. It is granted here,
# out of band, by someone who already holds AWS credentials for this account.
#
# What this does:
#   1. Verifies the AWS account, region and that the user pool belongs to this
#      project (name-owned ekba-<env>-* AND tagged ProjectCode=ekba).
#   2. Creates - or re-labels - ONE Cognito user with
#      custom:role=platform_admin and custom:tenant_id=platform.
#   3. Leaves Cognito to email a one-time password. This script never sets,
#      reads or prints a password.
#
# SAFETY:
#   - Never deletes a user, a pool, or anything else. Re-running is idempotent.
#   - Refuses to touch a pool that is not owned by this project.
#   - Refuses to demote an existing platform operator (use --promote to change
#      an account TO the platform role; there is no --demote here on purpose).
#   - terraform destroy is forbidden in this workflow.
#
# Usage:
#   ./scripts/bootstrap-platform-admin.sh --email ops@yourcompany.com \
#                                         [--name "Platform Operator"] \
#                                         [--promote] [--dry-run]
#
#   --promote   allow re-labelling an existing NON-platform account. Without it,
#               an existing account is left exactly as it is.
#   --dry-run   print what would happen and change nothing.
#
# See RUNBOOK.md Part B and .claude/rules/security.md.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

PLATFORM_TENANT_ID="platform"
PLATFORM_ROLE="platform_admin"

EMAIL=""; DISPLAY_NAME=""; PROMOTE=0; DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --email)   EMAIL="${2:-}"; shift 2 ;;
    --name)    DISPLAY_NAME="${2:-}"; shift 2 ;;
    --promote) PROMOTE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

[ -n "$EMAIL" ] || die "--email is required. Example: --email ops@yourcompany.com"
case "$EMAIL" in
  *@*.*) ;;
  *) die "that does not look like an email address: ${EMAIL}" ;;
esac

REPORT="$(report_path platform-admin)"

log "Bootstrapping the first platform operator for ${PROJECT_NAME} [${ENVIRONMENT}]"
[ "$DRY_RUN" = "1" ] && warn "DRY RUN - nothing will be changed"

# ---------------------------------------------------------------------------
# 1. Identity, region and pool ownership
# ---------------------------------------------------------------------------
preflight_aws

step "Locating the user pool"
POOL_NAME="${PROJECT_CODE}-${ENVIRONMENT}-users"
POOL_ID="${COGNITO_USER_POOL_ID:-}"

if [ -z "$POOL_ID" ]; then
  # Read it from the baseline Terraform state rather than guessing, then still
  # verify the name and tag below.
  BASELINE_DIR="${REPO_ROOT}/infra/terraform/envs/baseline"
  if [ -f "${BASELINE_DIR}/backend.hcl" ] && command -v terraform >/dev/null 2>&1; then
    terraform -chdir="$BASELINE_DIR" init -input=false -backend-config=backend.hcl >/dev/null 2>&1 || true
    POOL_ID="$(terraform -chdir="$BASELINE_DIR" output -raw cognito_user_pool_id 2>/dev/null | tr -d '\r' || true)"
  fi
fi

[ -n "$POOL_ID" ] || die "could not determine the user pool id. Set COGNITO_USER_POOL_ID and retry."

ACTUAL_NAME="$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" \
                 --query 'UserPool.Name' --output text 2>/dev/null | tr -d '\r' || true)"
[ "$ACTUAL_NAME" = "$POOL_NAME" ] \
  || die "pool ${POOL_ID} is named '${ACTUAL_NAME}', expected '${POOL_NAME}'. Refusing to touch it."

POOL_ARN="$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" \
              --query 'UserPool.Arn' --output text | tr -d '\r')"
# Cognito user-pool ARNs carry the opaque pool id (us-west-2_…), not the
# friendly name, so assert_project_owned's ARN name-prefix test cannot apply.
# Ownership here is: name == ekba-<env>-users (checked above) AND the
# ProjectCode tag. That is the same three-signal rule, expressed for Cognito.
POOL_TAG="$(aws resourcegroupstaggingapi get-resources \
              --resource-arn-list "$POOL_ARN" \
              --query "ResourceTagMappingList[0].Tags[?Key=='ProjectCode'].Value | [0]" \
              --output text 2>/dev/null | tr -d '\r' || echo None)"
[ "$POOL_TAG" = "$PROJECT_CODE" ] \
  || die "pool ${POOL_ID} is missing ProjectCode=${PROJECT_CODE} - refusing to touch it."
ok "user pool ${POOL_NAME} (${POOL_ID}) is owned by this project"

# ---------------------------------------------------------------------------
# 2. Inspect the account before changing anything
# ---------------------------------------------------------------------------
step "Checking for an existing account"

# Read one attribute straight out of the API with JMESPath, so the script needs
# no JSON tooling beyond the AWS CLI itself. Absent attributes read as "".
user_attr() {
  local value
  value="$(aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" --username "$EMAIL" \
             --query "UserAttributes[?Name=='$1'].Value | [0]" --output text 2>/dev/null \
           | tr -d '\r')" || true
  [ "$value" = "None" ] && value=""
  printf '%s' "$value"
}

ACTION="create"
if aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" --username "$EMAIL" >/dev/null 2>&1; then
  CURRENT_ROLE="$(user_attr "custom:role")"
  CURRENT_TENANT="$(user_attr "custom:tenant_id")"
  log "account exists: role='${CURRENT_ROLE:-unset}' tenant='${CURRENT_TENANT:-unset}'"

  if [ "$CURRENT_ROLE" = "$PLATFORM_ROLE" ] && [ "$CURRENT_TENANT" = "$PLATFORM_TENANT_ID" ]; then
    ok "already a platform operator - nothing to do"
    ACTION="none"
  elif [ "$PROMOTE" != "1" ]; then
    err "an account for ${EMAIL} already exists as role='${CURRENT_ROLE:-unset}' in tenant='${CURRENT_TENANT:-unset}'"
    die "re-run with --promote to move it to the platform role, or use a different address."
  else
    # A tenant's own admin must not quietly become a platform operator: that is
    # exactly the escalation the hierarchy is built to prevent. Moving it would
    # also strand it outside the company it administers.
    if [ -n "$CURRENT_TENANT" ] && [ "$CURRENT_TENANT" != "$PLATFORM_TENANT_ID" ]; then
      err "${EMAIL} belongs to tenant '${CURRENT_TENANT}'"
      die "a company's own user is never promoted to the platform role. Create a separate operator account."
    fi
    ACTION="promote"
  fi
fi

# ---------------------------------------------------------------------------
# 3. Apply
# ---------------------------------------------------------------------------
if [ "$ACTION" = "none" ]; then
  RESULT="unchanged"
elif [ "$DRY_RUN" = "1" ]; then
  warn "would ${ACTION} ${EMAIL} as ${PLATFORM_ROLE} in tenant ${PLATFORM_TENANT_ID}"
  RESULT="dry-run (${ACTION})"
elif [ "$ACTION" = "create" ]; then
  step "Creating ${EMAIL}"
  # No TemporaryPassword: Cognito generates one and emails it directly, so no
  # password ever exists in this shell, this script's output, or the report.
  # An array, not a string - a display name contains spaces.
  ATTRS=(
    "Name=email,Value=${EMAIL}"
    "Name=email_verified,Value=true"
    "Name=custom:tenant_id,Value=${PLATFORM_TENANT_ID}"
    "Name=custom:role,Value=${PLATFORM_ROLE}"
  )
  [ -n "$DISPLAY_NAME" ] && ATTRS+=("Name=name,Value=${DISPLAY_NAME}")

  aws cognito-idp admin-create-user \
      --user-pool-id "$POOL_ID" \
      --username "$EMAIL" \
      --user-attributes "${ATTRS[@]}" \
      --desired-delivery-mediums EMAIL \
      --output json >/dev/null \
    || die "admin-create-user failed for ${EMAIL}"
  ok "created - Cognito has emailed a one-time password to ${EMAIL}"
  RESULT="created"
else
  step "Promoting ${EMAIL}"
  aws cognito-idp admin-update-user-attributes \
      --user-pool-id "$POOL_ID" \
      --username "$EMAIL" \
      --user-attributes "Name=custom:tenant_id,Value=${PLATFORM_TENANT_ID}" \
                        "Name=custom:role,Value=${PLATFORM_ROLE}" \
    || die "admin-update-user-attributes failed for ${EMAIL}"

  # Old tokens still carry the old claims until they expire. Revoke them so the
  # new role takes effect on the next sign-in rather than up to an hour later.
  aws cognito-idp admin-user-global-sign-out \
      --user-pool-id "$POOL_ID" --username "$EMAIL" >/dev/null 2>&1 || true
  ok "promoted - existing sessions revoked; sign in again to pick up the role"
  RESULT="promoted"
fi

# ---------------------------------------------------------------------------
# 4. Report (no secret values, ever)
# ---------------------------------------------------------------------------
report_header "$REPORT" "Platform operator bootstrap"
{
  printf '| User pool | %s |\n' "$POOL_NAME"
  printf '| Account | %s |\n' "$EMAIL"
  printf '| Role granted | %s |\n' "$PLATFORM_ROLE"
  printf '| Tenant | %s |\n' "$PLATFORM_TENANT_ID"
  printf '| Result | %s |\n\n' "$RESULT"
  printf 'No password was set, read or recorded. Cognito delivers a one-time\n'
  printf 'password by email and the operator replaces it on first sign-in.\n\n'
  printf 'This account can create companies and invite their first administrator.\n'
  printf 'It cannot read any company documents, conversations or metrics.\n'
} >> "$REPORT"

log "Report: ${REPORT}"
printf '\n'
ok "Sign in at the frontend with ${EMAIL} and the one-time password from the email."
log "First sign-in will require choosing a new password before the session is issued."
