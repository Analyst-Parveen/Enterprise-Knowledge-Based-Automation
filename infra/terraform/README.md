# Terraform

Four separate states, deliberately, plus a state backend created outside
Terraform by `scripts/bootstrap-state.sh`:

| State | Key in `s3://ekba-tfstate-<account-id>` | Lifecycle tag | Destroyed by `destroy.sh`? |
|---|---|---|---|
| `envs/baseline/` | `baseline/terraform.tfstate` | `protected` | Never |
| `envs/cost-guard/` | `cost-guard/terraform.tfstate` | `protected` | Never |
| `envs/dev/` | `dev/terraform.tfstate` | `ephemeral` | Yes — the only state it targets |
| `envs/frontend/` | `frontend/terraform.tfstate` | `protected` | Never — driven by `scripts/deploy-frontend.sh` |

All three lock through the DynamoDB table `ekba-tfstate-lock` and refuse to plan
against any account other than `expected_aws_account_id`. Each directory has a
git-ignored `backend.hcl` (it holds the account ID) and a git-ignored
`terraform.tfvars`; copy the committed `terraform.tfvars.example`.

## `envs/baseline/` — PROTECTED, never destroyed

ECR repositories (backend, frontend), S3 document bucket, Cognito user pool and
web client, four Secrets Manager secret containers, the `ekba-dev-monthly`
budget, the retained audit log group, and the GitHub OIDC deploy role.

Everything here is tagged `Lifecycle = "protected"`. `scripts/destroy.sh` does
not point at this state and cannot reach it. Apply it once, then leave it alone.

`ekba-dev-monthly` ($20, alerts at 50/80/100% and a 100% forecast) uses the AWS
default of **including credits**, so it reads $0 for as long as credits last. It
alerts only once spend reaches the card. The cost guard below is the protection
that measures usage before credits.

## `envs/cost-guard/` — PROTECTED, always on

The kill switch. It must be armed whenever the ephemeral environment exists, so
it cannot live in `envs/dev` (destroy would remove it), and it is kept out of
`envs/baseline` so adding it changed nothing there.

**Status (2026-09-11):** applied — 15 resources — with **`dry_run = true`**. It
reports what it would do and changes nothing. Arming it is a deliberate change
to `dry_run = false`.

### How it works

```
Budget ekba-dev-credit-guard  (gross, credits EXCLUDED) ──┐
Budget ekba-dev-card-charge-guard (net, credits INCLUDED) ─┼─► SNS ekba-dev-cost-guard-trigger ─┐
EventBridge ekba-dev-cost-guard-session-limit (hourly) ───────────────────────────────────────┼─► Lambda ekba-dev-cost-guard
                                                                                               │        │
Budget alert emails ◄── sent directly by AWS Budgets                                           │        ▼
                                                                          SNS ekba-dev-cost-guard-notify ─► email report
```

| Trigger | Threshold | Action |
|---|---|---|
| `ekba-dev-credit-guard`: cumulative gross usage since `tracking_start` (2026-09-01), credits and refunds excluded, `ANNUALLY` | > $10, > $15 | Email |
| same | forecast > $18 | Email |
| same | **> $18** (`shutdown_usd`) | Email + **shutdown** |
| `ekba-dev-card-charge-guard`: monthly spend after credits | **> $0.01** | Email + **shutdown** |
| Hourly schedule | environment older than **8 h** (`max_session_hours`) | **Shutdown** (email only when armed) |

"Shutdown" is exactly two actions, for resources that pass **all three**
ownership signals — name `ekba-dev` or `ekba-dev-*`, tags
`ProjectCode=ekba` + `Environment=dev` + `Lifecycle=ephemeral`, and presence in
`s3://ekba-tfstate-<account-id>/dev/terraform.tfstate`:

| Resource | Action |
|---|---|
| ECS service `ekba-dev/ekba-dev` | Scaled to **0 tasks** (not deleted) |
| ALB `ekba-dev-alb` and its listeners | **Deleted** — an ALB cannot be paused, and an idle one costs more per month than the whole ceiling |

Everything else in `envs/dev` (VPC, subnets, security groups, target groups,
cluster, task definitions, IAM roles, log group, alarms) costs nothing at rest
and is left for `destroy.sh`. The baseline, the state backend and every resource
outside this project are never touched. The Lambda never runs `terraform
destroy`.

