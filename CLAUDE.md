# CLAUDE.md — Enterprise Knowledge-Based Automation

## Read first

- **[PROJECT.md](PROJECT.md)** — requirements and architecture. The source of truth.
- **[.claude/rules/](.claude/rules/)** — binding constraints. Not suggestions.
- **[.claude/skills/](.claude/skills/)** — workflows for deploy, verify, E2E,
  rollback, Terraform, security testing, ingestion, and RAG testing.

## Current status (2026-09-11)

Local platform implemented; the last deploy gate passed 190 backend tests and
the frontend typecheck. **Phase 5 (AWS) is in progress** — status and open items
in PROJECT.md section 18, operating detail in [RUNBOOK.md](RUNBOOK.md) Part B:

- Account on the AWS **Paid** plan (the Free plan blocks CodeDeploy), `us-west-2`.
- Four Terraform states: `baseline` and `cost-guard` (protected), `dev` (ephemeral),
  `frontend` (Amplify + CloudFront, persistent — implemented, not applied yet).
- CodeDeploy blue-green deployments succeed; `verify.sh` passes.
- Cost guard applied with **`dry_run = true`**. Never set it to `false` without
  explicit user approval.
- Bedrock quotas are 0 in the account — no chat or demo data on AWS yet.
- `rollback.sh` does not shift traffic yet (placeholder).

## Non-negotiable safety rules

1. **Never** modify or delete infrastructure that does not belong to this project.
   Ownership requires all three: in this project's Terraform state, named
   `ekba-<env>-*`, and tagged `ProjectCode=ekba`. If ownership is unclear, **stop
   and ask**.
2. **Never** delete existing secrets, API keys, credentials, or unrelated resources.
3. **Never** expose or commit secrets.
4. **Never** run destructive AWS commands against resources outside this project.
5. **Never** run `terraform destroy` during development, testing, verification,
   deployment, or rollback. It is permitted **only** through `scripts/destroy.sh`,
   and only for resources in this project's Terraform state.
6. Protected secrets survive destruction of temporary infrastructure.
7. Always verify AWS account, region, Terraform state, and resource ownership
   before any infrastructure change.

## Invariants that define the product

- **Tenant isolation.** `tenant_id` comes only from the verified Cognito JWT and
  filters every query, retrieval, cache key, S3 prefix, and agent node. A
  cross-tenant leak is total product failure.
  See [tenant-isolation.md](.claude/rules/tenant-isolation.md).
- **Guardrails are pipeline stages**, not optional. No stage is skipped for speed,
  and a cache hit never bypasses auth, tenant filtering, or the output guardrail.
- **Bedrock only, for both chat and embeddings.** GPT-4 is *not* on Bedrock —
  only OpenAI's open-weight `gpt-oss` models are, and they are text-only. Vision
  uses `amazon.nova-lite-v1:0`; embeddings use `amazon.titan-embed-text-v2:0`.
  Nothing but the configured embedding model ever produces embeddings.
- **Cost: $20 hard ceiling**, measured gross of credits (credits pay first, but
  a ceiling measured after credits reads $0 until they run out). Local Docker
  Compose for all development at $0. AWS only for demos, at ~$0.09/hour. **No NAT Gateway, no RDS, no
  ElastiCache, no EKS, no EFS — those resources must not appear in the Terraform
  at all.** Postgres, Qdrant, and Redis run as containers and are re-seeded on
  every deploy. The environment is ephemeral:
  `setup -> test -> demo -> DESTROY -> setup again`.
- **`destroy.sh` is the normal end of a session**, not an emergency measure.
  Before any action that leaves something billable running, say so explicitly.

## Working rules

- Local first: Docker Compose (PostgreSQL, Qdrant, Redis) before any AWS deploy.
- Minimalistic but professional. Every component must be explainable in an
  interview. No technology outside PROJECT.md section 2 without approval.
- Never weaken or delete a test to make a build pass. Fix the code.
- Report outcomes honestly. A deploy that failed verification is a failed deploy.
- Every lifecycle run writes a timestamped report to `docs/reports/`, never
  containing secret values.

## Lifecycle

```bash
./scripts/cost-check.sh  # gross/credits/net spend, what is running, kill-switch state (read-only)
./scripts/deploy.sh      # refuses at $18 gross usage; ends with verify.sh
./scripts/verify.sh
./scripts/seed.sh        # local stack only; on AWS the task seeds itself
./scripts/test-e2e.sh    # local stack only
./scripts/rollback.sh    # application-level only, never destroys (traffic shift not built yet)
./scripts/destroy.sh     # the ONLY sanctioned terraform destroy path

./scripts/deploy-frontend.sh [--plan-only]   # Amplify frontend + CloudFront API + backend wiring + verify
./scripts/rollback-frontend.sh [--to <sha>]  # rebuild an earlier frontend commit (app-level only)
```

The frontend reaches the API through CloudFront (HTTPS) because the ALB is
HTTP-only. Never add the Amplify origin or CloudFront ingress to
`envs/dev/terraform.tfvars` by hand — `deploy-frontend.sh` owns them in
`envs/dev/frontend.auto.tfvars`.

Settings come from `.env` (see `.env.example`). `EXPECTED_AWS_ACCOUNT_ID`,
`EXPECTED_AWS_REGION`, and `AWS_REGION` are mandatory — scripts refuse to touch
AWS without them; if unset, they default from the git-ignored
`infra/terraform/envs/baseline/terraform.tfvars`.

**Before any AWS deploy or verify, check the operator IP.** The ALB admits only
`allowed_cidrs` in `infra/terraform/envs/dev/terraform.tfvars` (one `/32`). A
changed home IP makes `verify.sh` fail with a timeout while the deployment itself
is healthy — confirm with ALB target health and the app logs before touching
application code.

## Phases

Six phases, 0–5. Phases 0–4 are **local Docker Compose at $0**; only Phase 5
touches AWS. Full deliverables and exit criteria in
[PROJECT.md section 18](PROJECT.md). Do not start a phase before the previous
one's exit criteria pass.
