# Enterprise Knowledge-Based Automation

**Project type:** Multimodal Enterprise RAG + Agentic Automation Platform
**Status:** Foundation established. Phase 0 not yet started.

This document is the single source of truth for requirements and architecture.
Binding constraints on how code and infrastructure are produced live in
[.claude/rules/](.claude/rules/). Operational workflows live in
[.claude/skills/](.claude/skills/).

---

## 1. Goal

A production-style enterprise AI platform acting as a private company knowledge
assistant. Multiple departments per company, each with its own knowledge base,
served through secure, tenant-aware RAG and agentic automation.

**Departments:** HR, Finance, Legal, Sales, Marketing, Operations, Technical.

**Supported knowledge formats:**

| Class | Formats |
|---|---|
| Text | PDF, Markdown, TXT, DOC/DOCX |
| Tabular | CSV, Excel, embedded tables |
| Visual | Images, diagrams |
| Media | Audio, video |
| Content types | SOPs, policies, training material |

---

## 2. Core architecture

### Frontend
- Next.js / React
- TypeScript
- Tailwind CSS
- shadcn/ui

### Backend
- Python
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic

### AI / Agents
- LangGraph
- RAG
- Agentic workflows
- Model routing

### Data
- **Vector DB:** Qdrant — runs as a container, never a managed service
- **Database:** PostgreSQL 16 — a container locally, **Amazon RDS**
  (`db.t4g.micro`, private subnets) in the AWS demo, so data survives task
  replacement. Approved 2026-09-11; replaced the Postgres sidecar.
- **Cache:** Redis — container. **No ElastiCache.**
- **Storage:** Amazon S3 (the only persistent data store in the demo)

> Qdrant and Redis run as containers alongside the API and are **re-seeded on
> every deploy**, which is what makes the $20 budget achievable. PostgreSQL is
> the one managed service: a single small RDS instance that lives and dies with
> the ephemeral stack, with its data carried between sessions as a snapshot.
> See section 16.

### Identity
- Amazon Cognito

### AI platform — Amazon Bedrock only

**Both the LLM and the embedding model come from Amazon Bedrock.** No external AI
provider is required for the system to work.

| Role | Model ID | Notes |
|---|---|---|
| Chat (primary) | `us.amazon.nova-lite-v1:0` | Inference-profile ID — Nova cannot be invoked by bare ID |
| Chat (fallback) | `us.amazon.nova-micro-v1:0` | Cheaper, **text-only** |
| Vision / multimodal | `us.amazon.nova-lite-v1:0` | Native image **and** video understanding |
| Embeddings | `amazon.titan-embed-text-v2:0` | 1024 dimensions, invoked directly (no prefix) |
| Audio | Amazon Transcribe | Speech-to-text for audio, video, and voice input |

The original plan put `openai.gpt-oss-20b-1:0` in the primary chat slot. It was
replaced because `us-west-2` offered no `gpt-oss` models at the last check. See
[ai-model-usage.md](.claude/rules/ai-model-usage.md) for the registry and the
inference-profile rule.

**Factual constraints that shaped this table:**

- **GPT-4 is not available on Amazon Bedrock**, and never has been. Proprietary
  OpenAI models are only on the OpenAI API and Azure OpenAI. Bedrock hosts
  OpenAI's *open-weight* `gpt-oss` family only.
- `openai.gpt-oss-*` models are **text-only**. All vision work routes to Nova Lite.
- **OpenAI embedding models are not on Bedrock.** Embeddings use Titan v2.
- **Gemini 2.5 Flash is not available through Bedrock** either — it is a Google
  Vertex AI model. It is not used in this project.

**Hard rules:**

1. No model is ever used for embeddings except the configured Bedrock embedding
   model. A chat or vision model is never an embedding model.
2. All model IDs are configuration (`.env` / Secrets Manager), never hardcoded.
   Swapping a model must not require a code change.
3. Verify regional availability and current pricing in the target account before
   depending on a model — and its **quota**: a new account can show every Bedrock
   inference quota as `0`, which is the case in this project's AWS account as of
   2026-09-11. If the primary chat model is unavailable, fall back to the
   configured fallback model and record the fallback in `model_used`.

### Infrastructure and delivery
- AWS + Terraform
- Docker + Amazon ECR
- GitHub Actions
- AWS CodeDeploy
- EKS only where genuinely required (see cost strategy)

