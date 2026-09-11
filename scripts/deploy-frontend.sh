#!/usr/bin/env bash
# deploy-frontend.sh - Deploy or update the Next.js frontend on AWS Amplify
#                      Hosting, with CloudFront as the HTTPS front door for the API.
#
#   Browser -> Amplify (HTTPS, static export) -> CloudFront (HTTPS) -> ALB (HTTP) -> ECS
#
# Usage:
#   ./scripts/deploy-frontend.sh               deploy / update, then verify
#   ./scripts/deploy-frontend.sh --plan-only   gate + Terraform plans; changes nothing
#   ./scripts/deploy-frontend.sh --no-backend  never run deploy.sh; report the wiring still needed
#   ./scripts/deploy-frontend.sh --rebuild     start an Amplify build even if the branch head is live
#
# Safe to re-run: Terraform is declarative, the Amplify app is imported once,
# the backend is redeployed only when its live CORS / ingress differ from what
# the frontend needs, and a build starts only when needed - never while one is
# already running.
#
# SAFETY: never runs terraform destroy, never commits or pushes, never touches
# secrets, the protected baseline or the cost guard. Code reaches Amplify only
# through git push. Exit code 3 = the one-time console connection is pending.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

PLAN_ONLY=0
RUN_BACKEND=1
FORCE_REBUILD=0
for arg in "$@"; do
  case "$arg" in
    --plan-only)  PLAN_ONLY=1 ;;
    --no-backend) RUN_BACKEND=0 ;;
    --rebuild)    FORCE_REBUILD=1 ;;
    -h|--help)    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            die "unknown option: $arg (see --help)" ;;
  esac
done

FE_APP_DIR="${REPO_ROOT}/frontend"
FE_TF="${REPO_ROOT}/infra/terraform/envs/frontend"
WIRING_FILE="${TF_DIR}/frontend.auto.tfvars"   # TF_DIR is envs/<env> (see _common.sh)
APP_NAME="${PROJECT_CODE}-${ENVIRONMENT}-frontend"
CLUSTER="${PROJECT_CODE}-${ENVIRONMENT}"
ALB_NAME="${PROJECT_CODE}-${ENVIRONMENT}-alb"
BUILD_TIMEOUT_MIN="${FRONTEND_BUILD_TIMEOUT_MIN:-25}"

# Plan/apply transcripts are scratch output, kept out of the repository.
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
PLAN_TXT="${WORK_DIR}/frontend-plan.txt"

fe_tfvar() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\([^\"]*\)\".*/\1/p" "${FE_TF}/terraform.tfvars" | head -1
}
norm_repo() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's#\.git$##; s#/+$##'; }
tf_fe() { terraform -chdir="$FE_TF" "$@"; }
tf_out() { tf_fe output -raw "$1" 2>/dev/null || true; }

# ---------------------------------------------------------------------------
# 1. Preflight
# ---------------------------------------------------------------------------
log "Frontend deploy: ${APP_NAME} [${ENVIRONMENT}]$([ "$PLAN_ONLY" = 1 ] && printf '  -- PLAN ONLY, nothing will change')"

preflight_aws
require_cmd terraform npm curl git
[ -f "${FE_TF}/backend.hcl" ] \
  || die "no ${FE_TF}/backend.hcl - copy envs/dev/backend.hcl and set key = \"frontend/terraform.tfstate\""
[ -f "${FE_TF}/terraform.tfvars" ] \
  || die "no ${FE_TF}/terraform.tfvars - copy terraform.tfvars.example next to it and fill it in"

REPO_URL="$(fe_tfvar repository_url)"
BRANCH="$(fe_tfvar branch_name)"
[ -n "$REPO_URL" ] && [ -n "$BRANCH" ] || die "repository_url and branch_name must be set in ${FE_TF}/terraform.tfvars"

assert_within_budget

