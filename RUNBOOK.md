# Runbook — how to run this project

Two ways to run it:

| | Where | Cost | Needs |
|---|---|---|---|
| **Part A — Local** | Docker Compose on your machine | **$0** | Docker, Python 3.11+, Node 22+ |
| **Part B — AWS** | ECS Fargate, ephemeral | **~$0.30/session** | An AWS account, Terraform, AWS CLI |

**Do Part A first.** Everything works locally with no AWS account at all. Only
go to Part B when you want to prove the deployment story.

---

# PART A — Running locally ($0)

## The fastest path — one command

```bash
# Git Bash / macOS / Linux
./scripts/setup-local.sh

# Windows PowerShell
.\scripts\setup-local.ps1
```

This does every step below for you: creates `.env` with generated secrets, builds
the venv, installs dependencies, starts the containers, runs the migrations,
seeds the demo data, and prints a login token.

Safe to re-run. If you would rather do it by hand, or something failed, follow
the steps below.

---

## Step 1 — Create `.env`

```bash
cp .env.example .env
```

Open `.env` and set these **four**. Everything else already has a working local
default.

| Variable | What to put |
|---|---|
| `POSTGRES_PASSWORD` | any password, e.g. `ekba_local_dev` |
| `DATABASE_URL` | the **same** password inside the URL (see below) |
| `DEV_AUTH_SECRET` | any random string, **32+ characters** |
| `S3_BUCKET` | `ekba-dev-documents` |

```bash
POSTGRES_PASSWORD=ekba_local_dev
DATABASE_URL=postgresql+asyncpg://ekba:ekba_local_dev@localhost:5432/ekba
#                                      ^^^^^^^^^^^^^^ must match the line above
```

Leave these exactly as they are for local development:

```bash
ENVIRONMENT=dev
AI_PROVIDER=local          # deterministic stub - no AWS, no cost
DEV_AUTH_ENABLED=true      # local tokens instead of Cognito
S3_ENDPOINT_URL=http://localhost:9000
```

> **Two mistakes that cost real time:**
>
> 1. **`DATABASE_URL==postgres...` (double `=`)** — the app now catches this and
>    names the problem instead of throwing a SQLAlchemy traceback.
> 2. **The password in `DATABASE_URL` not matching `POSTGRES_PASSWORD`** —
>    Postgres keeps whatever password its volume was *first* created with, so the
>    two drift apart. Fix by resetting the volume:
>    `docker compose --env-file .env -f infra/docker/docker-compose.yml down -v`

The whole AWS block, the Cognito block and `LANGSMITH_API_KEY` can be ignored for
local development.

## Step 2 — Start the containers

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml \
  up -d postgres qdrant redis minio minio-init
```

> **`--env-file .env` is required, not optional.** The compose file lives in
> `infra/docker/`, so without it Compose never reads the repo-root `.env` and
> `${POSTGRES_PASSWORD}` silently falls back to its default — which then does not
> match what the application uses.

Check all four are healthy:

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml ps
```

## Step 3 — Install the backend

```bash
cd backend
python -m venv .venv

source .venv/Scripts/activate      # Windows (Git Bash)
# .\.venv\Scripts\Activate.ps1     # Windows (PowerShell)
# source .venv/bin/activate        # macOS / Linux

pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` is the pinned runtime set (FastAPI, uvicorn, alembic,
SQLAlchemy, Qdrant, boto3, LangGraph, the extractors).
`requirements-dev.txt` adds pytest, ruff, mypy and pip-audit.

## Step 4 — Create the database tables

```bash
# still in backend/, venv active
alembic upgrade head
```

If `alembic` is not found, the venv is not active. Activate it, or go through the
interpreter directly:

```bash
python -m alembic upgrade head
```

## Step 5 — Seed the demo data

```bash
python -m seeds.seed
```

Creates 2 tenants, 4 users, 12 documents across all 7 departments and all 5
modalities, 8 conversations with citations, usage rows, feedback, and 9 audit
events.

It also **indexes the documents into Qdrant** through the real
chunk → embed → upsert path, so retrieval and citations are genuine. Expect
`chunks_indexed: 41` in the output. Confirm it landed:

```bash
curl -s http://localhost:6333/collections/ekba_chunks | grep -o '"points_count":[0-9]*'
```

`"points_count":0` means chat will answer "I could not find anything" to every
question — re-run the seed.

## Step 6 — Start the backend

```bash
# in backend/, venv active
uvicorn app.main:app --reload --port 8000
```