### Observability
- CloudWatch
- LangSmith

### Testing
- Pytest (backend, security, evaluation)
- Playwright (frontend E2E)

---

## 3. Multimodal ingestion flows

**PDF / Text**

```
Extract -> Chunk -> Embed -> Qdrant
```

**Image / Table / Diagram**

```
Multimodal model -> Extract and understand content -> Chunk -> Embed -> Qdrant
```

**Audio**

```
S3 -> Amazon Transcribe -> Transcript -> Chunk -> Embed -> Qdrant
```

**Video**

```
S3 -> Audio track -> Amazon Transcribe
   -> Optional key frames -> Multimodal model
   -> Combine knowledge -> Chunk -> Embed -> Qdrant
```

---

## 4. RAG pipeline

Every question passes through this ordered pipeline. No stage may be skipped.

```
Question
  1.  Authentication
  2.  Input validation
  3.  Prompt injection scan
  4.  Semantic cache
  5.  Tenant / permission filtering
  6.  Retrieval
  7.  Relevance threshold
  8.  Context construction
  9.  Model routing
  10. Reranking
  11. Citation validation
  12. Output guardrail
  13. Token / cost tracking
  14. Language handling
  -> Response
```

---

## 5. Agentic automation

Implemented with LangGraph. Supported workflow families:

- Policy comparison
- Document summarization
- Cross-document analysis
- Knowledge extraction
- Report generation
- Department-specific automation

**Reference workflow — compare old and new travel policies:**

```
Find documents -> Analyze -> Compare -> Generate summary -> Add citations
```

---

## 6. Vector metadata

Every chunk stored in Qdrant carries:

| Field | Purpose |
|---|---|
| `document_id` | Parent document |
| `chunk_id` | Chunk identity |
| `document_name` | Human-readable citation label |
| `page_number` | Citation precision |
| `source_uri` | S3 / origin pointer |
| `owner_id` | Ownership for deletion authorization |
| `tenant_id` | **Mandatory isolation key** |
| `document_version` | Version pinning |
| `created_by` | Audit |
| `created_at` | Audit / recency |

**Every retrieval must enforce tenant and permission filters.** See
[tenant-isolation.md](.claude/rules/tenant-isolation.md).

---

## 7. Chat response contract

```jsonc
{
  "answer":            "string",
  "citations":         [],
  "retrieved_chunks":  [],
  "model_used":        "string",
  "input_tokens":      0,
  "output_tokens":     0,
  "estimated_cost":    0.0,
  "latency_ms":        0,
  "cache_hit":         false,
  "tenant_id":         "string",
  "confidence":        0.0
}
```

---

## 8. Security

### Threats to defend against
- Direct prompt injection
- Indirect prompt injection
- System prompt extraction
- Instruction override
- Cross-tenant access
- Malicious files
- Unsafe HTML / JS
- Path traversal
- Retrieval poisoning
- Token abuse
- Sensitive-data leakage
- Unauthorized deletion

### Controls
Cognito JWT, RBAC, tenant authorization, least-privilege IAM, secure headers,
CORS allow-list, rate limiting, request limits, upload limits, trusted-host
validation, HTTPS, encrypted S3, private database and networking, non-root
containers, ECR scanning, AWS Secrets Manager.

### Roles
- `user` — an employee of a customer company.
- `admin` — that company's own administrator.
- `platform_admin` — the service provider. Lives in the reserved `platform`
  tenant, which holds operators and no content.

### Onboarding hierarchy

```
Platform operator (platform_admin, tenant "platform")
  └── creates a company          ──> tenant row + audited event
        └── invites its first admin (role=admin, that tenant)
              └── creates its own users (role=user or admin, same tenant)
```

Each step is only available one level up, and never sideways or downward into
another company. The rules that make it hold:

1. Only `platform_admin` may create a company.
2. `admin` manages users **only inside its own company**, and may assign only
   `user` or `admin`.
3. `admin` can never create a company, and can never create or become a
   `platform_admin`.
4. `user` manages nothing.
5. The platform role and the `platform` tenant imply each other at token
   verification, so neither half can be forged alone.
6. No API grants `platform_admin`. The first operator is created out of band by
   `scripts/bootstrap-platform-admin.sh`.
7. A company can never be left with no active administrator.