# ---------------------------------------------------------------------------
# 2. Backend - the ALB the API front door points at (changes on every recreate)
# ---------------------------------------------------------------------------
step "Backend: the ALB behind the API front door"
[ -f "${TF_DIR}/backend.hcl" ] || die "no ${TF_DIR}/backend.hcl - run ./scripts/bootstrap-state.sh first"
terraform -chdir="$TF_DIR" init -input=false -backend-config=backend.hcl >/dev/null \
  || die "terraform init failed in envs/${ENVIRONMENT}"

APP_URL="$(terraform -chdir="$TF_DIR" output -raw app_url 2>/dev/null || true)"
case "$APP_URL" in
  http://*) ;;
  *) die "envs/${ENVIRONMENT} has no app_url - the backend is not deployed. Run ./scripts/deploy.sh first." ;;
esac
ALB_DNS="${APP_URL#http://}"
LIVE_DNS="$(aws elbv2 describe-load-balancers --names "$ALB_NAME" \
              --query 'LoadBalancers[0].DNSName' --output text 2>/dev/null || echo none)"
[ "$LIVE_DNS" = "$ALB_DNS" ] \
  || die "envs/${ENVIRONMENT} records ALB ${ALB_DNS} but AWS reports '${LIVE_DNS}' (was it stopped by the cost guard?). Run ./scripts/destroy.sh and ./scripts/deploy.sh first."
ok "ALB ${ALB_DNS}"

# ---------------------------------------------------------------------------
# 3. Source - Amplify builds GitHub, never this working tree
# ---------------------------------------------------------------------------
step "Source: what Amplify will build"
REMOTE_HEAD="$(git -C "$REPO_ROOT" ls-remote origin "refs/heads/${BRANCH}" 2>/dev/null | cut -f1)"
[ -n "$REMOTE_HEAD" ] || die "branch ${BRANCH} not found on origin - Amplify builds from GitHub, so push it first"
ok "origin/${BRANCH} is at ${REMOTE_HEAD:0:7}"

DIRTY="$(git -C "$REPO_ROOT" status --porcelain -- frontend amplify.yml)"
if [ -n "$DIRTY" ]; then
  warn "uncommitted changes below are NOT deployed - Amplify builds ${REMOTE_HEAD:0:7} from GitHub:"
  printf '%s\n' "$DIRTY" | sed 's/^/    /' >&2
fi
if git -C "$REPO_ROOT" cat-file -e "${REMOTE_HEAD}:amplify.yml" 2>/dev/null; then
  ok "amplify.yml is present in ${REMOTE_HEAD:0:7}"
elif git -C "$REPO_ROOT" cat-file -e "${REMOTE_HEAD}^{commit}" 2>/dev/null; then
  warn "amplify.yml is not on origin/${BRANCH} yet - Amplify builds fail until it is committed and pushed"
else
  warn "${REMOTE_HEAD:0:7} is not in the local clone (git fetch) - cannot check it for amplify.yml"
fi

# ---------------------------------------------------------------------------
# 4. Frontend gate - the same steps amplify.yml runs, locally, before any change
# ---------------------------------------------------------------------------
step "Terraform init: envs/frontend"
tf_fe init -input=false -backend-config=backend.hcl >/dev/null || die "terraform init failed in envs/frontend"
ok "initialised"

step "Frontend gate: lint, typecheck, static-export build"
GATE_API_URL="$(tf_out api_url)"
GATE_API_URL="${GATE_API_URL:-https://api.not-created-yet.invalid}"
(
  cd "$FE_APP_DIR" || exit 1
  if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
    npm ci --no-audit --no-fund || exit 1
  fi
  npm run lint || exit 1
  npm run typecheck || exit 1
  rm -rf out
  NEXT_TELEMETRY_DISABLED=1 NEXT_OUTPUT_MODE=export NEXT_PUBLIC_API_URL="$GATE_API_URL" \
    npm run build || exit 1
  [ -f out/index.html ] && [ -f out/404.html ] || { echo "static export produced no out/index.html or out/404.html"; exit 1; }
) || die "frontend gate failed - fix the code, never the check"
ok "frontend gate passed"

# ---------------------------------------------------------------------------
# 5. The Amplify app - connected once in the console, found here by name
# ---------------------------------------------------------------------------
step "Amplify app ${APP_NAME}"
APPS="$(aws amplify list-apps --query "apps[?name=='${APP_NAME}'].[appId,repository]" --output text)" \
  || die "could not list Amplify apps"