Check it:

```bash
curl http://localhost:8000/api/v1/health
```

API docs: **http://localhost:8000/docs** (dev only — disabled outside dev).

## Step 7 — Start the frontend

In a **second terminal**:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:3000**.

## Step 8 — Log in

In a **third terminal**, mint a token:

```bash
cd backend
source .venv/Scripts/activate      # or .\.venv\Scripts\Activate.ps1

python -m seeds.dev_token                                    # normal user
python -m seeds.dev_token --role admin --user seed-admin-a   # admin
```

Copy the token it prints and paste it into the sign-in box.

| Token | Sees |
|---|---|
| `python -m seeds.dev_token` | Dashboard, Chat, Workflows, Documents, Departments, Usage, Feedback |
| `--role admin --user seed-admin-a` | the above **plus** Users, Tenants, Documents, AI Metrics, Security, Audit Logs, Deployments |

**The tenant-isolation demo — the best thing to show:**

```bash
python -m seeds.dev_token --tenant seed-tenant-contoso --user seed-admin-b --role admin
```

That is a full **administrator of the other tenant**. The admin pages open, but
the Documents page is empty and chat answers "I could not find anything" — a
complete admin of Contoso cannot see one row of Northwind's data.

Tokens last 12 hours.

## Step 9 — Ask a question

In the chat UI, or straight from the terminal:

```bash
TOKEN=$(python -m seeds.dev_token 2>/dev/null)
curl -s -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"What is the domestic hotel reimbursement limit?"}'
```

You should get **150 USD**, cited to *Travel Policy 2026.pdf* page 4.

Other questions the seeded corpus answers:

| Question | Answer |
|---|---|
| What is the daily meal allowance for domestic travel? | 60 USD |
| How much annual leave carries over? | 5 days |
| What PPE is required in the warehouse? | steel-toed boots, hi-vis vest |
| Who is paged first for a severity one incident? | the on-call engineer |
| How long does the standard NDA last? | three years |
| What changed between the 2024 and 2026 travel policies? | try this one in **Workflows** |

## Step 10 — Run the tests

```bash
cd backend
pytest tests/ -q                 # 181 tests
pytest tests/security -q         # the release-blocking ones
ruff check app tests seeds
```

```bash
cd frontend
npm run typecheck
npx playwright install chromium  # once, first time only
npx playwright test              # 15 journeys
```

Playwright skips the authenticated journeys unless it has tokens:

```bash
export E2E_USER_TOKEN=$(cd backend && python -m seeds.dev_token 2>/dev/null)
export E2E_ADMIN_TOKEN=$(cd backend && python -m seeds.dev_token --role admin --user seed-admin-a 2>/dev/null)
npx playwright test
```

Or just run `./scripts/test-e2e.sh`, which mints them for you.

## Daily loop, once set up

```bash
# terminal 1
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d postgres qdrant redis minio
cd backend && source .venv/Scripts/activate && uvicorn app.main:app --reload

# terminal 2
cd frontend && npm run dev

# terminal 3, when the token expires
cd backend && python -m seeds.dev_token --role admin --user seed-admin-a
```

Stop everything:

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml down
#                                                                 down -v  <- also wipes the data
```

Kill a backend still holding port 8000 (Windows PowerShell):

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## Optional — real Bedrock calls locally

Only if you want genuine model output before deploying:

1. `aws configure`
2. In `.env`: `AI_PROVIDER=bedrock`, and set `S3_ENDPOINT_URL=` (empty) plus a real `S3_BUCKET`
3. Confirm the models exist in your region:

```bash
aws bedrock list-foundation-models --region us-west-2 \
  --query 'modelSummaries[].modelId' --output table | grep -E "nova|titan-embed|gpt-oss"
