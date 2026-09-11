#!/usr/bin/env bash
# cost-check.sh - Report month-to-date spend and what is running billable RIGHT NOW.
#
# Run this before a demo, after a demo, and any time you are unsure whether
# something was left running.
#
# SAFETY: strictly READ-ONLY. Describes and reports. Mutates nothing.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
forbid_destroy

log "Cost check: ${PROJECT_NAME} [${ENVIRONMENT}]  ceiling \$${MAX_MONTHLY_SPEND_USD}"

preflight_aws

# ---------------------------------------------------------------------------
# 1. Spend since COST_GUARD_START - gross, credits, and what reaches the card
# ---------------------------------------------------------------------------
step "Spend since ${COST_GUARD_START}"
SPEND="$(gross_usage_since_start)"
CREDITS="$(credits_since_start)"
if [ "$SPEND" = "unknown" ]; then
  warn "Cost Explorer unavailable - check the Billing console manually"
else
  [ "$CREDITS" = "unknown" ] && CREDITS=0
  NET="$(awk -v s="$SPEND" -v c="$CREDITS" 'BEGIN{n = s + c; printf "%.2f", (n < 0 ? 0 : n)}')"
  REMAINING="$(awk -v s="$SPEND" -v m="$COST_GUARD_SHUTDOWN_USD" 'BEGIN{printf "%.2f", m-s}')"
  printf '    gross usage:         $%s\n' "$SPEND"
  printf '    covered by credits:  $%s\n' "$CREDITS"
  printf '    net (card):          $%s\n' "$NET"
  printf '    auto-shutdown at:    $%s gross   (remaining $%s)\n' "$COST_GUARD_SHUTDOWN_USD" "$REMAINING"

  OVER="$(awk -v s="$SPEND" -v m="$COST_GUARD_SHUTDOWN_USD" 'BEGIN{print (s+0 >= m+0) ? 1 : 0}')"
  NEAR="$(awk -v s="$SPEND" -v m="$COST_GUARD_SHUTDOWN_USD" 'BEGIN{print (s+0 >= m*0.8) ? 1 : 0}')"
  if [ "$OVER" = "1" ];  then err "SHUTDOWN THRESHOLD REACHED - deploys are blocked. Destroy and review."
  elif [ "$NEAR" = "1" ]; then warn "over 80% of the shutdown threshold used"
  else ok "within budget"; fi

  CARD="$(awk -v n="$NET" 'BEGIN{print (n+0 > 0) ? 1 : 0}')"
  [ "$CARD" = "1" ] && err "charges NOT covered by credits: \$${NET} - these reach the card"
fi

# ---------------------------------------------------------------------------
# 1b. Is the kill switch armed?
# ---------------------------------------------------------------------------
step "Cost guard"
GUARD_FN="${PROJECT_CODE}-${ENVIRONMENT}-cost-guard"
DRY_RUN="$(aws lambda get-function-configuration --function-name "$GUARD_FN" \
             --query 'Environment.Variables.DRY_RUN' --output text 2>/dev/null || echo missing)"
case "$DRY_RUN" in
  false)   ok "kill switch ${GUARD_FN} is ARMED" ;;
  missing) err "kill switch ${GUARD_FN} not found - apply infra/terraform/envs/cost-guard" ;;
  *)       warn "kill switch ${GUARD_FN} is in DRY RUN - it reports but will not stop anything" ;;
esac

# ---------------------------------------------------------------------------
# 2. What is running billable right now
# ---------------------------------------------------------------------------
step "Billable resources currently running (ProjectCode=${PROJECT_CODE})"

RUNNING=0

# ECS services / tasks
TASKS="$(aws ecs list-tasks --cluster "${PROJECT_CODE}-${ENVIRONMENT}" \
          --query 'length(taskArns)' --output text 2>/dev/null || echo 0)"
if [ "${TASKS:-0}" != "0" ] && [ "${TASKS}" != "None" ]; then
  warn "ECS tasks running: ${TASKS}  (~\$0.059/hr each, incl. public IPv4)"
  RUNNING=1
fi

# Load balancers
LBS="$(aws elbv2 describe-load-balancers \
        --query "length(LoadBalancers[?starts_with(LoadBalancerName, '${PROJECT_CODE}-${ENVIRONMENT}')])" \
        --output text 2>/dev/null || echo 0)"
if [ "${LBS:-0}" != "0" ] && [ "${LBS}" != "None" ]; then
  warn "Load balancers running: ${LBS}  (~\$0.033/hr each, incl. 2 public IPv4)"
  RUNNING=1
fi

# NAT gateways - should ALWAYS be zero in this project
NATS="$(aws ec2 describe-nat-gateways \
         --filter "Name=state,Values=available" \
         --query 'length(NatGateways)' --output text 2>/dev/null || echo 0)"
if [ "${NATS:-0}" != "0" ] && [ "${NATS}" != "None" ]; then
  err "NAT Gateways found: ${NATS} (~\$0.045/hr each)"
  err "This project must NEVER create a NAT Gateway - investigate before it burns budget."
  RUNNING=1
fi

# RDS - should ALWAYS be zero in this project
DBS="$(aws rds describe-db-instances \
        --query "length(DBInstances[?starts_with(DBInstanceIdentifier, '${PROJECT_CODE}')])" \
        --output text 2>/dev/null || echo 0)"
if [ "${DBS:-0}" != "0" ] && [ "${DBS}" != "None" ]; then
  err "RDS instances found: ${DBS}. This project uses a Postgres CONTAINER, not RDS."
  RUNNING=1
fi

if [ "$RUNNING" = "0" ]; then
  ok "nothing billable running for this project - \$0/hour"
else
  cost_reminder
fi

# ---------------------------------------------------------------------------
# 3. Bedrock / Transcribe usage this month
# ---------------------------------------------------------------------------
step "AI service spend this month"
aws ce get-cost-and-usage \
    --time-period "Start=$(date -u +%Y-%m-01),End=$(date -u +%Y-%m-%d)" \
    --granularity MONTHLY --metrics UnblendedCost \
    --group-by Type=DIMENSION,Key=SERVICE \
    --query 'ResultsByTime[0].Groups[?contains(Keys[0], `Bedrock`) || contains(Keys[0], `Transcribe`)].[Keys[0],Metrics.UnblendedCost.Amount]' \
    --output table 2>/dev/null || warn "per-service breakdown unavailable"

log "Cost check complete."