APP_COUNT="$(printf '%s\n' "$APPS" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
APP_ID=""
case "$APP_COUNT" in
  0)
    warn "no Amplify app named ${APP_NAME} yet - the console connection is still pending" ;;
  1)
    APP_ID="$(printf '%s' "$APPS" | cut -f1)"
    APP_REPO="$(printf '%s' "$APPS" | cut -f2)"
    [ "$(norm_repo "$APP_REPO")" = "$(norm_repo "$REPO_URL")" ] \
      || die "${APP_NAME} (${APP_ID}) is connected to '${APP_REPO}', expected ${REPO_URL}. STOPPING - not changing an app this project does not own."
    aws amplify get-branch --app-id "$APP_ID" --branch-name "$BRANCH" >/dev/null 2>&1 \
      || die "app ${APP_ID} has no branch ${BRANCH} - connect that branch in the Amplify console"
    ok "found ${APP_ID} with branch ${BRANCH}" ;;
  *)
    die "${APP_COUNT} Amplify apps are named ${APP_NAME} - remove the duplicate in the console first" ;;
esac

# ---------------------------------------------------------------------------
# 6. Terraform - CloudFront API front door, Amplify settings (import on first use)
# ---------------------------------------------------------------------------
step "Terraform: fmt, validate, plan (envs/frontend)"
terraform fmt -check -recursive "${REPO_ROOT}/infra/terraform" >/dev/null \
  || die "terraform fmt -check failed - run: terraform fmt -recursive infra/terraform"
tf_fe validate -no-color >/dev/null || { tf_fe validate -no-color; die "terraform validate failed"; }

tf_fe plan -input=false -no-color -out=tfplan \
  -var="api_origin_domain=${ALB_DNS}" -var="amplify_app_id=${APP_ID}" > "$PLAN_TXT" 2>&1 \
  || { cat "$PLAN_TXT"; die "terraform plan failed"; }

PLAN_JSON="$(tf_fe show -json tfplan)" || die "could not read the saved plan"
# grep exits 1 on zero matches - scope || true to grep only.
DESTRUCTIVE="$(printf '%s' "$PLAN_JSON" \
  | { grep -oE '"actions":\[("delete"|"delete","create"|"create","delete")\]' || true; } | wc -l | tr -d ' ')"
AMPLIFY_CHANGED=0
grep -qE '# module\.frontend\.aws_amplify_(app|branch)\.this\[0\] (will be|must be)' "$PLAN_TXT" && AMPLIFY_CHANGED=1

grep -E '^[[:space:]]+# .*(will be|must be)' "$PLAN_TXT" | sed 's/^[[:space:]]*/    /' || true
PLAN_SUMMARY="$(grep -E '^(Plan:|No changes)' "$PLAN_TXT" | head -1)"
log "${PLAN_SUMMARY:-plan summary unavailable}"

if [ "${DESTRUCTIVE:-0}" -gt 0 ]; then
  err "the plan deletes or replaces ${DESTRUCTIVE} resource(s):"
  grep -E '^[[:space:]]+# .*(destroyed|replaced)' "$PLAN_TXT" >&2 || true
  [ "$PLAN_ONLY" = 1 ] || confirm_phrase "I REVIEWED THIS PLAN"
fi

if [ "$PLAN_ONLY" = 0 ]; then
  step "Terraform apply (the reviewed plan only)"
  if ! tf_fe apply -input=false -no-color tfplan > "${WORK_DIR}/frontend-apply.txt" 2>&1; then
    cat "${WORK_DIR}/frontend-apply.txt"
    die "terraform apply failed"
  fi
  grep -E 'complete after|Apply complete|Import' "${WORK_DIR}/frontend-apply.txt" | sed 's/^/    /' || true
  ok "envs/frontend applied"
fi