IAM enforces the same boundary independently of the code: the role's only
mutating permissions are `ecs:UpdateService` and
`elasticloadbalancing:DeleteLoadBalancer`, restricted to `ekba-dev` ARNs **and**
the three ephemeral tags. IAM Access Analyzer reported 0 findings for the policy,
and the IAM policy simulator allowed only those two actions on `ekba-dev`
resources tagged `ephemeral`, and denied the same actions on `protected`-tagged
or foreign resources, and every other action tested.

Session age is measured from the earliest creation time of the billable
resources (the ALB's `CreatedTime`, the ECS service's `createdAt`), so
redeploying without destroying does **not** reset it.

`dry_run` is fail-safe: any value other than `false` means dry run. In dry-run
the hourly check only logs, so it does not email every hour.

### Operating it

```bash
# Test (dry-run reports and changes nothing)
aws lambda invoke --function-name ekba-dev-cost-guard \
  --cli-binary-format raw-in-base64-out --payload '{"trigger":"manual"}' out.json
# {"trigger":"session-limit"} tests the 8-hour check

# Is it armed?
./scripts/cost-check.sh

# Its logs
MSYS_NO_PATHCONV=1 aws logs tail /aws/lambda/ekba-dev-cost-guard --since 1h
```

Dry-run results on 2026-09-11 (all three trigger paths — direct `manual`, direct
`session-limit`, and a test message through the SNS trigger topic):

```
WOULD scale ECS service ekba-dev/ekba-dev from 1 to 0 tasks
WOULD delete load balancer ekba-dev-alb (and its listeners)
refused: none   errors: none
session-limit: "environment has existed 18.4h, limit is 8h"
```

After an automatic shutdown: run `scripts/destroy.sh`, then `scripts/deploy.sh`.
`deploy.sh` on its own would leave the service at 0 tasks, because Terraform
ignores `desired_count`.

`scripts/deploy.sh` refuses to deploy at the same threshold
(`COST_GUARD_SHUTDOWN_USD`, default 18, gross usage since `COST_GUARD_START`),
so a deploy cannot restart what the guard just stopped.

### Not yet proven

- A **live** shutdown has not run. The dry run does not call ECS, so scaling a
  CodeDeploy-controlled service to 0 is supported by AWS documentation and the
  IAM simulation, not yet by an actual run.
- Shutdown reports reach email only after the SNS subscription is confirmed
  (link emailed to `alert_email`).
- Billing data lags: Budgets refresh a few times a day and cost data can trail by
  up to ~24 h, which is why the shutdown sits $2 below the $20 ceiling.

Cost at rest: ~$0/month — the budgets are notification-only, and SNS, Lambda and
EventBridge stay inside the free tiers.

The Lambda's unit tests (27) run with the backend venv:

```bash
backend/.venv/Scripts/python.exe -m pytest infra/terraform/modules/cost-guard/lambda/tests -q
```

## `envs/dev/` — EPHEMERAL, destroyed after every demo

VPC (`10.42.0.0/16`) with two public subnets and an internet gateway — no NAT
Gateway — security groups, the ALB with a production listener (`:80`) and a test
listener (`:8080`), blue and green target groups, the ECS cluster, task
definition and service (`CODE_DEPLOY` deployment controller), the CodeDeploy
application and deployment group, task IAM roles, a 1-day log group, and the
`ekba-dev-5xx` / `ekba-dev-latency` alarms.

One Fargate task (1 vCPU / 3 GB, x86_64) runs three containers: the API, Qdrant
and Redis. It has a public IP so it reaches ECR and Bedrock without a NAT
Gateway; its security group admits only the ALB.

PostgreSQL is **Amazon RDS** (`modules/database`): `ekba-dev-postgres`,
`db.t4g.micro`, single-AZ, 20 GB gp3, encrypted, in two private subnets with no
internet route, `publicly_accessible = false`, and a security group that admits
port 5432 from the task security group only. The master password is the
`password` key of `ekba/dev/backend/database-url`, read through an **ephemeral**
Secrets Manager resource into the **write-only** `password_wo` argument, so no
password is ever written to state or to a plan. The API container assembles
`DATABASE_URL` at startup from `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER` plus the
injected `DB_PASSWORD`, and connects with `ssl=require`.

Its data survives task replacement and every deploy. Between sessions it
survives as a **manual snapshot**: `destroy.sh` takes one before destroying
(and aborts if it cannot), and `deploy.sh` passes the newest one as
`-var restore_snapshot_id=…` when the instance does not exist. The instance
ignores later changes to that variable, so a newer snapshot never replaces a
running database. Creating or restoring the instance adds ~10 minutes to a
deploy.

