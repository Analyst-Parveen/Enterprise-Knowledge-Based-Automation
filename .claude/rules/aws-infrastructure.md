# Rule: AWS Infrastructure

## 1. Preflight — required before ANY infrastructure action

No AWS mutation happens until all five checks pass:

```bash
aws sts get-caller-identity          # 1. account ID matches EXPECTED_AWS_ACCOUNT_ID
echo "$AWS_REGION"                   # 2. region matches the project region
terraform workspace show             # 3. workspace matches the target environment
terraform state list | head          # 4. state contains this project's resources
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=ProjectCode,Values=ekba   # 5. tag ownership confirmed
```

If any check fails or is ambiguous: **stop and ask the user.** Never guess.

## 2. Blast-radius rules

- Only ever touch resources that are simultaneously (a) in this project's
  Terraform state, (b) named with the `ekba-<env>-` prefix, and (c) tagged
  `ProjectCode=ekba`.
- Never run a destructive AWS CLI command against a resource this project does
  not own. This includes `delete-*`, `terminate-*`, `remove-*`, `put-*` that
  overwrites, and any `--force` variant.
- Never operate on a wildcard scope: no "delete all buckets", no "terminate all
  instances", no untagged sweeps.
- Prefer Terraform over the CLI for anything that changes state. CLI mutations
  create drift.
- Read-only AWS CLI commands (`describe-*`, `list-*`, `get-*`) are always fine.

## 3. Least-privilege IAM

- One role per component (API service, ingestion worker, CI deploy role).
- Policies scope to specific ARNs, never `Resource: "*"`, except where the API
  genuinely requires it (and then with a condition key).
- No long-lived IAM user access keys for services. Use roles: IAM roles for tasks
  in ECS, OIDC federation for GitHub Actions.
- CI deploy role is scoped to this project's resources and cannot touch IAM
  outside its own scope.

## 4. Network and data protection

- The database (RDS PostgreSQL) is in private subnets with no internet route,
  `publicly_accessible = false`, and a security group that admits PostgreSQL
  only from the task security group. Qdrant and Redis are sidecars inside the
  task and are not reachable from outside it at all.
- Security groups reference other security groups, not `0.0.0.0/0`, except for the
  single public ingress on 443.
- S3: all public access blocked, SSE encryption enabled, versioning on for
  document buckets, TLS-only bucket policy.
- Secrets from AWS Secrets Manager, injected at runtime. See
  [secrets-management.md](secrets-management.md).
- CloudWatch log groups have explicit, short retention in ephemeral environments.

## 5. Cost strategy — $20 HARD CEILING

The account holds promotional credits ($158.99 at the last check, 2026-09-11)
and is on the **Paid** plan — the Free plan rejects CodeDeploy with
`SubscriptionRequiredException`. **The ceiling is $20, measured gross of
credits.** Treat it as a hard limit, not a target to approach. The architecture
must be cheap by construction, not by remembering to turn things off.

Credits pay for usage first. On the Paid plan anything they do not cover — or
anything after they run out or expire — is charged to the card. A budget that
includes credits (the AWS default) reads $0 until that moment, so every cost
control here measures usage before credits.

### The only two environments

| Environment | Where | Cost |
|---|---|---|
| Local — all development | Docker Compose | **$0** |
| AWS demo — deployed on demand | ECS Fargate + RDS, ephemeral | ~$0.11 / hour (~$0.44 per 4-hour session) |

**Local-first is mandatory.** Every feature is built and tested on Docker Compose
(PostgreSQL, Qdrant, Redis, API, frontend) before AWS is involved at all. AWS
exists to prove the deployment story and run a live demo — nothing else.

### What the ephemeral stack contains

ALB · ECS Fargate task (API + Qdrant + Redis containers, 1 vCPU / 3 GB) · RDS
PostgreSQL (`db.t4g.micro`, 20 GB gp3, single-AZ, private) · ECR · S3 · Cognito
· CloudWatch Logs (1-day retention) · Bedrock · Transcribe.

