# Enterprise Knowledge-Based Automation

A multimodal enterprise RAG and agentic automation platform — a private company
knowledge assistant with tenant-aware retrieval, LangGraph workflows, and a
cost-conscious ephemeral AWS deployment.

> **Status (2026-09-12):** the local platform runs end to end; the last deploy
> gate passed 279 backend tests and the frontend typecheck. Tenant onboarding is
> now a product feature: a platform operator creates a company and invites its
> first administrator, that administrator manages its own users, and users sign
> in with an email and a password instead of pasting a token. The AWS demo stack
> deploys through CodeDeploy blue-green and passes verification; PostgreSQL on
> AWS is a private **Amazon RDS** instance (the Postgres sidecar is gone), and
> the frontend is live on Amplify behind a CloudFront API front door. Open items:
> Bedrock quotas are 0 in the AWS account (no chat or demo data on AWS yet),
> `rollback.sh` does not shift traffic yet, and the cost guard is still in
> dry-run. See [RUNBOOK.md](RUNBOOK.md) to run it, or the Hinglish
> [handbook](docs/handbook/ENTERPRISE_KNOWLEDGE_AUTOMATION_COMPLETE_HANDBOOK.md)
> ([PDF](docs/handbook/ENTERPRISE_KNOWLEDGE_AUTOMATION_COMPLETE_HANDBOOK.pdf)).

## Documentation

| Document | Purpose |
|---|---|
| **[RUNBOOK.md](RUNBOOK.md)** | **How to run it — start here.** Local setup, AWS deploy, blue-green explained, troubleshooting |
| [docs/handbook/](docs/handbook/) | Complete hands-on training manual in simple Hinglish (Markdown + PDF — rebuild with `node scripts/build-handbook-pdf.mjs`) |
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
**Data** Qdrant · PostgreSQL (Amazon RDS on AWS) · Redis · Amazon S3
**Auth** Amazon Cognito
**Infra** Terraform · Docker · ECR · CodeDeploy · GitHub Actions
**Observability** CloudWatch · LangSmith

## Getting started

```bash
cp .env.example .env    # set POSTGRES_PASSWORD, DEV_AUTH_SECRET, DEV_AUTH_PASSWORD
docker compose -f infra/docker/docker-compose.yml up -d postgres qdrant redis minio minio-init

cd backend && python -m venv .venv && source .venv/Scripts/activate
pip install -e ".[dev]"
alembic upgrade head && python -m seeds.seed
uvicorn app.main:app --reload          # http://localhost:8000/docs

cd ../frontend && npm install && npm run dev    # http://localhost:3000
```

Sign in at <http://localhost:3000> with any seeded account's email and the
`DEV_AUTH_PASSWORD` from your `.env` — the same email/password screen the AWS
deployment uses, so the sign-in path is never "works on my machine". Seeded
accounts include a platform operator, an administrator per company, and ordinary
users; `python -m seeds.seed` prints them. For a quick token instead, use
`cd backend && python -m seeds.dev_token [--role admin|platform_admin]` and paste
it under "Developer sign-in", which only appears against a localhost API.

With `AI_PROVIDER=local` the entire stack runs offline with **no AWS account and
no cost**. Full walkthrough in **[RUNBOOK.md](RUNBOOK.md)**.

## Roles

| Role | Can | Cannot |
|---|---|---|
| `platform_admin` | create companies, invite each company's first admin, read the onboarding trail | read any company's documents, chat or metrics |
| `admin` | manage users in **its own** company, assign `user`/`admin`, see its own metrics and audit log | create a company, create a platform operator, touch another company |
| `user` | use chat, documents, workflows | manage users or companies |

The hierarchy is enforced in the API, not by hiding buttons: `tenant_id` and
`role` are read only from the verified Cognito ID token, the platform role and
the reserved `platform` tenant imply each other, and no API can grant
`platform_admin` — the first operator is created out of band by
`scripts/bootstrap-platform-admin.sh`. Accounts are invitation-only: Cognito
emails a one-time password and the invitee replaces it on first sign-in, so no
password is ever shared, logged or returned.