API_URL="$(tf_out api_url)"
DIST_ID="$(tf_out api_distribution_id)"
SITE_URL=""
if [ -n "$APP_ID" ]; then
  # Known before apply too: the default domain is <appId>.amplifyapp.com.
  SITE_URL="https://${BRANCH}.$(aws amplify get-app --app-id "$APP_ID" --query 'app.defaultDomain' --output text)"
fi

# ---------------------------------------------------------------------------
# 7. Backend wiring - CloudFront ingress + CORS, persisted for every deploy.sh
# ---------------------------------------------------------------------------
step "Backend wiring: CloudFront ingress + CORS${SITE_URL:+ for ${SITE_URL}}"

DESIRED_WIRING="$(printf '%s\n' \
  '# Generated by scripts/deploy-frontend.sh - do not edit by hand. Git-ignored.' \
  '# Terraform loads *.auto.tfvars automatically, so every deploy.sh keeps this wiring.' \
  'cloudfront_origin_ingress = true' \
  "amplify_origin            = \"${SITE_URL}\"")"
CURRENT_WIRING="$(cat "$WIRING_FILE" 2>/dev/null || true)"

CF_PL="$(aws ec2 describe-managed-prefix-lists \
          --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
          --query 'PrefixLists[0].PrefixListId' --output text)"
SG_PLS="$(aws ec2 describe-security-groups \
          --filters "Name=group-name,Values=${ALB_NAME}" "Name=tag:ProjectCode,Values=${PROJECT_CODE}" \
          --query 'SecurityGroups[0].IpPermissions[].PrefixListIds[].PrefixListId' --output text)"
INGRESS_LIVE=0
printf '%s\n' "$SG_PLS" | tr '\t' '\n' | grep -qx "$CF_PL" && INGRESS_LIVE=1

TASK_DEF="$(aws ecs describe-services --cluster "$CLUSTER" --services "$CLUSTER" \
             --query "services[0].taskSets[?status=='PRIMARY'].taskDefinition | [0]" --output text)"
if [ -z "$TASK_DEF" ] || [ "$TASK_DEF" = "None" ]; then
  TASK_DEF="$(aws ecs describe-services --cluster "$CLUSTER" --services "$CLUSTER" \
               --query 'services[0].taskDefinition' --output text)"
fi
LIVE_CORS="$(aws ecs describe-task-definition --task-definition "$TASK_DEF" \
  --query "taskDefinition.containerDefinitions[?name=='api'] | [0].environment[?name=='CORS_ALLOWED_ORIGINS'].value | [0]" \
  --output text)"
CORS_LIVE=0
if [ -n "$SITE_URL" ]; then
  case ",${LIVE_CORS}," in *",${SITE_URL},"*) CORS_LIVE=1 ;; esac
fi

[ "$INGRESS_LIVE" = 1 ] && ok "ALB admits CloudFront (${CF_PL})" || warn "ALB does not admit CloudFront yet"
if [ -n "$SITE_URL" ]; then
  [ "$CORS_LIVE" = 1 ] && ok "live CORS allows ${SITE_URL}" || warn "live CORS does not allow ${SITE_URL} yet (live: ${LIVE_CORS})"
fi

BACKEND_ACTION="none needed"
if [ "$PLAN_ONLY" = 1 ]; then
  if [ "$DESIRED_WIRING" != "$CURRENT_WIRING" ]; then
    log "would write ${WIRING_FILE#${REPO_ROOT}/}:"
    printf '%s\n' "$DESIRED_WIRING" | sed 's/^/    /'
  fi
  step "Preview: envs/${ENVIRONMENT} plan that deploy.sh would apply for this wiring (read-only)"
  CUR_IMAGE="$(aws ecs describe-task-definition --task-definition "$TASK_DEF" \
    --query "taskDefinition.containerDefinitions[?name=='api'] | [0].image" --output text)"
  DEV_PLAN_TXT="${WORK_DIR}/dev-preview-plan.txt"
  terraform -chdir="$TF_DIR" plan -input=false -no-color \
    -var="backend_image=${CUR_IMAGE}" -var="cloudfront_origin_ingress=true" -var="amplify_origin=${SITE_URL}" \
    > "$DEV_PLAN_TXT" 2>&1 || { cat "$DEV_PLAN_TXT"; die "preview plan failed"; }
  grep -E '^[[:space:]]+# .*(will be|must be)' "$DEV_PLAN_TXT" | sed 's/^[[:space:]]*/    /' || true
  log "$(grep -E '^(Plan:|No changes)' "$DEV_PLAN_TXT" | head -1)"
  [ -z "$SITE_URL" ] && warn "CORS is not in this preview - it needs the Amplify app, which does not exist yet"
  rm -f "${FE_TF}/tfplan"   # plan-only never leaves an applicable plan behind
