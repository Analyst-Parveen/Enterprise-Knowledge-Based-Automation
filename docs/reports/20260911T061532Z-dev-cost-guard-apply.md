# Cost Guard - Applied (DRY RUN)

| Field | Value |
|---|---|
| Timestamp (UTC) | 20260911T061532Z |
| Project | enterprise-knowledge-based-automation |
| Environment | dev |
| Git SHA | 89f623b (plus uncommitted cost-guard changes) |
| AWS account | 890290782965 |
| AWS region | us-west-2 |

## Result

The reviewed plan file was applied unchanged:

```
Apply complete! Resources: 15 added, 0 changed, 0 destroyed.
```

`DRY_RUN = true`. The kill switch reports; it does not act. The application
and deployment infrastructure (envs/dev, envs/baseline) were not modified.

## Live configuration

| Item | Value |
|---|---|
| Lambda `ekba-dev-cost-guard` | Active, python3.12, `DRY_RUN=true`, `MAX_SESSION_HOURS=8` |
| Budget `ekba-dev-credit-guard` | $20, ANNUALLY from 2026-09-01, credits excluded - actual $1.024 |
| Budget `ekba-dev-card-charge-guard` | MONTHLY, credits included, > $0.01 - actual $0.00 |
| Schedule `ekba-dev-cost-guard-session-limit` | ENABLED, rate(1 hour) |
| Email subscription (notify topic) | PendingConfirmation - requires clicking the emailed link |

## Dry-run tests

| Trigger | Path | Result |
|---|---|---|
| manual | direct invoke | dry-run, 2 actions, 0 refused, 0 errors |
| session-limit | direct invoke | dry-run - environment existed 18.4h, limit 8h |
| budget | SNS publish to trigger topic -> Lambda | dry-run, 2 actions, 0 refused, 0 errors |

Actions reported by every trigger (none executed):

- WOULD scale ECS service `ekba-dev/ekba-dev` from 1 to 0 tasks
- WOULD delete load balancer `ekba-dev-alb` (and its listeners)

After the tests: ECS 1/1 running, ALB active, `/api/v1/health` HTTP 200.

## Pending

Disabling dry-run requires explicit approval. When disabled, the first
hourly session check will stop the current environment (already past 8h).

## Estimated cost impact

~$0.00/month (notification-only budgets; SNS, Lambda and EventBridge within
free tiers).
