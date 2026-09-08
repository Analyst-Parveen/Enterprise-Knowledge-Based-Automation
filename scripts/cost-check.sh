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
# 1. Month-to-date spend
# ---------------------------------------------------------------------------
step "Month-to-date spend"
SPEND="$(month_to_date_spend)"
if [ "$SPEND" = "unknown" ]; then
  warn "Cost Explorer unavailable - check the Billing console manually"
else
  REMAINING="$(awk -v s="$SPEND" -v m="$MAX_MONTHLY_SPEND_USD" 'BEGIN{printf "%.2f", m-s}')"
  printf '    spent:     $%.2f\n' "$SPEND"
  printf '    ceiling:   $%s\n'   "$MAX_MONTHLY_SPEND_USD"
  printf '    remaining: $%s\n'   "$REMAINING"

  OVER="$(awk -v s="$SPEND" -v m="$MAX_MONTHLY_SPEND_USD" 'BEGIN{print (s+0 >= m+0) ? 1 : 0}')"
  NEAR="$(awk -v s="$SPEND" -v m="$MAX_MONTHLY_SPEND_USD" 'BEGIN{print (s+0 >= m*0.8) ? 1 : 0}')"
  if [ "$OVER" = "1" ];  then err "CEILING REACHED - deploys are blocked. Destroy and review."
  elif [ "$NEAR" = "1" ]; then warn "over 80% of the ceiling used"
  else ok "within budget"; fi
fi

# ---------------------------------------------------------------------------
# 2. What is running billable right now
# ---------------------------------------------------------------------------
step "Billable resources currently running (ProjectCode=${PROJECT_CODE})"

RUNNING=0

# ECS services / tasks
TASKS="$(aws ecs list-tasks --cluster "${PROJECT_CODE}-${ENVIRONMENT}" \
          --query 'length(taskArns)' --output text 2>/dev/null || echo 0)"
if [ "${TASKS:-0}" != "0" ] && [ "${TASKS}" != "None" ]; then
  warn "ECS tasks running: ${TASKS}  (~\$0.049/hr each)"
  RUNNING=1
fi

# Load balancers
LBS="$(aws elbv2 describe-load-balancers \
        --query "length(LoadBalancers[?starts_with(LoadBalancerName, '${PROJECT_CODE}-${ENVIRONMENT}')])" \
        --output text 2>/dev/null || echo 0)"
if [ "${LBS:-0}" != "0" ] && [ "${LBS}" != "None" ]; then
  warn "Load balancers running: ${LBS}  (~\$0.023/hr each)"
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
