# Enterprise Knowledge-Based Automation

A multimodal enterprise RAG and agentic automation platform — a private company
knowledge assistant with tenant-aware retrieval, LangGraph workflows, and a
cost-conscious ephemeral AWS deployment.

> **Status:** all six phases complete. 164 backend tests passing, frontend
> building, Terraform validating. See [RUNBOOK.md](RUNBOOK.md) to run it.

## Documentation

| Document | Purpose |
|---|---|
| **[RUNBOOK.md](RUNBOOK.md)** | **How to run it — start here.** Local setup, AWS deploy, blue-green explained |
| [PROJECT.md](PROJECT.md) | Full requirements and architecture — the source of truth |
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
| `scripts/deploy.sh` | Create/update infrastructure, deploy, verify, seed, test |
| `scripts/verify.sh` | Verify infra, services, application, AI pipeline (read-only) |
| `scripts/test-e2e.sh` | Full end-to-end tests |
| `scripts/seed.sh` | Seed demo data so dashboards are not empty |
| `scripts/rollback.sh` | Roll back the application (never destroys) |
| `scripts/destroy.sh` | Destroy only this project's ephemeral infrastructure |

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
| AWS demo — on demand | ECS Fargate, ephemeral | **~$0.30 / session** |

The AWS environment is ephemeral by design. **No NAT Gateway, no RDS, no
ElastiCache, no EKS, no EFS** — Postgres, Qdrant, and Redis run as containers and
are re-seeded on every deploy. Nothing survives `destroy.sh`, so a destroyed
environment costs $0/hour.

```bash
./scripts/cost-check.sh   # spend so far + what is running billable now
./scripts/destroy.sh      # run this when the demo ends
```

`deploy.sh` refuses to run once month-to-date spend crosses `MAX_MONTHLY_SPEND_USD`.
An AWS Budget with 50/80/100% alerts is part of the protected baseline.

## AI models — Amazon Bedrock only

Both the LLM and the embeddings come from Bedrock. Note that **GPT-4 is not
available on Bedrock** — only OpenAI's open-weight `gpt-oss` models are, and they
are text-only.

| Role | Model |
|---|---|
| Chat | `openai.gpt-oss-20b-1:0` (fallback `amazon.nova-lite-v1:0`) |
| Vision | `amazon.nova-lite-v1:0` |
| Embeddings | `amazon.titan-embed-text-v2:0` (1024-dim) |
| Audio | Amazon Transcribe |

All model IDs are configuration in `.env` — swapping one requires no code change.