else
  if [ -z "$APP_ID" ]; then
    : # nothing to wire until the app exists - handled below
  else
    if [ "$DESIRED_WIRING" != "$CURRENT_WIRING" ]; then
      printf '%s\n' "$DESIRED_WIRING" > "$WIRING_FILE"
      ok "wrote ${WIRING_FILE#${REPO_ROOT}/}"
    else
      ok "${WIRING_FILE#${REPO_ROOT}/} already up to date"
    fi

    if [ "$INGRESS_LIVE" = 1 ] && [ "$CORS_LIVE" = 1 ]; then
      ok "backend already wired - no backend deploy needed"
    elif [ "$RUN_BACKEND" = 1 ]; then
      # deploy.sh ends with verify.sh, which reaches the ALB from this machine.
      MY_IP="$(curl -s --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]' || true)"
      if [ -n "$MY_IP" ] && ! grep -q "\"${MY_IP}/32\"" "${TF_DIR}/terraform.tfvars" 2>/dev/null; then
        warn "your public IP ${MY_IP} is not in allowed_cidrs - deploy.sh's verify.sh will time out. Update envs/${ENVIRONMENT}/terraform.tfvars first."
      fi
      log "running ./scripts/deploy.sh - backend blue-green with the new wiring (about 12 minutes)"
      "${REPO_ROOT}/scripts/deploy.sh" \
        || die "backend deploy failed - see its report. The frontend cannot call the API until it passes."
      BACKEND_ACTION="deploy.sh ran (security group and/or CORS changed)"
    else
      warn "--no-backend: the backend still needs this wiring. Apply it with: ./scripts/deploy.sh"
      BACKEND_ACTION="PENDING - run ./scripts/deploy.sh"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# One-time manual step: connect the repository in the Amplify console
