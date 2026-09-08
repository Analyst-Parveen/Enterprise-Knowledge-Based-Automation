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

- Database and Qdrant are in private subnets, reachable only from the application
  security group.
- Security groups reference other security groups, not `0.0.0.0/0`, except for the
  single public ingress on 443.
- S3: all public access blocked, SSE encryption enabled, versioning on for
  document buckets, TLS-only bucket policy.
- Secrets from AWS Secrets Manager, injected at runtime. See
  [secrets-management.md](secrets-management.md).
- CloudWatch log groups have explicit, short retention in ephemeral environments.

## 5. Cost strategy — $20 HARD CEILING

$140 of credit exists. **The ceiling is $20.** Treat it as a hard limit, not a
target to approach. The architecture must be cheap by construction, not by
remembering to turn things off.

### The only two environments

| Environment | Where | Cost |
|---|---|---|
| Local — all development | Docker Compose | **$0** |
| AWS demo — deployed on demand | ECS Fargate, ephemeral | ~$0.30 / session |

**Local-first is mandatory.** Every feature is built and tested on Docker Compose
(PostgreSQL, Qdrant, Redis, API, frontend) before AWS is involved at all. AWS
exists to prove the deployment story and run a live demo — nothing else.

### What the ephemeral stack contains

ALB · ECS Fargate task (API + Postgres + Qdrant + Redis containers, 1 vCPU / 2 GB)
· ECR · S3 · Cognito · CloudWatch Logs (1-day retention) · Bedrock · Transcribe.

Roughly **$0.072/hour**, so a 4-hour session is about **$0.30**.

### Never provisioned — in any environment

| Excluded | Cost avoided | Instead |
|---|---|---|
| **NAT Gateway** | ~$32/mo + data | Fargate in a public subnet with a public IP reaches ECR directly |
| **RDS** | continuous | Postgres container, re-seeded each deploy |
| **ElastiCache** | continuous | Redis container |
| **EKS** | ~$73/mo control plane | ECS Fargate tells the same story for free |
| **EFS / persistent volumes** | continuous | Data is ephemeral by design |
| **Idle ALB / compute** | ~$16/mo each | Nothing survives `destroy.sh` |

### Rules

- The environment is **ephemeral by design**: `setup -> test -> demo -> DESTROY ->
  setup again` must work repeatedly. `destroy.sh` is the normal end of a session,
  not an emergency measure.
- Data loss on destroy is **intended**. `seed.sh` rebuilds the demo dataset
  through the real ingestion pipeline on every deploy.
- An AWS Budget of $20 with alerts at 50/80/100% is part of the protected baseline.
- `deploy.sh` refuses to deploy once month-to-date spend crosses
  `MAX_MONTHLY_SPEND_USD`.
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

## 7. Observability requirements

- CloudWatch metrics and structured JSON logs for requests, latency, tokens,
  estimated cost, cache hits, retrieval performance, LLM performance, ingestion
  failures, and security events.
- Alarms on error rate, latency, and **estimated spend**.
- The correlation ID appears in every log line so a request can be traced from
  the browser through to LangSmith.

## 8. Escalation

Stop and ask the user before:

- Any action in an unverified account or region.
- Any change touching untagged or unknown resources.
- Any IAM change outside this project's roles.
- Any deletion of data (S3 objects, database, snapshots).
- Anything that would raise standing monthly cost.
