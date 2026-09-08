# CLAUDE.md — Enterprise Knowledge-Based Automation

## Read first

- **[PROJECT.md](PROJECT.md)** — requirements and architecture. The source of truth.
- **[.claude/rules/](.claude/rules/)** — binding constraints. Not suggestions.
- **[.claude/skills/](.claude/skills/)** — workflows for deploy, verify, E2E,
  rollback, Terraform, security testing, ingestion, and RAG testing.

## Current status

Foundation complete: structure, PROJECT.md, rules, skills, and lifecycle scripts.
**Phase 0 implementation has not started.** Build phases in order, 0 through 5
(PROJECT.md section 18).

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
- **Cost: $20 hard ceiling** (not $140). Local Docker Compose for all development
  at $0. AWS only for demos, at ~$0.30/session. **No NAT Gateway, no RDS, no
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
./scripts/cost-check.sh  # spend + what is running billable (read-only)
./scripts/deploy.sh      # refuses past the $20 ceiling
./scripts/verify.sh
./scripts/seed.sh
./scripts/test-e2e.sh
./scripts/rollback.sh    # application-level only, never destroys
./scripts/destroy.sh     # the ONLY sanctioned terraform destroy path
```

Settings come from `.env` (see `.env.example`). `EXPECTED_AWS_ACCOUNT_ID`,
`EXPECTED_AWS_REGION`, and `AWS_REGION` are mandatory — scripts refuse to touch
AWS without them.

## Phases

Six phases, 0–5. Phases 0–4 are **local Docker Compose at $0**; only Phase 5
touches AWS. Full deliverables and exit criteria in
[PROJECT.md section 18](PROJECT.md). Do not start a phase before the previous
one's exit criteria pass.