# ---------------------------------------------------------------------------
print_connect_steps() {
  printf '\n%sONE-TIME MANUAL STEP - connect the repository (needs your GitHub login):%s\n' "$C_YEL" "$C_RST"
  cat <<EOF
  0. Commit and push amplify.yml and frontend/next.config.mjs to origin/${BRANCH}.
     Amplify builds GitHub, not this working tree.
  1. Open https://${AWS_REGION}.console.aws.amazon.com/amplify/create  (region ${AWS_REGION})
  2. Choose GitHub, authorize, and install the "AWS Amplify" GitHub App for
     ONLY this repository: ${REPO_URL#https://github.com/}
  3. Repository ${REPO_URL#https://github.com/}, branch ${BRANCH}.
     Tick "My app is a monorepo" and set the root directory to: frontend
  4. App name: ${APP_NAME}. Keep the detected amplify.yml. No environment
     variables are needed - Terraform sets them.
  5. Save and deploy. The first build FAILS on purpose: NEXT_PUBLIC_API_URL is
     not set until this script imports the app.
  Then run ./scripts/deploy-frontend.sh again.
EOF
  [ -n "$API_URL" ] && printf '  (The API front door is already live: %s)\n' "$API_URL"
  printf '\n'
}

if [ "$PLAN_ONLY" = 1 ]; then
  [ -z "$APP_ID" ] && print_connect_steps
  ok "plan only - nothing was changed"
  exit 0
fi

REPORT="$(report_path frontend-deploy)"
write_report() {  # $1 = result line, $2 = verification table (may be empty)
  report_header "$REPORT" "Frontend Deployment Report"
  {
    printf '## Result\n\n%s\n\n' "$1"
    printf '| Item | Value |\n|---|---|\n'
    printf '| Amplify app | %s |\n' "${APP_ID:-not connected yet}"
    printf '| Branch / commit | %s @ %s |\n' "$BRANCH" "${REMOTE_HEAD:0:7}"
    printf '| Amplify job | %s |\n' "${JOB_ID:-none started}"
    printf '| Site URL | %s |\n' "${SITE_URL:-n/a}"
    printf '| API URL (CloudFront) | %s |\n' "${API_URL:-n/a}"
    printf '| API origin (ALB) | %s |\n' "$ALB_DNS"
    printf '| Terraform (envs/frontend) | %s |\n' "${PLAN_SUMMARY:-n/a}"
    printf '| Backend wiring | %s |\n\n' "$BACKEND_ACTION"
    [ -n "${2:-}" ] && printf '## Verification\n\n| Result | Check |\n|---|---|\n%b\n' "$2"
    printf '## Cost\n\nAmplify ~$0.04 per build; CloudFront ~$0 at demo traffic; ~$0/month idle.\n'
    printf 'The backend stack still burns ~$0.09/hour until ./scripts/destroy.sh.\n'
  } >> "$REPORT"
}

if [ -z "$APP_ID" ]; then
  print_connect_steps
  write_report "PENDING - CloudFront API front door applied; Amplify app not connected yet" ""
  ok "report written: ${REPORT}"
  exit 3
fi

# ---------------------------------------------------------------------------
# 8. Amplify build - only when the live deployment is not already current
# ---------------------------------------------------------------------------
step "Amplify build: origin/${BRANCH} at ${REMOTE_HEAD:0:7}"

job_field() { aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$1" --query "$2" --output text; }

wait_for_job() {  # $1 = job id; returns 0 on SUCCEED
  local id="$1" deadline status last=""
  deadline=$(( $(date +%s) + BUILD_TIMEOUT_MIN * 60 ))
  while :; do
    status="$(job_field "$id" 'job.summary.status' 2>/dev/null || echo UNKNOWN)"
    [ "$status" != "$last" ] && log "job ${id}: ${status}" && last="$status"
    case "$status" in
      SUCCEED) return 0 ;;
      FAILED|CANCELLED)
        err "job ${id} ${status}. Steps:"
        job_field "$id" 'job.steps[].[stepName,status]' | sed 's/^/    /' >&2 || true
        err "build logs: Amplify console -> ${APP_NAME} -> ${BRANCH} -> job ${id}"
        return 1 ;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] || { err "job ${id} still ${status} after ${BUILD_TIMEOUT_MIN} min"; return 1; }
    sleep 15
  done
}

# Job queries: no --max-items (with it the CLI appends a pagination-token line
# to text output), and strip the \r the Windows CLI adds.
# Never start a second build while one is in flight - wait for it instead.
RUNNING_JOB="$(aws amplify list-jobs --app-id "$APP_ID" --branch-name "$BRANCH" \
  --query "jobSummaries[?status=='PENDING' || status=='PROVISIONING' || status=='RUNNING' || status=='CANCELLING'] | [0].jobId" \
  --output text 2>/dev/null | tr -d '\r' || echo None)"
if [ -n "$RUNNING_JOB" ] && [ "$RUNNING_JOB" != "None" ]; then
  log "build ${RUNNING_JOB} is already in progress - waiting for it"
  wait_for_job "$RUNNING_JOB" || warn "the in-flight build did not succeed - deciding whether to rebuild"
fi

LAST_OK_COMMIT="$(aws amplify list-jobs --app-id "$APP_ID" --branch-name "$BRANCH" \
  --query "jobSummaries[?status=='SUCCEED'] | [0].commitId" --output text 2>/dev/null | tr -d '\r' || echo None)"

REASON=""
if   [ "$FORCE_REBUILD" = 1 ];              then REASON="--rebuild"
elif [ "$AMPLIFY_CHANGED" = 1 ];            then REASON="Amplify settings changed in this run"
elif [ -z "$LAST_OK_COMMIT" ] || [ "$LAST_OK_COMMIT" = "None" ]; then REASON="no successful build yet"
elif [ "$LAST_OK_COMMIT" != "$REMOTE_HEAD" ]; then REASON="live build is ${LAST_OK_COMMIT:0:7}, branch is ${REMOTE_HEAD:0:7}"
fi