```

Cost is roughly **$0.0002 per question**.

> **Switching `AI_PROVIDER` changes the embeddings**, and a Qdrant collection is
> fixed to one model's dimensions. After switching, reset and re-seed:
>
> ```bash
> docker compose --env-file .env -f infra/docker/docker-compose.yml down -v
> docker compose --env-file .env -f infra/docker/docker-compose.yml up -d postgres qdrant redis minio minio-init
> cd backend && alembic upgrade head && python -m seeds.seed
> ```

# PART B — Deploying to AWS (~$0.30 per session)

## Understand the split first

There are **two separate Terraform states**, and this is the most important
thing to understand about the deployment:

| State | Contains | Lifecycle |
|---|---|---|
| **`envs/baseline/`** | ECR, S3 bucket, Cognito, Secrets Manager, the $20 Budget, audit logs | **Applied once. Never destroyed.** |
| **`envs/dev/`** | VPC, ALB, ECS, CodeDeploy, task IAM roles | **Created per demo, destroyed after.** |

`destroy.sh` points at `envs/dev` **only**. It has no reference to the baseline
state, so it *cannot* delete your secrets or ECR images even if you wanted it
to. The safety rule is structural, not a promise.

---

## Step 0 — Use Git Bash for every AWS step

The lifecycle scripts are Bash. In VS Code: terminal dropdown (next to **+**) →
**Git Bash**. PowerShell will reject `export`, `\` line continuations and the
scripts themselves.

You do **not** need to export anything. The scripts read the account ID and
region from `infra/terraform/envs/baseline/terraform.tfvars`, and pick up the
AWS CLI from its default Windows install path if Git Bash cannot see it.

Check the CLI is configured for the right account:

```bash
aws sts get-caller-identity --query Account --output text
```

## Step 1 — Create the Terraform state backend (once, ever)

Fill in `infra/terraform/envs/baseline/terraform.tfvars` first (copy it from the
`.example` next to it — **never** put real values in the `.example`, which is
committed). Then:

```bash
./scripts/bootstrap-state.sh
```

It creates, and leaves alone on every re-run:

| Resource | Notes |
|---|---|
| S3 bucket `ekba-tfstate-<account>` | versioned, AES256-encrypted, public access blocked |
| DynamoDB table `ekba-tfstate-lock` | state locking, on-demand billing |
| `backend.hcl` in `envs/baseline` and `envs/dev` | git-ignored; holds the account ID |

…and runs `terraform init` in both environments. Both resources are tagged
`Lifecycle=protected`; `destroy.sh` never touches them. Cost: a few cents a month.

## Step 2 — Bedrock quota (can be done later)

The Bedrock **Model access page has been retired** — serverless models enable
themselves on first call. What can still block you is **quota**: a new account
may show every Bedrock inference quota as `0`, and the calls fail with
`ThrottlingException` / `Too many tokens per day`.

Check:

```bash
aws service-quotas list-service-quotas --service-code bedrock --region us-west-2 \
  --query "Quotas[?contains(QuotaName,'Titan Text Embeddings V2') || contains(QuotaName,'Nova Lite')].[QuotaName,Value]" \
  --output table
```

If they are `0` and not adjustable, open **Support Center → Create case →
Service limit increase → Amazon Bedrock (us-west-2)** for Titan Text Embeddings
V2 and Nova Lite. Confirm a verified payment method first — that is the usual
reason a new account gets zero.

**Nothing else in Part B needs Bedrock.** Deploy, blue-green, rollback and
destroy all work without it; only chat answers wait for the quota, and they start
working the moment it is granted — no redeploy.

## Step 3 — Apply the protected baseline (once)

```bash
cd infra/terraform/envs/baseline
terraform plan -out=tfplan      # READ IT: ECR x2, S3, Cognito, 4 empty secrets, $20 budget, OIDC role
terraform apply tfplan
terraform output                # keep s3_bucket and cognito_client_id for Step 5
cd ../../../..
```

`bootstrap-state.sh` already ran `terraform init` here. This state is
**protected** and is never destroyed.

## Step 4 — Give the secrets a value (once)

```bash
./scripts/set-secrets.sh
```

Terraform created four **empty** secret containers. This fills them with
generated values that go straight to Secrets Manager — never through Terraform
state, git, a command-line argument, or your terminal. A secret that already has
a value is **left untouched**.

The database secret is JSON with two keys built from **one** password — the
Postgres container reads `…:password::`, the API reads `…:url::` — so the two can
never drift apart.

## Step 5 — Deploy (this creates BLUE)

```bash
cd infra/terraform/envs/dev
cp terraform.tfvars.example terraform.tfvars
curl -s https://checkip.amazonaws.com       # your IP, for allowed_cidrs
cd ../../../..
```

Edit `infra/terraform/envs/dev/terraform.tfvars`:

```hcl
expected_aws_account_id = "<your account>"
allowed_cidrs           = ["<your ip>/32"]   # Terraform refuses 0.0.0.0/0
s3_bucket               = "<baseline output s3_bucket>"
cognito_client_id       = "<baseline output cognito_client_id>"
```

Do **not** set `backend_image`, and do **not** run `terraform apply` in
`envs/dev` yourself — the ECS service needs an image that already exists in ECR.
`deploy.sh` builds and pushes it first:

```bash
# Docker Desktop must be running
./scripts/deploy.sh
```

In order, stopping at the first failure:

1. account, region, $20 cost guard, Terraform state
2. local gate — ruff + the full pytest suite + frontend typecheck
3. build → push to ECR (git-SHA tag) → block on critical CVEs
4. `terraform plan` → **refuse any plan that destroys** → `apply`
5. CodeDeploy blue-green release
6. `verify.sh` against the deployed API → roll back automatically if it fails

Inside the task, the API container runs `alembic upgrade head`, then seeds the
demo data, then starts uvicorn — Postgres is a fresh private sidecar on every
task, so this is the only place the schema and data can be created. A failed
seed (for example, Bedrock quota still `0`) does not stop the API.

**The frontend runs on your machine**, pointed at the ALB — the task only runs
the API:

```bash
cd frontend
NEXT_PUBLIC_API_URL=$(terraform -chdir=../infra/terraform/envs/dev output -raw app_url) npm run dev
```

Sign in with a Cognito token (Step 6). `dev_token` does not work on AWS.

## Step 6 — Create the Cognito users

`dev_token` does **not** work here. It refuses unless `ENVIRONMENT=dev` and
`DEV_AUTH_ENABLED=true`, and Terraform sets `DEV_AUTH_ENABLED=false` on AWS.
Real tokens come from Cognito.

```bash
POOL=$(terraform -chdir=infra/terraform/envs/baseline output -raw cognito_user_pool_id)
CLIENT=$(terraform -chdir=infra/terraform/envs/baseline output -raw cognito_client_id)