The ALB security group admits **only `allowed_cidrs`** (ports 80 and 443) — a
single `/32` in `terraform.tfvars`. Port 8080 is open to no one. When the
operator's public IP changes, the app becomes unreachable from their machine
and `verify.sh` fails, even though the ALB's own health checks pass. Update
`allowed_cidrs` and redeploy (a one-resource, in-place change).

Everything here is tagged `Lifecycle = "ephemeral"` and costs ~$0.11/hour while
it exists (list-price estimate: Fargate ~$0.054, ALB ~$0.023 + LCUs, three public
IPv4 addresses ~$0.015, RDS ~$0.016 + ~$0.003 storage). Left running 24/7 the
database alone would be ~$14/month, which is why nothing here is meant to
outlive a session. `scripts/destroy.sh` targets this state only.

The `estimated_charges` billing alarm in `modules/service` is created only when
the region is `us-east-1` (billing metrics exist only there), so it does not
exist in this `us-west-2` deployment. The cost guard covers spend instead.

Apply this state only through `scripts/deploy.sh`: the ECS service needs an image
that already exists in ECR, and `deploy.sh` builds it, pushes it and passes it as
`-var backend_image=…`.

Two variables wire the Amplify frontend in, and neither belongs in
`terraform.tfvars`: `scripts/deploy-frontend.sh` writes them to
`frontend.auto.tfvars` (git-ignored), which Terraform loads automatically, so
every later `deploy.sh` keeps them.

| Variable | Effect |
|---|---|
| `cloudfront_origin_ingress` | Adds a port-80 ingress rule from the CloudFront origin-facing managed prefix list (46 entries against the 60-rule quota) |
| `amplify_origin` | Appended to `CORS_ALLOWED_ORIGINS`; a change registers a new task-definition revision, released through CodeDeploy |

## `envs/frontend/` — persistent, ~$0 idle

AWS Amplify Hosting for the static Next.js export, plus a CloudFront
distribution that gives the HTTP-only ALB an HTTPS URL. Module:
`modules/frontend`. Always driven by `scripts/deploy-frontend.sh`, which looks up
two values on every run and passes them with `-var`, so they never go stale:

| Variable | Source |
|---|---|
| `api_origin_domain` | `app_url` output of `envs/dev`, cross-checked against the live ALB |
| `amplify_app_id` | The Amplify app named `ekba-dev-frontend`; empty until the console connection |

| Resource | Notes |
|---|---|
| `aws_cloudfront_cache_policy.api` (`ekba-dev-api-no-cache`) | TTL 0 (max 1 s), `Authorization` in the cache key — forwarded on every method, never shared between users |
| `aws_cloudfront_distribution.api` | Default certificate, HTTPS only, origin = the ALB over HTTP, all methods, `Managed-AllViewerExceptHostHeader`, 60 s origin timeout |
| `aws_amplify_app.this` / `aws_amplify_branch.this` | Created by the console's GitHub App connection, then **imported** through `import` blocks — the plan shows `will be imported` first. Platform `WEB`, env vars (`AMPLIFY_MONOREPO_APP_ROOT`, `NEXT_PUBLIC_API_URL`), security headers, a 404 rule, auto-build on push. `build_spec` is ignored: `amplify.yml` in the repository is the spec |

No GitHub token passes through Terraform state. The CloudFront URL stays stable
when `envs/dev` is recreated — rerun `deploy-frontend.sh` to repoint the origin.
**Status (2026-09-11): not applied.** The last `--plan-only` run showed
`2 to add, 0 to change, 0 to destroy`.

## Why separate states

A single state would put secrets and ECR images one `terraform destroy` away
from deletion. Splitting them makes the safety rule structural rather than
procedural: the destroy workflow simply has no reference to the protected
resources. The cost guard is separate for the same reason in reverse — it must
outlive every destroy of the environment it protects.

## Order

```
1. scripts/bootstrap-state.sh                       # once: state bucket, lock table, backend.hcl (baseline, dev)
2. terraform -chdir=envs/baseline init && apply     # once, ever
3. scripts/set-secrets.sh                           # once
4. terraform -chdir=envs/cost-guard init && apply   # once; needs a hand-written backend.hcl; test in dry-run
5. scripts/deploy.sh                                # per demo (applies envs/dev)
6. scripts/deploy-frontend.sh                       # once, then after every new ALB (envs/frontend)
7. scripts/destroy.sh                               # after the demo
8. go to 5
```