JOB_ID=""
if [ -n "$REASON" ]; then
  log "starting a build: ${REASON}"
  # Name the commit: otherwise the job records "HEAD" and the live-build check
  # above can never match, so every run would rebuild.
  JOB_ID="$(aws amplify start-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-type RELEASE \
             --commit-id "$REMOTE_HEAD" --commit-message "deploy-frontend.sh build of ${REMOTE_HEAD:0:7}" \
             --job-reason "deploy-frontend.sh: ${REASON}" --query 'jobSummary.jobId' --output text | tr -d '\r')" \
    || die "could not start an Amplify build"
  wait_for_job "$JOB_ID" || { write_report "FAILED - Amplify build ${JOB_ID} did not succeed" ""; die "FRONTEND DEPLOY FAILED at the Amplify build - see ${REPORT}"; }
  ok "build ${JOB_ID} succeeded"
else
  ok "the live build is already ${REMOTE_HEAD:0:7} - no build needed (use --rebuild to force one)"
fi

# CloudFront edges can take several minutes after a create or an origin change.
if [ -n "$DIST_ID" ]; then
  if [ "$(aws cloudfront get-distribution --id "$DIST_ID" --query 'Distribution.Status' --output text)" != "Deployed" ]; then
    log "waiting for CloudFront ${DIST_ID} to finish deploying (usually 5-15 minutes)"
    aws cloudfront wait distribution-deployed --id "$DIST_ID" || die "CloudFront ${DIST_ID} did not reach Deployed"
  fi
  ok "CloudFront ${DIST_ID} is Deployed"
fi

# ---------------------------------------------------------------------------
# 9. Verification - every check runs; any failure fails the deploy
# ---------------------------------------------------------------------------
step "Verification"
PASS=0; FAIL=0; RESULTS=""
record() {  # $1 = ok|fail, $2 = check, $3 = detail on failure
  if [ "$1" = ok ]; then ok "$2"; PASS=$((PASS+1)); RESULTS="${RESULTS}| PASS | $2 |\n"
  else err "$2${3:+ - $3}"; FAIL=$((FAIL+1)); RESULTS="${RESULTS}| FAIL | $2${3:+ - $3} |\n"; fi
}
code_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@" || true; }

LATEST="$(aws amplify list-jobs --app-id "$APP_ID" --branch-name "$BRANCH" \
  --query 'jobSummaries[0].[status,commitId]' --output text 2>/dev/null | tr -d '\r' | head -1 || true)"
[ "$(printf '%s' "$LATEST" | cut -f1)" = "SUCCEED" ] \
  && record ok "Amplify: latest build SUCCEED ($(printf '%s' "$LATEST" | cut -f2 | cut -c1-7))" \
  || record fail "Amplify: latest build SUCCEED" "got: ${LATEST:-nothing}"

c="$(code_of "${SITE_URL}/")";       [ "$c" = 200 ] && record ok "HTTPS site ${SITE_URL}/ -> 200" || record fail "HTTPS site ${SITE_URL}/ -> 200" "got ${c}"
c="$(code_of "${SITE_URL}/chat")";   [ "$c" = 200 ] && record ok "page route /chat -> 200" || record fail "page route /chat -> 200" "got ${c}"
# Amplify answers a missing path with redirects (301 trailing slash, then 302 to
# the 404 rule's /404.html), not a literal 404 status. What must hold is that
# the visitor lands on the not-found page, never on an app page.
NF="$(curl -sL -o "${WORK_DIR}/not-found.html" -w '%{url_effective}' --max-time 20 "${SITE_URL}/no-such-page-$$" || true)"
case "$NF" in
  */404.html) grep -q 'This page could not be found' "${WORK_DIR}/not-found.html" \
                && record ok "unknown path -> the 404 page" \
                || record fail "unknown path -> the 404 page" "landed on ${NF} without the not-found text" ;;
  *) record fail "unknown path -> the 404 page" "landed on ${NF:-nothing}" ;;
