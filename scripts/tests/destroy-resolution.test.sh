#!/usr/bin/env bash
# destroy-resolution.test.sh - tests for how scripts/destroy.sh resolves
# backend_image and stays resumable after a PARTIAL destroy.
#
# SAFETY: this test never touches AWS and never destroys anything. `aws` and
# `terraform` are replaced by stubs on PATH which record every call and return
# recorded-shape output. A test fails if destroy.sh ever reaches
# `terraform apply` when it should not.
#
#   ./scripts/tests/destroy-resolution.test.sh

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${TEST_DIR}/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

# --------------------------------------------------------------------------
# stubs
# --------------------------------------------------------------------------
mkdir -p "${WORK}/bin"

cat > "${WORK}/bin/aws" <<'STUB'
#!/usr/bin/env bash
echo "aws $*" >> "$STUB_LOG"
ARGS="$*"
case "$ARGS" in
  *"sts get-caller-identity"*)   echo "${EXPECTED_AWS_ACCOUNT_ID:-890290782965}" ;;
  *"ecs describe-task-definition"*)
    [ -n "${STUB_TD_IMAGE:-}" ] || exit 254
    echo "$STUB_TD_IMAGE" ;;
  *"ecr describe-repositories"*)
    [ -n "${STUB_ECR_URI:-}" ] || exit 254
    echo "$STUB_ECR_URI" ;;
  *"ecr describe-images"*)
    [ -n "${STUB_ECR_TAGS:-}" ] || exit 254
    printf '%s\n' "$STUB_ECR_TAGS" ;;
  *"rds describe-db-instances"*)
    [ -n "${STUB_DB_STATUS:-}" ] || { echo "DBInstanceNotFound" >&2; exit 254; }
    echo "$STUB_DB_STATUS" ;;
  *"rds create-db-snapshot"*|*"rds wait"*) ;;
  *"secretsmanager list-secrets"*) echo "6" ;;
  *) echo "STUB-AWS unhandled: $ARGS" >&2; exit 90 ;;
esac
STUB

cat > "${WORK}/bin/terraform" <<'STUB'
#!/usr/bin/env bash
echo "terraform $*" >> "$STUB_LOG"
ARGS="$*"
case "$ARGS" in
  # " init " is space-delimited on purpose: aws_ecs_task_defINITion contains it.
  *" init "*)         echo "Terraform has been successfully initialized!" ;;
  *"workspace show"*) echo "default" ;;
  *"state list"*)     printf '%s\n' "${STUB_STATE_LIST:-}" ;;
  *"state show"*)
    [ -n "${STUB_TD_ARN:-}" ] || exit 1
    printf '    arn                      = "%s"\n' "$STUB_TD_ARN" ;;
  *"plan -destroy"*)  echo "Plan: 0 to add, 0 to change, ${STUB_PLAN_COUNT:-2} to destroy." ;;
  *"show"*)           printf '%s\n' "${STUB_PLAN_TEXT:-  # module.network.aws_vpc.main will be destroyed}" ;;
  *"apply"*)          echo "APPLY-CALLED" ;;
  *) echo "STUB-TF unhandled: $ARGS" >&2; exit 90 ;;
esac
STUB

chmod +x "${WORK}/bin/aws" "${WORK}/bin/terraform"

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
# run_destroy <confirm-input> [VAR=VALUE ...] - runs destroy.sh with the stubs.
run_destroy() {
  local confirm="$1"; shift
  STUB_LOG="${WORK}/calls.log"
  : > "$STUB_LOG"
  ( cd "$REPO" && printf '%s\n' "$confirm" | PATH="${WORK}/bin:$PATH" STUB_LOG="$STUB_LOG" \
      env "$@" ./scripts/destroy.sh 2>&1 | sed 's/\x1b\[[0-9;]*m//g' )
}

check() {
  local name="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -qF "$needle"; then
    printf '  OK   %s\n' "$name"; PASS=$((PASS + 1))
  else
    printf '  FAIL %s\n       expected to find: %s\n' "$name" "$needle"; FAIL=$((FAIL + 1))
  fi
}

check_absent() {
  local name="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -qF "$needle"; then
    printf '  FAIL %s\n       must NOT contain: %s\n' "$name" "$needle"; FAIL=$((FAIL + 1))
  else
    printf '  OK   %s\n' "$name"; PASS=$((PASS + 1))
  fi
}

STATE_FULL="module.network.aws_vpc.main
module.service.aws_ecs_task_definition.app
module.service.aws_lb_target_group.green"
STATE_PARTIAL="module.network.aws_vpc.main
module.service.aws_lb_target_group.green"
TD_ARN="arn:aws:ecs:us-west-2:890290782965:task-definition/ekba-dev:14"
TD_IMAGE="890290782965.dkr.ecr.us-west-2.amazonaws.com/ekba-dev-backend:f73b4a1"
ECR_URI="890290782965.dkr.ecr.us-west-2.amazonaws.com/ekba-dev-backend"

