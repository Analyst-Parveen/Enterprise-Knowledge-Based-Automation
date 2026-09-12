# Cost Guard - Plan (not applied)

| Field | Value |
|---|---|
| Timestamp (UTC) | 20260911T054447Z |
| Project | enterprise-knowledge-based-automation |
| Environment | dev |
| Git SHA | 89f623b (plus uncommitted cost-guard changes) |
| AWS account | 890290782965 |
| AWS region | us-west-2 |

## Result

Plan produced for the new `infra/terraform/envs/cost-guard` state. **Nothing
applied.** Awaiting approval.

```
Plan: 15 to add, 0 to change, 0 to destroy.
```

## Why

Every existing budget included credits, so spend read $0.00 while gross usage
was $0.91 for the month; no alert could fire until credits were exhausted.
`deploy.sh` had the same blind spot. No automatic shutdown existed.

## Approved configuration

| Setting | Value |
|---|---|
| Shutdown | cumulative gross usage (credits excluded) > $18 since 2026-09-01 |
| Warnings | $10, $15 (email), forecast > $18 (email) |
| Card-charge tripwire | net (after-credit) spend > $0.01 in a month -> shutdown |
| Max session | 8 hours, checked hourly |
| Alerts | email to the address in the git-ignored tfvars |
| Mode | `dry_run = true` |

## Stopped on trigger (dev only, after name + tag + state ownership checks)

- ECS service `ekba-dev/ekba-dev` scaled to 0 tasks (not deleted)
- ALB `ekba-dev-alb` deleted, with its listeners

## Never touched

Baseline state (ECR, S3 documents, Cognito, secrets, audit logs, OIDC role,
`ekba-dev-monthly` budget), Terraform state backend, `My Zero-Spend Budget`,
and everything outside this project.

## Checks run

| Check | Result |
|---|---|
| Lambda unit tests (27) | passed |
| ruff format + check, mypy --strict | clean |
| terraform fmt -check -recursive | clean |
| terraform validate | valid |
| IAM Access Analyzer validate-policy | 0 findings |
| IAM policy simulation (12 cases) | only ekba-dev + ephemeral-tagged scale/delete allowed |
| tflint / checkov | not installed - not run |

## Estimated cost impact

~$0.00/month (notification-only budgets are free; SNS, Lambda, EventBridge
within free tiers).