# A normal user in tenant A
aws cognito-idp admin-create-user \
  --user-pool-id "$POOL" --username priya@northwind.example \
  --user-attributes \
      Name=email,Value=priya@northwind.example \
      Name=email_verified,Value=true \
      Name=custom:tenant_id,Value=seed-tenant-northwind \
      Name=custom:role,Value=user

aws cognito-idp admin-set-user-password \
  --user-pool-id "$POOL" --username priya@northwind.example \
  --password 'ChangeMe!2026x' --permanent
```

Repeat with `Name=custom:role,Value=admin` for an administrator, and with
`Value=seed-tenant-contoso` for the second tenant — that second tenant is what
makes the isolation demo possible.

Get a real token:

```bash
aws cognito-idp initiate-auth \
  --client-id "$CLIENT" --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=priya@northwind.example,PASSWORD='ChangeMe!2026x' \
  --query 'AuthenticationResult.AccessToken' --output text
```

Paste that into the sign-in box, exactly like the local token. **The application
code is identical** — the same `custom:tenant_id` and `custom:role` claims. Only
the signature changes: HS256 with a local secret becomes RS256 verified against
the Cognito JWKS.

Promote someone to admin without touching code or redeploying:

```bash
aws cognito-idp admin-update-user-attributes \
  --user-pool-id "$POOL" --username priya@northwind.example \
  --user-attributes Name=custom:role,Value=admin
```

The change takes effect on their next login, when a fresh token is issued.

## Step 7 — Watch a blue-green deployment happen

The first `deploy.sh` had nothing to replace, so there was no traffic shift to
watch. Deploy a **second** time to see the real thing.

Make any small visible change, commit it (the image is tagged with the git SHA,
so an unchanged SHA gives you the same image), then:

```bash
./scripts/deploy.sh
```

### Watch it live, in a second terminal

```bash
APP=ekba-dev

# The deployment as CodeDeploy sees it
watch -n 5 "aws deploy get-deployment --deployment-id \$(aws deploy list-deployments \
  --application-name $APP --deployment-group-name $APP-dg \
  --query 'deployments[0]' --output text) \
  --query 'deploymentInfo.[status,deploymentOverview]'"
```

You will see it move through `Created` → `InProgress` → `Succeeded`.

### The three things worth watching

**1. Two task sets exist at once**

```bash
aws ecs describe-services --cluster ekba-dev --services ekba-dev \
  --query 'services[0].taskSets[].[id,status,taskDefinition]' --output table
```

During the shift there are **two** rows: `PRIMARY` (blue, serving users) and
`ACTIVE` (green, being health-checked). This is the moment blue-green is
actually happening.

**2. Green is tested on port 8080 before it gets any real traffic**

```bash
ALB=$(terraform -chdir=infra/terraform/envs/dev output -raw app_url)

