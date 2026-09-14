#!/usr/bin/env bash
# set-secrets.sh - ONE-TIME, after the baseline apply: give the empty Secrets
# Manager containers created by Terraform a value.
#
#   ./scripts/set-secrets.sh              (run from Git Bash)
#
# Values are generated here and go straight to Secrets Manager through a temp
# file - never through Terraform state, git, a command-line argument, or this
# terminal's output.
#
# A secret that ALREADY has a value is left untouched. Existing values are never
# overwritten to "reset" them - see .claude/rules/secrets-management.md.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

PREFIX="${PROJECT_CODE}/${ENVIRONMENT}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

log "Setting secret values under ${PREFIX}/"
preflight_aws

rand_hex() {
  openssl rand -hex "$1" 2>/dev/null || python -c "import secrets; print(secrets.token_hex($1))"
}

# aws.exe on Windows cannot read a Git Bash /tmp path, so hand it a Windows path.
file_uri() { printf 'file://%s' "$(cygpath -w "$1" 2>/dev/null || printf '%s' "$1")"; }

set_if_empty() {
  local id="${PREFIX}/$1" versions
  versions="$(aws secretsmanager describe-secret --secret-id "$id" --region "$AWS_REGION" \
      --query 'VersionIdsToStages' --output text 2>/dev/null)" \
    || die "secret ${id} not found - apply envs/baseline first"

  if [ -n "$versions" ] && [ "$versions" != "None" ]; then
    ok "${id}: already has a value - left untouched"
    return 0
  fi

  printf '%s' "$2" > "$TMP"
  aws secretsmanager put-secret-value --secret-id "$id" --region "$AWS_REGION" \
    --secret-string "$(file_uri "$TMP")" >/dev/null || die "could not set ${id}"
  : > "$TMP"
  ok "${id}: value set"
}

# The database secret is JSON. Only its "password" key is used, in two places,
# so they can never drift apart:
#   RDS master password   Terraform reads it ephemerally (write-only, never in state)
#   api container         <arn>:password:: injected as DB_PASSWORD; the container
#                         builds DATABASE_URL from it plus the RDS endpoint
# Hex keeps the password URL-safe. Secrets created before the move to RDS also
# carry a "url" key for the old Postgres sidecar; nothing reads it any more.
PGPASS="$(rand_hex 24)"
set_if_empty "backend/database-url" "{\"password\":\"${PGPASS}\"}"
unset PGPASS

set_if_empty "backend/qdrant-api-key"   "$(rand_hex 32)"
set_if_empty "backend/dev-auth-secret"  "$(rand_hex 32)"   # unused on AWS: DEV_AUTH_ENABLED=false
# ECS refuses to start a task whose secret has no value, so this needs one even
# while LangSmith tracing is off. Replace it with a real key if you enable tracing.
set_if_empty "ai/langsmith-api-key"     "not-configured"
# Placeholders only - real Cohere/Groq values are set out-of-band for live demos.
set_if_empty "ai/cohere-api-key"        "not-configured"
set_if_empty "ai/groq-api-key"          "not-configured"

REPORT="$(report_path set-secrets)"
report_header "$REPORT" "Secrets Setup"
{
  printf '## Result\n\nAll four secrets under `%s/` have a value.\n\n' "$PREFIX"
  printf 'Secret VALUES are never written to reports.\n'
} >> "$REPORT"
ok "report written: ${REPORT}"