### Access rules
1. Users only access documents in tenants they are authorized for.
2. Admins can access operational metrics for their own company.
3. Document deletion requires ownership or explicit authorization.
4. Every RAG query applies `tenant_id` filtering.
5. `platform_admin` sees the company **registry** — names, ids, seat counts and
   the onboarding trail — and never a company's documents, conversations,
   metrics or chat. The registry lives in one module (`app/db/control_plane.py`)
   behind one dependency, and every mutation is audited under the target tenant.

### Authentication flow
Sign-in is email and password against the Cognito user pool, proxied through the
API so local development exercises the same path. Accounts are created by
invitation only: Cognito emails a one-time password and the invitee replaces it
on first sign-in. The application never sets, stores, logs or returns a password.

---

## 9. Rate limiting

| Limit | Value |
|---|---|
| API requests | 20 / minute / user |
| Server-side requests | 10 / minute / user |
| Document uploads | 5 / minute / user |
| Sign-in / password reset | 10 / minute / account |

The auth bucket is keyed by a hash of the account identifier rather than the IP:
behind CloudFront and an ALB the client IP is shared or client-supplied.

---

## 10. Observability

**Tracked:** requests, latency, tokens, estimated cost, cache hits, retrieval
performance, LLM performance, ingestion failures, security events.

**One correlation ID** propagates across frontend, API, backend, logs, AI calls,
LangSmith, and errors.

---

## 11. Evaluation

Retrieval precision, retrieval recall / hit rate, answer relevance, faithfulness,
citation correctness, factual correctness, latency, token usage, estimated cost.

Datasets live in [evaluation/datasets/](evaluation/datasets/).

---

## 12. Frontend surface

**Landing / first page — "Enterprise Knowledge AI"** shows: search/chat,
documents, departments, recent queries, usage, confidence, citations.

| User pages | Company admin pages | Platform pages |
|---|---|---|
| Dashboard | Users | Companies |
| Knowledge Chat | My Company | Onboarding Trail |
| Documents | Documents | Security |
| Departments | AI Metrics | Deployments |
| Usage | Security | |
| Feedback | Audit Logs | |
| | Deployments | |

Navigation is role-aware: a platform operator sees the control plane and no chat
or documents, because the platform tenant holds none. Hiding a page is a
convenience, never the boundary — every route is authorized again server-side,
and each frontend gate mirrors exactly one backend dependency
(`AdminOnly`/`AdminUser`, `TenantAdminOnly`/`TenantAdminUser`,
`PlatformAdminOnly`/`PlatformAdminUser`).

The sign-in page asks for an email and a password, handles the first-sign-in
password challenge and password recovery, renews the session silently, and
revokes it server-side on sign-out. A paste-a-token box exists for local
debugging only and renders solely when the API is localhost.

Development data is seeded so no dashboard renders empty.

---

## 13. Voice (optional enhancement)

```
Voice input -> Amazon Transcribe -> existing RAG/Agent pipeline -> Answer
```

No separate AI backend for voice. It reuses the same pipeline.

---

## 14. Database entities

`users`, `tenants`, `documents`, `ingestion_jobs`, `conversations`, `messages`,
`request_usage`, `user_feedback`, `prompt_releases`, `audit_events`

---

## 15. DevOps

Docker, GitHub, GitHub Actions, Terraform, ECR, AWS Secrets Manager, CloudWatch,
AWS CodeDeploy, EKS where required.

**Pipeline:**

```
Push -> Test -> Security checks -> Build -> Docker -> Scan -> ECR
     -> Terraform validation -> Deploy -> Health checks
     -> E2E verification -> Rollback if needed
```

---

## 16. AWS cost strategy — $20 hard cap

The account holds promotional credits ($158.99 remaining at the last check,
2026-09-11) and is on the AWS **Paid** plan — the Free plan does not allow
CodeDeploy. **The spend target is $20 total, and $20 is treated as a hard
ceiling, not a guideline.** Spend is measured **gross of credits**: credits pay
first, but a ceiling measured after credits would read $0 until they ran out.
Everything below follows from that.

### The operating model

```
setup -> test -> demo -> DESTROY -> (later) setup again
```

Nothing runs between demos. There is no "staging environment sitting there".
A destroyed environment costs **$0/hour**, and `setup` rebuilds it from scratch
in minutes with freshly seeded data.

### Two environments

