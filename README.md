# Enterprise Knowledge-Based Automation

A multimodal enterprise RAG and agentic automation platform — a private company
knowledge assistant with tenant-aware retrieval, LangGraph workflows, and a
cost-conscious ephemeral AWS deployment.

> **Status (2026-09-11):** the local platform runs end to end; the last deploy
> gate passed 190 backend tests and the frontend typecheck. The AWS demo stack
> deploys through CodeDeploy blue-green and passes verification. Open items:
> Bedrock quotas are 0 in the AWS account (no chat or demo data on AWS yet),
> `rollback.sh` does not shift traffic yet, and the cost guard is still in
> dry-run. See [RUNBOOK.md](RUNBOOK.md) to run it.

## Documentation

| Document | Purpose |
|---|---|
| **[RUNBOOK.md](RUNBOOK.md)** | **How to run it — start here.** Local setup, AWS deploy, blue-green explained, troubleshooting |
| [infra/terraform/README.md](infra/terraform/README.md) | The four Terraform states, the cost guard and the frontend hosting |
| [PROJECT.md](PROJECT.md) | Full requirements and architecture — the source of truth |
| [docs/reports/](docs/reports/) | Timestamped reports from every lifecycle run |
| [CLAUDE.md](CLAUDE.md) | Working instructions and safety rules |
| [.claude/rules/](.claude/rules/) | Binding constraints (security, terraform, tenancy, …) |
| [.claude/skills/](.claude/skills/) | Operational workflows (deploy, verify, E2E, …) |

## Stack

**Frontend** Next.js · TypeScript · Tailwind · shadcn/ui
**Backend** FastAPI · Pydantic · SQLAlchemy · Alembic
**AI** Amazon Bedrock · LangGraph · Amazon Transcribe
**Data** Qdrant · PostgreSQL · Redis · Amazon S3
**Auth** Amazon Cognito
**Infra** Terraform · Docker · ECR · CodeDeploy · GitHub Actions
**Observability** CloudWatch · LangSmith

## Getting started

```bash
cp .env.example .env    # set POSTGRES_PASSWORD and DEV_AUTH_SECRET
docker compose -f infra/docker/docker-compose.yml up -d postgres qdrant redis minio minio-init

cd backend && python -m venv .venv && source .venv/Scripts/activate
pip install -e ".[dev]"
alembic upgrade head && python -m seeds.seed
uvicorn app.main:app --reload          # http://localhost:8000/docs

cd ../frontend && npm install && npm run dev    # http://localhost:3000
```

Get a login token with `cd backend && python -m seeds.dev_token`.

With `AI_PROVIDER=local` the entire stack runs offline with **no AWS account and
no cost**. Full walkthrough in **[RUNBOOK.md](RUNBOOK.md)**.

## Lifecycle

```
deploy -> test -> verify -> live demo -> destroy -> audit -> deploy again
```

| Script | Purpose |
|---|---|
| `scripts/deploy.sh` | Cost guard, local gate, image, Terraform, CodeDeploy blue-green, `verify.sh` |
| `scripts/verify.sh` | Health of the deployed API (read-only; most other checks are still placeholders) |
| `scripts/cost-check.sh` | Gross / credits / net spend, what is running, whether the kill switch is armed |
| `scripts/test-e2e.sh` | Full end-to-end tests — local stack |
| `scripts/seed.sh` | Seed demo data — local stack (on AWS the task seeds itself at startup) |
| `scripts/rollback.sh` | Placeholder — does not shift traffic yet; re-runs `verify.sh` (never destroys) |
| `scripts/destroy.sh` | Destroy only this project's ephemeral infrastructure |
| `scripts/deploy-frontend.sh` | Frontend on AWS Amplify: CloudFront API front door, backend wiring, build, verification |
| `scripts/rollback-frontend.sh` | Rebuild an earlier frontend commit on Amplify (app-level only) |

## AWS deployment

