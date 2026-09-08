---
name: verify
description: Verify deployed AWS infrastructure, services, application health, and the AI/RAG pipeline for this project. Use when asked to verify, health-check, validate a deployment, confirm everything is working, or run verify.sh. Read-only - never mutates infrastructure.
---

# Skill: Verification

Governed by [deployment.md](../../rules/deployment.md) and
[aws-infrastructure.md](../../rules/aws-infrastructure.md).

## Core constraint

**Verification is strictly read-only.** Use only `describe-*`, `list-*`, `get-*`,
and application read endpoints. If verification finds a problem, report it — do
not fix it inside this workflow, and never mutate infrastructure to make a check
pass.

`terraform destroy` and `terraform apply` are both forbidden here. `terraform plan`
is permitted only to detect drift.

## Step 1 — Identity and ownership

```bash
aws sts get-caller-identity
echo "$AWS_REGION"
terraform -chdir=infra/terraform/envs/$ENV workspace show
aws resourcegroupstaggingapi get-resources --tag-filters Key=ProjectCode,Values=ekba
```

Confirm every resource under review is owned by this project (state + `ekba-` name
prefix + `ProjectCode=ekba` tag). Report anything ambiguous; touch nothing.

## Step 2 — Infrastructure

- Terraform state is readable and matches expectations.
- `terraform plan` shows no unexpected drift (report drift; do not apply).
- Networking: private subnets, security groups, no unintended public exposure.
- S3: public access blocked, encryption on, versioning as configured.
- IAM roles exist and are scoped, with no wildcard grants beyond the documented
  exceptions.
- Secrets exist in Secrets Manager. **Verify presence and metadata only — never
  read or print a secret value.**

## Step 3 — Services

- PostgreSQL reachable from the application security group; migrations at head.
- Qdrant reachable; collections exist with the expected vector dimension.
- Redis reachable; used for cache and rate limiting.
- Containers running as non-root, healthy, and at the expected image SHA.

## Step 4 — Application

- `/health` liveness and readiness return healthy.
- Authentication rejects an unauthenticated request with 401.
- Security headers present: HSTS, `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, CSP.
- CORS reflects the allow-list only.
- Rate limiting returns 429 at the configured thresholds (20 / 10 / 5 per minute).

## Step 5 — AI pipeline

Run one real smoke query against seeded data and confirm the response envelope is
complete and correct:

```
answer, citations, retrieved_chunks, model_used, input_tokens, output_tokens,
estimated_cost, latency_ms, cache_hit, tenant_id, confidence
```

Check specifically:

- Citations resolve to documents that were actually retrieved, in the caller's tenant.
- `tenant_id` matches the caller.
- A second identical query reports `cache_hit: true`.
- A cross-tenant probe returns nothing from the other tenant.
- Bedrock and Transcribe are reachable; the embedding model matches the collection
  dimension.
- LangSmith traces carry the correlation ID.

## Step 6 — Observability and cost

- CloudWatch log groups receiving structured logs with correlation IDs.
- Metrics flowing for requests, latency, tokens, cost, cache hits, security events.
- Alarms configured, including estimated spend.
- Report currently running billable resources and their cost at rest.

## Step 7 — Report

Write a pass/fail verification report to `docs/reports/` with a timestamp, git
SHA, account, and region. State failures plainly with the evidence. Never include
secret values. If anything failed, recommend the next action — do not silently
report success.