| Environment | Where | Cost |
|---|---|---|
| **Local** — all day-to-day development | Docker Compose | **$0** |
| **AWS demo** — deployed only when needed | ECS Fargate + RDS, ephemeral | ~$0.11 / hour (~$0.44 per 4-hour session) |

All development, testing, and iteration happens locally at zero cost. AWS is used
only to prove the deployment story and run a live demo.

### The ephemeral AWS stack

**Deployed:**

| Resource | Sizing | Approx. cost |
|---|---|---|
| Application Load Balancer | 1, across two AZs | ~$0.023 / hr + LCUs |
| ECS Fargate task | 1 vCPU / 3 GB, three containers in one task | ~$0.054 / hr |
| RDS PostgreSQL | `db.t4g.micro`, single-AZ, 20 GB gp3, private | ~$0.016 / hr + ~$0.003 / hr storage |
| Public IPv4 addresses | 2 on the ALB, 1 on the task | ~$0.015 / hr |
| S3 | documents | pennies |
| Cognito | user pool | free tier |
| CloudWatch Logs | 1-day retention | pennies |
| Bedrock | per token | ~$0.01–0.05 / session |
| Amazon Transcribe | per minute of audio | keep demo clips short |

**A 4-hour demo session costs roughly $0.44** (list-price estimate). That is
about 40 sessions before the $18 automatic shutdown. The protected baseline adds
about $1.70/month at rest, mostly four Secrets Manager secrets, plus a few cents
for the database snapshots kept between sessions.

The database is the one resource that would break the budget if forgotten:
**~$14/month running 24/7**. It therefore lives in the ephemeral stack, and the
cost guard stops it (never deletes it) on a budget or session-limit breach.

**Never provisioned, at all:**

| Excluded | Why |
|---|---|
| NAT Gateway | ~$32/mo + data. Fargate runs in a public subnet with a public IP to reach ECR. |
| RDS running between sessions, Multi-AZ, or anything above `db.t4g.small` | One single-AZ `db.t4g.micro` exists only while the stack does; its data is kept as a snapshot |
| ElastiCache | Redis runs as a container |
| EKS | ~$73/mo control plane alone. ECS Fargate tells the same story for free. |
| EFS / persistent volumes | Data is ephemeral by design |
| Idle compute or load balancers | Nothing survives `destroy.sh` |

### What persists, and what is deliberately ephemeral

| Data | On AWS | Survives task replacement? | Survives `destroy.sh`? |
|---|---|---|---|
| Relational data (tenants, users, documents, jobs, conversations, usage, audit) | **RDS PostgreSQL** | **Yes** | **Yes, through the snapshot `destroy.sh` takes and `deploy.sh` restores** |
| Vectors (chunks + embeddings) | Qdrant container | No | No |
| Cache, semantic cache, rate limits | Redis container | No | No |
| Uploaded files | S3 (protected baseline) | Yes | Yes |

Qdrant and Redis run as containers with no persistent volume — **this is
intended.** On AWS the API container migrates and re-seeds at every task start
(`alembic upgrade head`, then `python -m seeds.seed`, through the real ingestion
pipeline), so dashboards and retrieval are populated with genuine chunks,
embeddings, and citations. `alembic upgrade head` is a no-op once the schema is
current, and the seed upserts by stable IDs, so a persistent database is never
duplicated. A failed seed does not stop the API. `seed.sh` does the same for the
local stack.

> **Known gap:** because Qdrant is still ephemeral while PostgreSQL is not, a
> document uploaded on AWS keeps its row and its S3 file but loses its vectors at
> the next task replacement, so it stops being retrievable until it is ingested
> again. Only the seed documents are re-indexed automatically. A re-index job for
> user documents is **not implemented**.

Beyond the database, only the protected baseline persists: S3 documents, Secrets
Manager, ECR images, Cognito, budgets and the audit log group.

### Enforcement

1. An AWS Budget of **$20** is created as part of the protected baseline, with
   alerts at 50% / 80% / 100%. It uses the AWS default of including credits, so
   it only alerts once spend reaches the card.
2. The **cost guard** (`infra/terraform/envs/cost-guard`) measures gross usage.
   It emails at $10 and $15, and stops the ephemeral stack — ECS scaled to zero,
   ALB deleted — at $18 of gross usage, on any charge credits did not cover, or
   when the stack is older than 8 hours. Applied 2026-09-11, **in dry-run** until
   explicitly armed.