Roughly **$0.11/hour** (list-price estimate: Fargate ~$0.054, ALB ~$0.023 plus
LCUs, three public IPv4 addresses ~$0.015, RDS ~$0.016 + storage ~$0.003), so a
4-hour session is about **$0.44**. The protected baseline adds about
$1.70/month at rest, and database snapshots kept between sessions a few cents.

**RDS, if left running 24/7: ~$14/month** ($11.68 instance + $2.30 storage) —
it would reach the $18 shutdown threshold within weeks. That is why it lives in
the ephemeral stack: it exists only between `deploy.sh` and `destroy.sh`, and
the cost guard stops it (never deletes it) on a budget or session-limit breach.

### Never provisioned — in any environment

| Excluded | Cost avoided | Instead |
|---|---|---|
| **NAT Gateway** | ~$32/mo + data | Fargate in a public subnet with a public IP reaches ECR directly |
| **RDS running 24/7** | ~$14/mo | One `db.t4g.micro` in the ephemeral stack only; data kept as a snapshot between sessions |
| **ElastiCache** | continuous | Redis container |
| **EKS** | ~$73/mo control plane | ECS Fargate tells the same story for free |
| **Multi-AZ / larger RDS / Aurora** | 2x+ | Single-AZ `db.t4g.micro` |
| **EFS / persistent volumes** | continuous | Qdrant/Redis data is ephemeral by design |
| **Idle ALB / compute** | ~$16/mo each | Nothing billable survives `destroy.sh` |

### Rules

- The environment is **ephemeral by design**: `setup -> test -> demo -> DESTROY ->
  setup again` must work repeatedly. `destroy.sh` is the normal end of a session,
  not an emergency measure.
- **PostgreSQL data survives** task replacement, every deploy, and — through the
  snapshot `destroy.sh` takes and `deploy.sh` restores — a destroy. Qdrant
  vectors and Redis cache are still lost on every task replacement; the seed
  re-indexes the demo documents at startup. (Documents uploaded on AWS keep
  their Postgres rows and S3 files but lose their vectors until re-ingested.)
- An AWS Budget of $20 with alerts at 50/80/100% is part of the protected
  baseline. It includes credits, so it alerts only once spend reaches the card.
- The **cost guard** (`infra/terraform/envs/cost-guard`, protected) stops the
  ephemeral stack — ECS scaled to zero, ALB deleted, RDS stopped (never
  deleted) — at $18 of gross usage, on
  any charge credits did not cover, or after 8 hours, with email warnings at $10
  and $15. It runs **in dry-run** until the user explicitly approves arming it.
- `deploy.sh` refuses to deploy once gross usage since `COST_GUARD_START`
  reaches `COST_GUARD_SHUTDOWN_USD` ($18).
- Before adding **any** AWS resource, state its cost at rest. If it cannot be
  justified inside $20, it does not go in. This applies to "just a small" anything.
- If a task would leave something billable running, say so explicitly before doing
  it.

## 6. Protected baseline

These resources are created once, tagged `Lifecycle = "protected"`, and are
**never** destroyed by project scripts:

- Terraform state bucket and lock table
- AWS Secrets Manager secrets
- ECR repository (images needed to redeploy)
- Budget and billing alarms
- Audit log groups with retention requirements
- The cost guard (`envs/cost-guard`): its budgets, SNS topics, Lambda and
  schedule. It must outlive every destroy of the environment it protects.

## 7. Observability requirements

- CloudWatch metrics and structured JSON logs for requests, latency, tokens,
  estimated cost, cache hits, retrieval performance, LLM performance, ingestion
  failures, and security events.
- Alarms on error rate, latency, and **estimated spend**. (Status: the
  `EstimatedCharges` alarm is created only in `us-east-1`, where billing metrics
  live, so it does not exist in the `us-west-2` deployment; spend is covered by
  the cost-guard budgets instead.)
- The correlation ID appears in every log line so a request can be traced from
  the browser through to LangSmith.

## 8. Escalation

Stop and ask the user before:

- Any action in an unverified account or region.
- Any change touching untagged or unknown resources.
- Any IAM change outside this project's roles.
- Any deletion of data (S3 objects, database, snapshots).
- Anything that would raise standing monthly cost.