printf '\nscripts/destroy.sh - backend_image resolution and resumability\n\n'

# 1. Normal destroy: the task definition is still in state.
OUT="$(run_destroy "wrong-phrase" STUB_STATE_LIST="$STATE_FULL" STUB_TD_ARN="$TD_ARN" STUB_TD_IMAGE="$TD_IMAGE")"
check "normal: image read from the task definition" "$OUT" "backend_image: ${TD_IMAGE}"
check "normal: source reported"                     "$OUT" "resolved from: task definition in Terraform state"
check "normal: destroy plan generated"              "$OUT" "RESOURCES THAT WILL BE DESTROYED"
check "normal: stops on a wrong confirmation"       "$OUT" "confirmation not given"
check_absent "normal: nothing applied"              "$(cat "${WORK}/calls.log")" "apply"

# 2. Partial destroy: the task definition is already gone -> ECR fallback.
OUT="$(run_destroy "wrong-phrase" STUB_STATE_LIST="$STATE_PARTIAL" STUB_ECR_URI="$ECR_URI" STUB_ECR_TAGS="f73b4a1")"
check "partial: does not abort on the missing task definition" "$OUT" "falling back to the ECR repository"
check "partial: concrete image from ECR"                       "$OUT" "backend_image: ${ECR_URI}:f73b4a1"
check "partial: source reported"                               "$OUT" "resolved from: newest tagged image in ECR ekba-dev-backend"
check "partial: continues to the plan"                         "$OUT" "RESOURCES THAT WILL BE DESTROYED"
check_absent "partial: no 'NOTHING was destroyed' abort"       "$OUT" "could not read backend_image"

# 3. Missing ALB / target group: only networking is left in state.
OUT="$(run_destroy "wrong-phrase" STUB_STATE_LIST="module.network.aws_vpc.main" \
        STUB_ECR_URI="$ECR_URI" STUB_ECR_TAGS="f73b4a1" STUB_PLAN_COUNT="1" \
        STUB_PLAN_TEXT="  # module.network.aws_vpc.main will be destroyed")"
check "leftovers only: still plans a destroy" "$OUT" "module.network.aws_vpc.main will be destroyed"
check "leftovers only: protected check runs"  "$OUT" "no protected resources in the destroy plan"

# 4. Explicit override wins and skips every lookup.
OVERRIDE="${ECR_URI}:be6a659"
OUT="$(run_destroy "wrong-phrase" STUB_STATE_LIST="$STATE_FULL" BACKEND_IMAGE="$OVERRIDE")"
check "override: used verbatim"            "$OUT" "backend_image: ${OVERRIDE}"
check "override: source reported"          "$OUT" "resolved from: BACKEND_IMAGE override"
check_absent "override: no ECS lookup"     "$(cat "${WORK}/calls.log")" "ecs describe-task-definition"
check_absent "override: no ECR lookup"     "$(cat "${WORK}/calls.log")" "ecr describe-images"

# 5. Fallback impossible: no task definition and no tagged image anywhere.
OUT="$(run_destroy "wrong-phrase" STUB_STATE_LIST="$STATE_PARTIAL")"
check "no image anywhere: fails loudly"        "$OUT" "backend_image could not be resolved"
check "no image anywhere: nothing destroyed"   "$OUT" "NOTHING was destroyed"
check_absent "no image anywhere: no plan"      "$(cat "${WORK}/calls.log")" "plan -destroy"

# 6. Resumed run, confirmed: RDS already gone must not stop the destroy.
BEFORE="$(ls "${REPO}/docs/reports" 2>/dev/null | sort)"
OUT="$(run_destroy "DESTROY ekba-dev" STUB_STATE_LIST="$STATE_PARTIAL" STUB_ECR_URI="$ECR_URI" STUB_ECR_TAGS="f73b4a1")"
check "resumed: RDS already destroyed is fine" "$OUT" "no database instance - nothing to snapshot"
check "resumed: terraform apply reached"       "$(cat "${WORK}/calls.log")" "apply"
check "resumed: reports success"               "$OUT" "Ephemeral infrastructure destroyed"
# the run writes a real audit report - remove it, this was a stubbed test
AFTER="$(ls "${REPO}/docs/reports" 2>/dev/null | sort)"
for NEW in $(comm -13 <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$AFTER")); do
  rm -f "${REPO}/docs/reports/${NEW}"
done

printf '\n  %s passed, %s failed\n\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