3. `cost-check.sh` reports gross usage, credits applied, net spend, what is
   running billable right now, and whether the kill switch is armed.
4. `deploy.sh` refuses to deploy once gross usage since `COST_GUARD_START`
   reaches `COST_GUARD_SHUTDOWN_USD` ($18).
5. Every deploy prints an estimated hourly burn and a reminder to run
   `destroy.sh`.
6. Adding any new AWS resource requires stating its cost at rest. If it cannot be
   justified inside $20, it does not go in.

---

## 17. Project lifecycle

Reproducibility is mandatory. Lifecycle entrypoints in [scripts/](scripts/):

| Script | Responsibility |
|---|---|
| `deploy.sh` | Create/update infrastructure, deploy application, verify |
| `verify.sh` | Verify AWS infrastructure, services, application, AI pipeline |
| `cost-check.sh` | Report gross/net spend, billable resources, and cost-guard state |
| `test-e2e.sh` | Run complete end-to-end tests |
| `seed.sh` | Seed development/demo data |
| `rollback.sh` | Safely roll back the application |
| `destroy.sh` | Destroy **only** temporary infrastructure owned by this project |

### Non-negotiable lifecycle safety rules

1. Never destroy unrelated infrastructure.
2. Never delete existing secrets, API keys, credentials or unrelated resources.
3. Identify project resources by Terraform state, project-specific naming, and tags.
4. **Never** run `terraform destroy` during normal development, testing,
   verification, deployment or rollback. It is permitted only through
   `scripts/destroy.sh`.
5. Protected secrets survive destruction of temporary infrastructure.
6. Always verify AWS account, region, Terraform state and resource ownership
   before any infrastructure change.

Full detail: [terraform.md](.claude/rules/terraform.md),
[aws-infrastructure.md](.claude/rules/aws-infrastructure.md),
[secrets-management.md](.claude/rules/secrets-management.md).

---

## 18. Phases

**Six phases, 0 through 5.** Phases 0–4 run entirely on local Docker Compose and
cost **$0**. Only Phase 5 touches AWS.

Each phase ends with a working, demonstrable state and a verification report in
`docs/reports/`. No phase starts before the previous one's exit criteria pass.

---

### Phase 0 — Foundation *(complete)*
Repository structure, `PROJECT.md`, `.claude/rules/`, `.claude/skills/`,
lifecycle scripts with safety guards.

**Exit:** structure validated, all scripts syntax-clean, safety guards tested.

---

### Phase 1 — Core platform · local · $0
FastAPI skeleton with config, correlation ID, structured logging, and error
handling. SQLAlchemy models for all 10 entities plus Alembic migrations. Cognito
JWT verification and RBAC. S3 upload with the full file-safety chain. Qdrant
collection setup. Docker Compose bringing up Postgres, Qdrant, Redis, and the API.
Text-only ingestion (PDF/MD/TXT) and a basic tenant-filtered RAG query.

**Exit:** upload a PDF, ask a question, get a cited answer with `tenant_id`
enforced. Cross-tenant isolation tests pass.

---

### Phase 2 — Multimodal and Bedrock · local · ~$1 of Bedrock tokens
Bedrock client and the model registry (chat / vision / embedding roles).
Titan v2 embeddings. Nova Lite vision for images, diagrams, and tables. DOC/DOCX,
CSV, and Excel extraction. Transcribe for audio and video. Chunking strategy and
the full vector metadata payload. Async ingestion workers with job status.

**Exit:** all modalities in PROJECT.md section 3 ingest and become retrievable.

---

### Phase 3 — Intelligence and defence · local · ~$2 of Bedrock tokens
The complete 14-stage RAG pipeline. Prompt-injection scanning, output guardrails,
citation validation, relevance threshold, semantic cache, model routing,
reranking, token and cost tracking. LangGraph agentic workflows including policy
comparison. The full security test suite. The evaluation harness and baseline.

**Exit:** all 23 mandatory security tests pass (17 platform + the 6 role-hierarchy
tests added with onboarding); evaluation baseline recorded.

---

### Phase 4 — Frontend · local · $0
Next.js with TypeScript, Tailwind, and shadcn/ui. The Enterprise Knowledge AI
landing page. Six user pages, seven company-admin pages and the platform control
plane. Email/password sign-in with the first-sign-in challenge, password
recovery, silent renewal and server-side sign-out. Chat with streaming,
citations, and confidence. Document upload with live ingestion status. Voice input
through Transcribe into the existing pipeline. Seeded demo data. Playwright E2E.