curl -s "$ALB/api/v1/health"        # port 80  -> BLUE, the live version
curl -s "${ALB/http:\/\//http://}:8080/api/v1/health"   # port 8080 -> GREEN, under test
```

The test listener is the whole point: CodeDeploy fully exercises green — health,
database, Qdrant, Redis and a real RAG query — while every real user is still
safely on blue.

**3. Traffic flips, then blue lingers**

```bash
aws elbv2 describe-listeners --load-balancer-arn <alb-arn> \
  --query 'Listeners[?Port==`80`].DefaultActions[].TargetGroupArn'
```

Run it before and after: the target group ARN changes from `ekba-dev-blue` to
`ekba-dev-green`. Blue stays alive for `rollback_window_minutes` (default **5**)
before terminating.

### Prove the rollback works

While blue is still alive, roll back:

```bash
./scripts/rollback.sh
```

Traffic returns to blue in seconds. No rebuild, no image pull, no Terraform.
`rollback.sh` is application-level only — it never runs `terraform destroy` and
never touches data or secrets. It also **refuses to proceed** if the failed
release applied a non-backward-compatible migration, because rolling the app back
under an incompatible schema can corrupt data.

### Deliberately break a deploy (the best demo)

Point the task at an image that fails its health check and deploy. What you
should see:

1. Green starts and fails the health check on the test listener
2. **Traffic never moves** — the production listener stays on blue
3. CodeDeploy marks the deployment failed and auto-rolls back
4. Users saw nothing at all

That is the entire value of blue-green in one demo: a broken release that never
reached a single user.

## Step 8 — Demo, then destroy

```bash
./scripts/cost-check.sh     # what is running and what it has cost so far
```

When you are finished — **this is the normal end of a session, not an emergency:**

```bash
./scripts/destroy.sh
```

It will:

1. Verify account, region, workspace, state
2. Print **every resource** it would destroy
3. **Scan the plan for protected resources and abort** if any appear
4. Require you to type `DESTROY ekba-dev` exactly — not `y`
5. Destroy only what is in the `envs/dev` state
6. Confirm your secrets survived, and write an audit report

**Preserved, always:** secrets, Terraform state, ECR images, the budget, audit logs.

Confirm you are back to $0/hour:

```bash
./scripts/cost-check.sh      # should report nothing billable running
```

## Step 9 — Deploy again

```bash
./scripts/deploy.sh
```

Same command. The baseline is still there, so it rebuilds in minutes with freshly
seeded data. **This loop is the whole point** — a destroyed environment costs
$0/hour, and being able to rebuild it on demand is what makes the $20 ceiling
work.


---

# Blue-green deployment — how it actually works

This is the concept most worth being able to explain, so here it is in full.

## The problem it solves

A naive deploy stops the old version and starts the new one. Two things go
wrong: there is a gap with no working service, and if the new version is broken
you have already destroyed the thing that worked.

Blue-green fixes both by **running both versions at once** and treating the
switch between them as a separate, reversible step.

## The pieces

| Piece | Role |
|---|---|
| **Blue** | The version currently serving real users |
| **Green** | The new version, running but receiving no real traffic |
| **Two target groups** | ALB pools — one holds blue, one holds green |
| **Production listener** (port 80) | Where real users arrive |
| **Test listener** (port 8080) | Where CodeDeploy probes green privately |

## The sequence

```
1. BEFORE
   users ──► :80 ──► [blue target group] ──► blue task (v1)
                     [green target group] ──► (empty)

2. GREEN STARTS
   CodeDeploy launches the v2 task and registers it in the green group.
   Real users are still entirely on blue.

   users ──► :80 ──► [blue]  ──► blue task (v1)     ← all real traffic
   nobody ─► :8080 ─► [green] ──► green task (v2)   ← starting up

3. HEALTH CHECKS  ← the critical step
   CodeDeploy probes green through the TEST listener:
     · /api/v1/health responds 200
     · database reachable
     · Qdrant reachable
     · Redis reachable
     · a real RAG smoke query returns a complete envelope

   If ANY check fails: green is destroyed, blue never moved, deploy fails.
   Users saw nothing.

4. TRAFFIC SHIFTS
   Only now does the ALB repoint the production listener.

   users ──► :80 ──► [green] ──► green task (v2)   ← real traffic
             blue task still alive, receiving nothing

5. ROLLBACK WINDOW (5 minutes by default)
   Blue is kept running. If the 5xx alarm fires, CodeDeploy points :80 back at
   blue in seconds. No rebuild, no image pull, no deploy.