esac

HEADERS="$(curl -s -D - -o /dev/null --max-time 20 "${SITE_URL}/" | tr -d '\r' || true)"
printf '%s' "$HEADERS" | grep -qi '^strict-transport-security:' && record ok "HSTS header present" || record fail "HSTS header present"
printf '%s' "$HEADERS" | grep -qi '^content-security-policy:.*connect-src' && record ok "CSP header present" || record fail "CSP header present"

REDIRECT="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 20 "http://${SITE_URL#https://}/" || true)"
case "$REDIRECT" in 30[12378]\ https://*) record ok "HTTP redirects to HTTPS" ;; *) record fail "HTTP redirects to HTTPS" "got: ${REDIRECT}" ;; esac

API_HOST="${API_URL#https://}"
BUNDLE_OK=0
for chunk in $(curl -s --max-time 20 "${SITE_URL}/dashboard" | grep -oE '/_next/static/chunks/[A-Za-z0-9/_.~-]+\.js' | sort -u | head -40); do
  if curl -s --max-time 20 "${SITE_URL}${chunk}" | grep -qF "$API_HOST"; then BUNDLE_OK=1; break; fi
done
[ "$BUNDLE_OK" = 1 ] && record ok "deployed bundle calls ${API_URL}" || record fail "deployed bundle calls ${API_URL}" "not found in the dashboard chunks"

HEALTH="$(curl -s --max-time 20 -w ' %{http_code}' "${API_URL}/api/v1/health" || true)"
case "$HEALTH" in *'"status":"ok"'*' 200') record ok "API health through CloudFront -> 200" ;; *) record fail "API health through CloudFront -> 200" "got: ${HEALTH}" ;; esac

ACAO="$(curl -s -D - -o /dev/null --max-time 20 -X OPTIONS "${API_URL}/api/v1/me" \
  -H "Origin: ${SITE_URL}" -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: authorization,x-correlation-id" \
  | tr -d '\r' | sed -n 's/^[Aa]ccess-[Cc]ontrol-[Aa]llow-[Oo]rigin: //p' || true)"
[ "$ACAO" = "$SITE_URL" ] && record ok "CORS preflight from ${SITE_URL} allowed" || record fail "CORS preflight from ${SITE_URL} allowed" "Access-Control-Allow-Origin: '${ACAO}'"

NOAUTH="$(curl -s --max-time 20 -w ' %{http_code}' "${API_URL}/api/v1/me" || true)"
case "$NOAUTH" in *'Authentication required.'*' 401') record ok "API enforces auth (no token -> 401)" ;; *) record fail "API enforces auth (no token -> 401)" "got: ${NOAUTH}" ;; esac

# A different message for a bad token proves CloudFront forwarded Authorization.
BADAUTH="$(curl -s --max-time 20 -w ' %{http_code}' -H 'Authorization: Bearer not-a-real-token' "${API_URL}/api/v1/me" || true)"
case "$BADAUTH" in *'Invalid token.'*' 401') record ok "Authorization header reaches the API through CloudFront" ;; *) record fail "Authorization header reaches the API through CloudFront" "got: ${BADAUTH}" ;; esac

# ---------------------------------------------------------------------------
# 10. Report
# ---------------------------------------------------------------------------
if [ "$FAIL" -eq 0 ]; then
  write_report "SUCCESS - ${PASS} checks passed" "$RESULTS"
else
  write_report "FAILED VERIFICATION - ${FAIL} of $((PASS + FAIL)) checks failed" "$RESULTS"
fi
ok "report written: ${REPORT}"

[ "$FAIL" -eq 0 ] || die "FRONTEND DEPLOY FAILED VERIFICATION (${FAIL} check(s)) - see ${REPORT}"

log "Frontend live: ${SITE_URL}"
log "API:           ${API_URL}"
log "Code changes deploy with: git push origin ${BRANCH}   (Amplify builds automatically)"
cost_reminder