**Exit:** all three dashboards visibly functional with real seeded data; E2E green.

---

### Phase 5 — AWS · the only phase that spends · ~$0.44 per session
Terraform for the ephemeral stack (ALB, ECS Fargate, ECR, S3, Cognito,
CloudWatch, IAM, Budget). GitHub Actions with OIDC. CodeDeploy blue-green.
CloudWatch metrics and alarms, LangSmith tracing. The lifecycle scripts fully
wired. First real `deploy -> verify -> seed -> e2e -> demo -> destroy` cycle.

**Exit:** the full cycle runs twice, proving reproducibility, for under $1 total.

**Status (2026-09-11) — in progress, exit not yet met.** Done: baseline and
`dev` stack applied; two CodeDeploy blue-green deployments succeeded and
`verify.sh` passed on the second; cost guard applied in dry-run. Not yet done:
a `destroy -> deploy` cycle; `rollback.sh` traffic shift; the GitHub Actions
deploy workflow; Bedrock on AWS (quotas are 0); `verify.sh` checks beyond
liveness.

---

### Budget by phase

| Phases | Where | Spend |
|---|---|---|
| 0, 1, 4 | Local Docker Compose | **$0** |
| 2, 3 | Local + Bedrock API calls | ~$3 |
| 5 | AWS ephemeral | ~$1 for two full cycles |
| **Total to build** | | **~$4** |

That leaves roughly $16 of the $20 ceiling for demos and interview walkthroughs.

---

## 19. Implementation rules

- Build from scratch.
- Minimalistic but professional architecture.
- No unnecessary technologies. Every component has a clear purpose and must be
  explainable in an interview.
- Build and test locally first, then deploy to AWS.
- Seed demo data.
- User, company-admin and platform dashboards must be visibly functional.
- Generate test and verification reports into [docs/reports/](docs/reports/).
- Proceed Phase 0 through Phase 5 in order.

---

## 20. Repository layout

```
.
├── PROJECT.md                  # this file - requirements and architecture
├── CLAUDE.md                   # entrypoint instructions for Claude
├── .claude/
│   ├── rules/                  # binding constraints (security, terraform, ...)
│   └── skills/                 # operational workflows (deploy, verify, ...)
├── backend/                    # FastAPI + LangGraph service
│   ├── app/
│   │   ├── api/v1/             # HTTP routes (incl. auth, admin, platform)
│   │   ├── core/               # config, auth, context, logging, correlation ID
│   │   ├── db/                 # SQLAlchemy models, tenant-filtered repositories,
│   │   │                       #   and control_plane (the one audited registry)
│   │   ├── schemas/            # Pydantic contracts
│   │   ├── services/
│   │   │   ├── identity.py     # Cognito admin + dev-local identity provider
│   │   │   ├── onboarding.py   # role/tenant guard functions (pure, DB-free)
│   │   │   ├── ingestion/      # multimodal extract -> chunk -> embed
│   │   │   ├── rag/            # retrieval, rerank, citation, model routing
│   │   │   ├── agents/         # LangGraph workflows
│   │   │   ├── security/       # injection scan, guardrails, validation
│   │   │   └── observability/  # metrics, cost, LangSmith
│   │   └── workers/            # async ingestion jobs
│   ├── alembic/                # migrations (0001 schema, 0002 onboarding)
│   ├── seeds/                  # demo data
│   └── tests/                  # unit | integration | security | evaluation | e2e
├── frontend/                   # Next.js dashboard
│   ├── src/app/(user)/         # dashboard, chat, documents, departments, usage, feedback
│   ├── src/app/admin/          # users, my company, documents, metrics, security, audit, deployments
│   ├── src/app/platform/       # companies (onboarding), onboarding trail
│   └── tests/e2e/              # Playwright
├── infra/
│   ├── terraform/
│   │   ├── modules/            # reusable AWS modules
│   │   └── envs/dev/           # ephemeral demo environment
│   └── docker/                 # Dockerfiles, compose
├── scripts/                    # lifecycle entrypoints
├── evaluation/datasets/        # RAG evaluation data
├── docs/reports/               # generated test and verification reports
└── .github/workflows/          # CI/CD
```