## Lifecycle

```
deploy -> test -> verify -> live demo -> destroy -> audit -> deploy again
```

| Script | Purpose |
|---|---|
| `scripts/deploy.sh` | Cost guard, local gate, image, Terraform, CodeDeploy blue-green, `verify.sh` |
| `scripts/verify.sh` | Health + readiness (PostgreSQL/RDS, Redis, Qdrant) of the deployed API, RDS posture (read-only; other checks are placeholders) |
| `scripts/cost-check.sh` | Gross / credits / net spend, what is running, whether the kill switch is armed |
| `scripts/test-e2e.sh` | Full end-to-end tests — local stack |
| `scripts/seed.sh` | Seed demo data — local stack (on AWS the task seeds itself at startup) |
| `scripts/rollback.sh` | Placeholder — does not shift traffic yet; re-runs `verify.sh` (never destroys) |
| `scripts/destroy.sh` | Snapshot the RDS database, then destroy only this project's ephemeral infrastructure |
| `scripts/deploy-frontend.sh` | Frontend on AWS Amplify: CloudFront API front door, backend wiring, build, verification |
| `scripts/rollback-frontend.sh` | Rebuild an earlier frontend commit on Amplify (app-level only) |
| `scripts/bootstrap-platform-admin.sh` | Create the first platform operator in Cognito — the one grant with no API (idempotent, never deletes) |

## AWS deployment

One region (`us-west-2`), four Terraform states — protected baseline, protected
cost guard, ephemeral `dev` stack, persistent frontend. The `dev` stack is an ALB in front of one ECS
Fargate task (API + Qdrant + Redis containers) and a private **RDS PostgreSQL**
(`db.t4g.micro`), released by **AWS CodeDeploy blue-green** through two target
groups and a test listener. PostgreSQL data survives task replacement and every
deploy; `destroy.sh` snapshots it and `deploy.sh` restores it. The
account must be on the AWS **Paid** plan — the Free plan rejects every CodeDeploy
call with `SubscriptionRequiredException`.

The ALB accepts traffic only from `allowed_cidrs`, a single `/32`. When your
public IP changes, update it and redeploy, or `verify.sh` fails even though the
deployment is healthy. Details and troubleshooting in
**[RUNBOOK.md](RUNBOOK.md#part-b--deploying-to-aws-011hour-while-it-exists)**.

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

**Status (2026-09-11):** live — `https://aws-deployment.<app-id>.amplifyapp.com`, API through CloudFront. See
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
| AWS demo — on demand | ECS Fargate + RDS, ephemeral | **~$0.11 / hour** (~$0.44 per 4-hour session) |

The AWS environment is ephemeral by design. **No NAT Gateway, no ElastiCache, no
EKS, no EFS**, and exactly one small RDS instance (`db.t4g.micro`, single-AZ, 20 GB,
private) that exists only while the `dev` stack does — ~$0.019/hour, or ~$14/month
if it were left running 24/7. Qdrant and Redis run as containers. Nothing billable
in the `dev` stack survives `destroy.sh`, so a destroyed environment costs $0/hour;
the database's data is kept as a manual snapshot (a few cents a month), and the
protected baseline costs about $1.70/month at rest, mostly Secrets Manager.

```bash
./scripts/cost-check.sh   # gross / credits / net spend + what is running billable now
./scripts/destroy.sh      # run this when the demo ends
```

Spend is measured **gross of credits** — with credits netted in, AWS reports $0
until they run out. The **cost guard** (`infra/terraform/envs/cost-guard`) emails
at $10 and $15 of gross usage, and stops the stack (ECS to zero, ALB deleted, RDS
stopped) at $18, on any charge credits
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