One region (`us-west-2`), four Terraform states — protected baseline, protected
cost guard, ephemeral `dev` stack, persistent frontend. The `dev` stack is an ALB in front of one ECS
Fargate task (API + Postgres + Qdrant + Redis containers), released by **AWS
CodeDeploy blue-green** through two target groups and a test listener. The
account must be on the AWS **Paid** plan — the Free plan rejects every CodeDeploy
call with `SubscriptionRequiredException`.

The ALB accepts traffic only from `allowed_cidrs`, a single `/32`. When your
public IP changes, update it and redeploy, or `verify.sh` fails even though the
deployment is healthy. Details and troubleshooting in
**[RUNBOOK.md](RUNBOOK.md#part-b--deploying-to-aws-009hour-while-it-exists)**.

### Frontend on AWS Amplify

The Next.js frontend deploys as a static export on **AWS Amplify Hosting**:
every push to `aws-deployment` builds and deploys it. Because the ALB is
HTTP-only, the browser reaches the API through a **CloudFront** distribution that
adds HTTPS (`Amplify → HTTPS CloudFront → HTTP ALB → ECS`). After a one-time
repository connection in the Amplify console, everything else is one command:

```bash
./scripts/deploy-frontend.sh --plan-only   # review, changes nothing
./scripts/deploy-frontend.sh               # deploy / update / verify
```

**Status (2026-09-11):** implemented and dry-run tested; not applied yet. See
**[RUNBOOK.md Part C](RUNBOOK.md#part-c--frontend-on-aws-amplify)**.

## Safety

- Only resources in this project's Terraform state, named `ekba-<env>-*`, and
  tagged `ProjectCode=ekba` are ever touched.
- `terraform destroy` runs **only** through `scripts/destroy.sh`.
- Secrets, credentials, the Terraform state backend, and ECR images are never
  deleted — they survive every destroy.
- Every retrieval enforces `tenant_id` filtering taken from the verified JWT.

## Cost — $20 ceiling

| Environment | Where | Cost |
|---|---|---|
| Local — all development | Docker Compose | **$0** |
| AWS demo — on demand | ECS Fargate, ephemeral | **~$0.09 / hour** (~$0.37 per 4-hour session) |

The AWS environment is ephemeral by design. **No NAT Gateway, no RDS, no
ElastiCache, no EKS, no EFS** — Postgres, Qdrant, and Redis run as containers and
are re-seeded on every deploy. Nothing in the `dev` stack survives `destroy.sh`,
so a destroyed environment costs $0/hour; the protected baseline costs about
$1.70/month at rest, mostly Secrets Manager.

```bash
./scripts/cost-check.sh   # gross / credits / net spend + what is running billable now
./scripts/destroy.sh      # run this when the demo ends
```

Spend is measured **gross of credits** — with credits netted in, AWS reports $0
until they run out. The **cost guard** (`infra/terraform/envs/cost-guard`) emails
at $10 and $15 of gross usage, and stops the stack at $18, on any charge credits
did not cover, or after 8 hours. **It is in dry-run** until explicitly armed.
`deploy.sh` refuses to deploy at the same $18. See
[infra/terraform/README.md](infra/terraform/README.md#envscost-guard--protected-always-on).

## AI models — Amazon Bedrock only

Both the LLM and the embeddings come from Bedrock. Note that **GPT-4 is not
available on Bedrock** — only OpenAI's open-weight `gpt-oss` models are, and they
are text-only.

| Role | Model |
|---|---|
| Chat | `us.amazon.nova-lite-v1:0` (fallback `us.amazon.nova-micro-v1:0`) |
| Vision | `us.amazon.nova-lite-v1:0` |
| Embeddings | `amazon.titan-embed-text-v2:0` (1024-dim) |
| Audio | Amazon Transcribe |

Nova must be called through an inference profile (the `us.` prefix); Titan
embeddings are invoked directly. All model IDs are configuration (`.env` locally,
Terraform variables on AWS) — swapping one requires no code change.

> **AWS status:** this account's Bedrock inference quotas are currently 0 in
> `us-west-2`, so on AWS the in-task seed fails with `ThrottlingException` and
> chat cannot answer. The API itself is healthy. Raising the quotas needs an AWS
> Support case — see RUNBOOK.md, Part B, Step 2.