6. CLEANUP
   After the window, blue terminates. Green becomes the new blue.
```

## Why the test listener matters

Without it, "health check" means checking the thing users are already hitting —
too late. The test listener lets CodeDeploy fully exercise green, including a
real RAG query, while real users are still safely on blue.

## What rollback is (and is not)

Rollback is **one traffic shift back to blue**. That is all.

It does **not** destroy infrastructure, delete data, touch secrets, or run
`terraform destroy`. `scripts/rollback.sh` enforces this: it checks migration
compatibility first and **refuses to proceed** if the failed release applied a
non-backward-compatible migration, because rolling the app back under an
incompatible schema can corrupt data.

## Why migrations must be backward compatible

During step 4, v1 and v2 are *both* running against the *same* database. So:

- ✅ Add a nullable column, add a table, add an index
- ❌ Drop a column v1 still reads, rename a column, change a type incompatibly

A destructive schema change is split across two releases: release A adds the new
shape and writes to both; release B removes the old one once nothing reads it.

## The trade-off, honestly

Blue-green costs **double compute during the shift** — two task sets for a few
minutes. At this project's scale that is roughly one extra cent per deploy,
which is a fine price for a rollback that takes seconds instead of a rebuild.

---

# Command reference

## Local

| Command | What it does |
|---|---|
| `docker compose -f infra/docker/docker-compose.yml up -d` | Start Postgres, Qdrant, Redis, MinIO |
| `alembic upgrade head` | Create/update database tables |
| `python -m seeds.seed` | Load demo data |
| `python -m seeds.dev_token` | Mint a login token |
| `uvicorn app.main:app --reload` | Run the API |
| `npm run dev` | Run the frontend |
| `pytest tests/ -q` | All 164 backend tests |
| `npx playwright test` | Frontend journeys |

## AWS

| Command | What it does | Safe? |
|---|---|---|
| `./scripts/bootstrap-state.sh` | One-time: S3 state bucket + lock table + `terraform init` | Creates protected resources only |
| `./scripts/set-secrets.sh` | One-time: fill the empty secrets | Never overwrites a value |
| `./scripts/cost-check.sh` | Spend + what is running | Read-only |
| `./scripts/deploy.sh` | Full deploy pipeline | Never destroys |
| `./scripts/verify.sh` | Infra + AI pipeline check | Read-only |
| `./scripts/seed.sh` | Demo data | Refuses prod |
| `./scripts/test-e2e.sh` | Full journeys | Refuses prod |
| `./scripts/rollback.sh` | Traffic back to blue | App-level only |
| `./scripts/destroy.sh` | Tear down ephemeral stack | **The only `terraform destroy`** |

---

# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: asyncpg` | venv not active, or deps not installed | `source .venv/Scripts/activate`, then `pip install -r requirements.txt -r requirements-dev.txt` |
| `connection refused :5432` | Containers not up | `docker compose … up -d postgres` |
| Login rejected | Token expired (12h) or wrong `DEV_AUTH_SECRET` | Mint a new one |
| `relation "documents" does not exist` | Migrations not run | `alembic upgrade head` |
| Dashboards empty | Not seeded | `python -m seeds.seed` |
| Qdrant dimension mismatch | Switched `AI_PROVIDER` | Restart Qdrant, re-seed |
| `400 Bad Request` on every call | Host not in `TRUSTED_HOSTS` | Add it to `.env` |
| CORS error in browser | Origin not allow-listed | Add to `CORS_ALLOWED_ORIGINS` |
| `deploy.sh` refuses to run | Spend at/over $20 | `cost-check.sh`, then `destroy.sh` |
| `ValidationException` from Bedrock | Model not enabled in region | Bedrock → Model access |
| Terraform "Wrong AWS account" | Wrong profile | Check `aws sts get-caller-identity` |

---

# Cost summary

| Activity | Cost |
|---|---|
| All local development | **$0** |
| Local with real Bedrock | ~$0.0002 per question |
| One 4-hour AWS demo | **~$0.30** |
| Building everything (phases 2, 3, 5) | ~$4 total |

**Guards in place:** AWS Budget at $20 with 50/80/100% alerts · `deploy.sh`
refuses past the ceiling · `cost-check.sh` alarms if a NAT Gateway or RDS
instance ever appears (they never should) · per-user daily AI ceiling in the app.

**The single most important habit:** run `./scripts/destroy.sh` when a demo ends.
