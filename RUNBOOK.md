# Runbook — how to run this project

Two ways to run it:

| | Where | Cost | Needs |
|---|---|---|---|
| **Part A — Local** | Docker Compose on your machine | **$0** | Docker, Python 3.11+, Node 22+ |
| **Part B — AWS** | ECS Fargate, ephemeral | **~$0.09/hour** (~$0.37 per 4-hour session) | An AWS account on the **Paid** plan, Terraform, AWS CLI, Docker |

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
pytest tests/ -q                 # 190 tests at the last deploy gate (2026-09-11)
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

# PART B — Deploying to AWS (~$0.09/hour while it exists)

## Where things stand (last verified 2026-09-11)

| Item | Status |
|---|---|
| Account plan | **Paid.** The Free plan blocks CodeDeploy entirely (see Step 0) |
| Region | `us-west-2` |
| Promotional credits | $158.99 remaining at the last check |
| Protected baseline (`envs/baseline`) | Applied |
| Cost guard (`envs/cost-guard`) | Applied, **`dry_run = true`**: it reports, it does not act |
| Ephemeral stack (`envs/dev`) | Deployed; CodeDeploy blue-green deployments succeed and `verify.sh` passes |
| Bedrock | Inference quotas are **0** in this account: the API is healthy, but chat and demo seeding cannot run (Step 2) |
| Frontend (`envs/frontend`, Amplify) | Code and scripts ready; **not applied yet** — see [Part C](#part-c--frontend-on-aws-amplify) |

## Understand the split first

There are **four separate Terraform states**, and this is the most important
thing to understand about the deployment:

| State | Contains | Lifecycle |
|---|---|---|
| **`envs/baseline/`** | ECR ×2, S3 document bucket, Cognito pool + client, 4 Secrets Manager secrets, `ekba-dev-monthly` budget, audit log group, GitHub OIDC deploy role | **Applied once. Never destroyed.** |
| **`envs/cost-guard/`** | 2 budgets, 2 SNS topics, the kill-switch Lambda and its IAM role, an hourly EventBridge rule | **Applied once. Never destroyed.** Must outlive every destroy of `dev` |
| **`envs/dev/`** | VPC, 2 public subnets, internet gateway, security groups, ALB (listeners `:80` and `:8080`), blue + green target groups, ECS cluster / service / task definition, CodeDeploy app + deployment group, IAM roles, log group, 2 alarms | **Created per demo, destroyed after.** |
| **`envs/frontend/`** | Amplify app + branch (imported after the console connection), CloudFront API distribution + cache policy | **Persistent, ~$0 idle.** Driven only by `deploy-frontend.sh` (Part C) |

The state backend itself — S3 bucket `ekba-tfstate-<account-id>` and DynamoDB
table `ekba-tfstate-lock` — is created by `bootstrap-state.sh`, outside Terraform.

`destroy.sh` points at `envs/dev` **only**. It has no reference to the baseline,
cost-guard or frontend states, so it *cannot* delete your secrets, ECR images,
the kill switch or the Amplify app even if you wanted it to. The safety rule is
structural, not a promise.

---

## Step 0 — Account, credentials and shell

### The account must be on the Paid plan

On the AWS **Free account plan**, every CodeDeploy API call fails in every region,
even for an administrator:

```
SubscriptionRequiredException: The AWS Access Key Id needs a subscription for the service
```

This is not an IAM problem (an IAM denial is `AccessDeniedException` and names
the action) and not a regional one. Check the plan:

```bash
aws freetier get-account-plan-state --region us-east-1 \
  --query '{plan:accountPlanType,status:accountPlanStatus,credits:accountPlanRemainingCredits.amount}'
```

Upgrading to the Paid plan keeps the remaining credits, but removes the Free
plan's guarantee that nothing is charged beyond them. That is why the cost guard
(Step 4b) exists — arm it before relying on the Paid plan.

### Account and region come from one place

The expected account ID and region live in the git-ignored
`infra/terraform/envs/baseline/terraform.tfvars`. Every lifecycle script and every
Terraform state refuses to run against any other account. The AWS CLI uses its
default profile — check it points at the right account:

```bash
aws sts get-caller-identity --query Account --output text
```

### Use Git Bash for every AWS step

The lifecycle scripts are Bash. In VS Code: terminal dropdown (next to **+**) →
**Git Bash**. PowerShell will reject `export`, `\` line continuations and the
scripts themselves.

You do **not** need to export anything. The scripts read the account ID and
region from the baseline `terraform.tfvars`, and pick up the AWS CLI from its
default Windows install path if Git Bash cannot see it.

Two shell traps hit during the first deployment:

- **Git Bash rewrites arguments that start with `/`** into Windows paths, so
  `--log-group-name /ekba/dev/service` fails with `InvalidParameterException`.
  Prefix the command with `MSYS_NO_PATHCONV=1`.
- **Windows PowerShell 5.1 mangles Terraform flags**: `-backend-config=backend.hcl`
  is split at the dot ("Too many command line arguments") and `-chdir=$dir` is not
  expanded. Quote them (`"-chdir=$dir"`), or use Git Bash.

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

`bootstrap-state.sh` does **not** write `envs/cost-guard/backend.hcl`. Copy
`envs/dev/backend.hcl` there and change one line:

```hcl
key = "cost-guard/terraform.tfstate"
```

`terraform init` prints a `Deprecated Parameter` warning about `dynamodb_table`
in all three states. It is harmless; locking works.

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

> **Current status (checked 2026-09-11): the quotas are 0 in this account.**
> Applied values in `us-west-2` include *On-demand model inference requests per
> minute* and *tokens per minute for Amazon Titan Text Embeddings V2*,
> *Cross-region model inference requests per minute for Amazon Nova Lite*, and
> *Model invocation max tokens per day for Amazon Nova Lite* — all `0`, listed as
> not adjustable. On AWS the in-task seed therefore fails on its first embedding
> batch:
>
> ```
> bedrock_embed_failed ... "error":"ThrottlingException"
> UpstreamError: The embedding service is unavailable.
> seed failed - continuing without demo data
> ```

**Nothing else in Part B needs Bedrock.** Deploy, blue-green, verification and
destroy all work without it; only chat answers and seeding wait for the quota.
Bedrock calls work the moment the quota is granted, but the running task has no
demo data — it needs one more `deploy.sh` (or destroy + deploy), because the seed
only runs at task start.

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

> The baseline budget `ekba-dev-monthly` counts spend **after credits**. While
> credits last it reads `$0.00` — it cannot warn you until credits run out. The
> cost guard (Step 4b) is the protection that measures usage *before* credits.

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

## Step 4b — Apply the cost guard (once)

The kill switch that stops `envs/dev` before it spends past the ceiling. Full
design, thresholds and ownership rules:
[infra/terraform/README.md](infra/terraform/README.md#envscost-guard--protected-always-on).

```bash
cd infra/terraform/envs/cost-guard
cp terraform.tfvars.example terraform.tfvars   # account ID, alert_email, tfstate_bucket
terraform init -backend-config=backend.hcl     # after creating backend.hcl (Step 1)
terraform plan -out=tfplan                     # expect: 15 to add, 0 to change, 0 to destroy
terraform apply tfplan
cd ../../../..
```

Then, in order:

1. **Confirm the SNS subscription.** AWS emails an "AWS Notification –
   Subscription Confirmation" link to `alert_email`. Budget alert emails arrive
   without it; the kill switch's shutdown reports do not.
2. **Run the dry-run test.** It reports and changes nothing:

   ```bash
   aws lambda invoke --function-name ekba-dev-cost-guard \
     --cli-binary-format raw-in-base64-out --payload '{"trigger":"manual"}' out.json
   cat out.json
   ```

   The result on 2026-09-11, with the stack deployed:

   ```json
   {"status": "dry-run", "trigger": "manual",
    "actions": ["WOULD scale ECS service ekba-dev/ekba-dev from 1 to 0 tasks",
                "WOULD delete load balancer ekba-dev-alb (and its listeners)"],
    "refused": [], "errors": []}
   ```

   `{"trigger":"session-limit"}` tests the 8-hour check the same way.
3. **Arm it only deliberately.** Set `dry_run = false` in the cost-guard
   `terraform.tfvars`, plan (one in-place change to the Lambda), review, apply.
   Once armed, the next hourly check stops any stack that has existed longer than
   8 hours — including one that is already running.

Check at any time whether it is armed:

```bash
./scripts/cost-check.sh     # "kill switch ... is ARMED" or "... is in DRY RUN"
```

## Step 5 — Deploy (the first run creates the stack)

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

> **`allowed_cidrs` is one IP address, and home IPs change.** The ALB security
> group admits only `allowed_cidrs`, on ports 80 and 443. Port 8080 — the
> CodeDeploy test listener — is open to no one. When your ISP hands you a new
> public IP, your requests are dropped silently: `curl` times out (exit code 28,
> HTTP `000`), `verify.sh` prints `FAIL health endpoint`, and `deploy.sh` reports
> a failed deploy — **even though CodeDeploy succeeded and the ALB's own health
> checks pass**. This is exactly what failed the first deployment.
>
> Before every deploy:
>
> ```bash
> curl -s https://checkip.amazonaws.com
> grep allowed_cidrs infra/terraform/envs/dev/terraform.tfvars
> ```
>
> If they differ, update `allowed_cidrs` and run `deploy.sh`. Its plan shows one
> in-place change to the `ekba-dev-alb` security group (0 to add, 1 to change,
> 0 to destroy). Anyone else — a teammate, another network, a GitHub-hosted
> runner — is blocked too until their IP is added.

Do **not** set `backend_image`, and do **not** run `terraform apply` in
`envs/dev` yourself — the ECS service needs an image that already exists in ECR.
`deploy.sh` builds and pushes it first:

```bash
# Docker Desktop must be running
./scripts/deploy.sh
```

In order, stopping at the first failure:

1. **Preflight** — account, region, Terraform state, and the cost guard: refuses
   if gross usage (credits excluded) since `COST_GUARD_START` has reached
   `COST_GUARD_SHUTDOWN_USD` ($18)
2. **Local gate** — `ruff check`, `ruff format --check`, pytest
   (`unit`, `integration`, `security`, `evaluation` — 190 tests), frontend typecheck
3. **Image** — refuses uncommitted changes in `backend/` or the Dockerfile (the
   tag must match the commit); reuses the `:<git-sha>` image if ECR already has
   it, otherwise builds and pushes; blocks on critical scan findings
4. **Terraform** — `fmt -check`, `validate`, `plan`. A plan that deletes anything
   stops and requires typing `I REVIEWED THIS PLAN`. Then applies that plan file
5. **CodeDeploy blue-green** — creates a deployment and waits for
   `deployment-successful`
6. **`verify.sh`** against the ALB URL. On failure, `rollback.sh` runs — which
   today only re-runs `verify.sh` (see "What rollback is")
7. A report in `docs/reports/`

Observed timing: about 11–12 minutes end to end, of which the CodeDeploy
deployment is about 9 (including the 5-minute rollback window).

Inside the task, the API container runs `alembic upgrade head`, then seeds the
demo data, then starts uvicorn — Postgres is a fresh private sidecar on every
task, so this is the only place the schema and data can be created. A failed
seed (for example, Bedrock quota still `0`) does not stop the API.

Check it from your machine:

```bash
curl -s "$(terraform -chdir=infra/terraform/envs/dev output -raw app_url)/api/v1/health"
# {"status":"ok","environment":"dev","components":[]}
```

`/api/v1/health` is **liveness only** — it answers as long as the process is up,
which is why `components` is empty. `/api/v1/health/ready` checks Postgres, Redis
and Qdrant and returns `503` if any is down. Neither is authenticated.

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

**Every `deploy.sh` run is a full blue-green deployment**, even when nothing
changed — the second deployment on 2026-09-11 re-released the same image and
task definition and still shifted traffic. So just run it again:

```bash
./scripts/deploy.sh
```

### Watch it live, in a second terminal

```bash
APP=ekba-dev
ID=$(aws deploy list-deployments --application-name $APP --deployment-group-name $APP-dg \
       --query 'deployments[0]' --output text)

aws deploy get-deployment --deployment-id "$ID" --query 'deploymentInfo.status'

# The eight lifecycle stages. For ECS the target ID is <cluster>:<service>.
aws deploy get-deployment-target --deployment-id "$ID" --target-id $APP:$APP \
  --query 'deploymentTarget.ecsTarget.lifecycleEvents[].[lifecycleEventName,status]' --output text
```

The stages run `BeforeInstall → Install → AfterInstall → AllowTestTraffic →
AfterAllowTestTraffic → BeforeAllowTraffic → AllowTraffic → AfterAllowTraffic`.
After the last one the status stays `InProgress` for the 5-minute rollback
window, then becomes `Succeeded`. (Git Bash has no `watch`; re-run the commands.)

### The three things worth watching

**1. Two task sets exist at once**

```bash
aws ecs describe-services --cluster ekba-dev --services ekba-dev \
  --query 'services[0].taskSets[].[status,runningCount,stabilityStatus]' --output table
```

During the deployment there are **two** rows: `PRIMARY` (the live version) and
`ACTIVE` (the replacement). After the shift the old one shows `DRAINING` with 0
running.

**2. The replacement is health-checked before it gets real traffic**

Port 8080 is not open to your IP, so you cannot `curl` the replacement directly.
Watch its target group instead:

```bash
for TG in ekba-dev-blue ekba-dev-green; do
  ARN=$(aws elbv2 describe-target-groups --names $TG --query 'TargetGroups[0].TargetGroupArn' --output text)
  echo "$TG: $(aws elbv2 describe-target-health --target-group-arn $ARN \
    --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State]' --output text)"
done
```

During `Install` the new target shows `unused` / `Target.NotInUse` — normal: the
ALB only health-checks a group once a listener points at it. After
`AllowTestTraffic` moves `:8080` to it, it turns `healthy`.

**3. Traffic flips, then the old version lingers**

```bash
ALB_ARN=$(aws elbv2 describe-load-balancers --names ekba-dev-alb \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)
aws elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" \
  --query 'Listeners[].[Port,DefaultActions[0].TargetGroupArn]' --output text
```

The two target groups **alternate** roles: one deployment moves `:80` from
`ekba-dev-blue` to `ekba-dev-green`, the next moves it back. The old task set
stays alive for `rollback_window_minutes` (default **5**) before terminating.

### What rollback is today

`./scripts/rollback.sh` does **not** shift traffic yet. Its CodeDeploy step is a
placeholder that prints `CodeDeploy rollback not yet implemented (Phase 5)`; the
script then re-runs `verify.sh` and writes an incident report. It changes nothing
in AWS. So when `deploy.sh` prints `ROLLBACK FAILED - ESCALATE TO A HUMAN`, it
means the post-rollback `verify.sh` failed — for the same reason the deploy's did.

What *is* configured in Terraform is CodeDeploy's own automatic rollback, on
`DEPLOYMENT_FAILURE` and on the `ekba-dev-5xx` alarm (more than five target 5xx
responses a minute, two minutes running). The manual equivalent, for a deployment
that is still in progress — including its rollback window — is the command the
GitHub workflow uses. **Neither has been exercised in this project yet.**

```bash
aws deploy stop-deployment --deployment-id "$ID" --auto-rollback-enabled
```

### Deliberately break a deploy (not yet exercised)

Point the task at an image that fails its health check and deploy. By the
configuration, the replacement never becomes healthy, the production listener
never moves, the deployment fails, and auto-rollback keeps the original task set
serving. Run it once before relying on it in a demo.

## Step 8 — Demo, then destroy

```bash
./scripts/cost-check.sh     # gross / credits / net spend, what is running, kill-switch state
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

**Preserved, always:** secrets, Terraform state, ECR images, budgets, audit logs,
and the whole cost guard.

If the **cost guard** stopped the stack (ECS at 0 tasks, ALB deleted), run
`destroy.sh` to clean up the rest, then `deploy.sh`. `deploy.sh` on its own would
leave the service at 0 tasks, because Terraform ignores `desired_count`.

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

## The GitHub Actions deploy workflow (not yet run)

`.github/workflows/deploy.yml` is a manual (`workflow_dispatch`) alternative to
`deploy.sh`: type `DEPLOY` to confirm, authenticate with OIDC through the baseline
role, then plan, reject destructive plans, apply, CodeDeploy, health check,
`verify.sh`, and `stop-deployment --auto-rollback-enabled` on failure. It reads
the repository variables `AWS_REGION`, `EXPECTED_AWS_ACCOUNT_ID`,
`DEMO_ALLOWED_CIDR`, `S3_BUCKET`, `COGNITO_CLIENT_ID` and the secret
`AWS_DEPLOY_ROLE_ARN`.

It has **not been run yet**, and reading it against the current setup shows
three problems to fix first:

1. **Its cost guard can never block.** It reads month-to-date spend with credits
   netted in (≈$0 while credits last), and the deploy role has no
   `ce:GetCostAndUsage` permission, so the `|| echo 0` fallback always passes.
2. **Its health check cannot reach the ALB.** The runner's IP is not in
   `DEMO_ALLOWED_CIDR`, so the `curl` loop and `verify.sh` will time out — the
   same failure as a changed home IP.
3. **Two sources for one rule.** CI applies `allowed_cidrs` from
   `vars.DEMO_ALLOWED_CIDR`; `deploy.sh` applies it from `envs/dev/terraform.tfvars`.
   Whichever applied last owns the security group.

---

# PART C — Frontend on AWS Amplify

The Next.js frontend is a **static export** hosted on **AWS Amplify Hosting**,
built and deployed automatically on every push to `aws-deployment`. Everything
else — infrastructure, backend wiring, builds, verification — is one command:

```bash
./scripts/deploy-frontend.sh
```

## Where things stand (2026-09-11)

Code, Terraform and scripts are complete and **not applied**.
`./scripts/deploy-frontend.sh --plan-only` passes: gate green, frontend plan
`2 to add, 0 to change, 0 to destroy` (CloudFront), backend preview
`0 to add, 1 to change, 0 to destroy` (the ALB security group rule). Still to do:
push `amplify.yml` and `frontend/next.config.mjs`, connect the repository in the
Amplify console, and the first real run.

## Architecture

```
git push aws-deployment ──► Amplify CI/CD (GitHub App) ──► amplify.yml build ──► static site
                                                                                    │
Browser ──HTTPS──► https://aws-deployment.<appId>.amplifyapp.com ◄──────────────────┘
   │
   └──HTTPS──► https://<id>.cloudfront.net ──HTTP :80──► ekba-dev-alb ──► ECS task (API)
               no caching, Authorization forwarded      security group: your /32 + CloudFront ranges
                                                        CORS: localhost:3000 + the Amplify origin
```

**Why CloudFront.** The page is HTTPS and the ALB is HTTP-only, so browsers block
the API calls (mixed content). Amplify's own reverse proxy supports only HTTPS
targets. A CloudFront distribution with its default `*.cloudfront.net`
certificate gives the API an HTTPS URL without a domain name — and that URL
stays the same when the ALB is recreated; only the origin is updated.

| Piece | Where it is defined |
|---|---|
| Build spec | `amplify.yml` (repo root; app root `frontend`) |
| Static export switch | `frontend/next.config.mjs` — `NEXT_OUTPUT_MODE=export`; without it, the Docker `standalone` output is unchanged |
| Amplify app `ekba-dev-frontend`, branch `aws-deployment` | Created by the console connection, then managed in `infra/terraform/envs/frontend` |
| CloudFront distribution + cache policy `ekba-dev-api-no-cache` | `infra/terraform/modules/frontend` |
| ALB ingress from CloudFront, CORS origin | `infra/terraform/envs/dev/frontend.auto.tfvars` — **generated** by the script, git-ignored, loaded by every `deploy.sh` |

## One-time setup

```bash
# 1. The frontend state (bootstrap-state.sh does not create these)
cd infra/terraform/envs/frontend
cp terraform.tfvars.example terraform.tfvars    # account ID, repository_url, branch_name
cp ../dev/backend.hcl backend.hcl               # then set: key = "frontend/terraform.tfstate"
cd ../../../..

# 2. Review what would happen - changes nothing
./scripts/deploy-frontend.sh --plan-only

# 3. First run: creates CloudFront, then stops with exit code 3 and the console steps
./scripts/deploy-frontend.sh
```

4. **Push the build files.** Amplify builds GitHub, not your working tree:
   `amplify.yml` and `frontend/next.config.mjs` must be on `origin/aws-deployment`.
5. **Connect the repository — the one step that needs a browser.** In the
   Amplify console (`us-west-2`): *Create app → GitHub →* authorize and install
   the **AWS Amplify** GitHub App for **only** this repository → branch
   `aws-deployment` → tick **My app is a monorepo**, root `frontend` → app name
   `ekba-dev-frontend` → keep the detected `amplify.yml`, add no environment
   variables → *Save and deploy*. The first build **fails on purpose**:
   `NEXT_PUBLIC_API_URL` is not set until the next step.
6. **Run it again:**

   ```bash
   ./scripts/deploy-frontend.sh
   ```

   It finds the app by name, imports it (the plan shows `will be imported`),
   applies its settings, wires the backend — which runs `./scripts/deploy.sh`,
   about 12 minutes — builds, and verifies.

No GitHub token is used anywhere: the GitHub App authorization lives in Amplify,
and nothing secret passes through Terraform state.

## Everyday use

| You want to | Run |
|---|---|
| Ship a frontend code change | `git push origin aws-deployment` — Amplify builds and deploys automatically (~4 min) |
| Deploy, update or just re-verify everything | `./scripts/deploy-frontend.sh` |
| See what it would change | `./scripts/deploy-frontend.sh --plan-only` |
| Force a fresh build of the branch head | `./scripts/deploy-frontend.sh --rebuild` |
| Change the frontend without redeploying the backend | `./scripts/deploy-frontend.sh --no-backend` (reports the wiring still needed) |
| Roll the site back to the previous build | `./scripts/rollback-frontend.sh` |
| Roll back to a specific commit | `./scripts/rollback-frontend.sh --to <sha>` |
| After `destroy.sh` + `deploy.sh` (new ALB) | `./scripts/deploy-frontend.sh` — updates the CloudFront origin in place; no rebuild |

## What `deploy-frontend.sh` does

1. **Preflight** — account, region, tools, the $18 cost guard; reads the ALB from
   `envs/dev` and checks it exists in AWS (stops if the backend is not deployed
   or the cost guard removed the ALB).
2. **Source** — `origin/aws-deployment` head; warns about uncommitted frontend
   changes and a missing `amplify.yml` on the remote.
3. **Gate** — `npm ci` when needed, `lint`, `typecheck`, the static-export build.
4. **Amplify app** — found by name; stops if there are duplicates, if it is
   connected to another repository, or if the branch is missing.
5. **Terraform (`envs/frontend`)** — `fmt -check`, `validate`, `plan`. A plan that
   deletes or replaces anything needs the typed phrase `I REVIEWED THIS PLAN`.
   Otherwise it applies the reviewed plan file.
6. **Backend wiring** — writes `frontend.auto.tfvars`, compares it with the
   **live** security group and the live task definition's CORS, and runs
   `./scripts/deploy.sh` only if they differ.
7. **Amplify build** — waits for any build already running; starts one only if
   none has succeeded, the Amplify settings changed, the live build is not the
   branch head, or `--rebuild` was given.
8. **Verification** (every check runs; one failure fails the deploy):
   - latest Amplify build `SUCCEED`
   - site `/` → 200, `/chat` → 200, an unknown path lands on the not-found page
   - HSTS and CSP headers present; HTTP redirects to HTTPS
   - the deployed bundle contains the CloudFront API URL
   - `/api/v1/health` through CloudFront → 200
   - CORS preflight from the Amplify origin is allowed
   - `/api/v1/me` without a token → 401 `Authentication required.`
   - with a bogus token → 401 `Invalid token.` — the different message proves
     CloudFront forwarded the `Authorization` header
9. **Report** in `docs/reports/`.

Exit codes: `0` success · `1` failure · `3` the console connection is pending.
Re-running is always safe: nothing is duplicated, the backend is redeployed only
on real drift, and no build starts while one is running.

## Configuration details

- **`NEXT_PUBLIC_API_URL`** is baked into the bundle at build time. Terraform sets
  it on the Amplify app to the CloudFront URL; `amplify.yml` refuses to build if
  it is not `https://…`.
- **Routing.** The export writes one HTML file per page (`chat.html`, …) and
  Amplify serves `/chat` from `chat.html`. The usual SPA catch-all rewrite to
  `index.html` would render the landing page at every URL, so the only rule is
  unknown paths → `/404.html` (status `404`). In practice Amplify answers a
  missing path with redirects — `301` to add a trailing slash, then `302` to
  `/404.html`, which returns `200` — so visitors see the not-found page, but the
  final HTTP status is not a literal 404.
- **Headers** (Amplify, since a static export cannot send them): HSTS, `nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a CSP whose
  `connect-src` allows only the site and the CloudFront API. `script-src` and
  `style-src` need `'unsafe-inline'` for the bootstrap scripts Next.js emits.
- **CloudFront.** No caching (TTL 0; max 1 s, which AWS requires for a header in
  the cache key). `Authorization` is in the cache key — the only way CloudFront
  forwards it on GET — so a response can never be served to another user. Every
  other viewer header is forwarded except `Host`, so the ALB sees its own DNS
  name, which the API's `TRUSTED_HOSTS` accepts. HTTPS only; 60 s origin timeout.
- **Security trade-off.** The ALB now also admits CloudFront's origin-facing
  ranges (46 entries of the security group's 60-rule quota) on port 80, so the
  API is reachable through CloudFront from anywhere. Every endpoint except
  `/health` still needs a valid Cognito token, and rate limits still apply.

## Limitations

- The site depends on the ephemeral backend. After `destroy.sh`, or once an armed
  cost guard deletes the ALB, API calls fail until `./scripts/deploy.sh` and then
  `./scripts/deploy-frontend.sh`.
- Amplify builds only what is pushed. The script never commits or pushes.
- Chat still cannot answer on AWS while the Bedrock quotas are 0 (Part B, Step 2).
- There is no frontend teardown script: `terraform destroy` is allowed only in
  `destroy.sh`, and the frontend costs ~$0 idle. Removing it would be an explicit
  decision.
- Not yet exercised end to end in AWS (as of 2026-09-11).

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
| **Two target groups** | ALB pools, `ekba-dev-blue` and `ekba-dev-green` — they swap roles every deployment |
| **Production listener** (port 80) | Where real users arrive |
| **Test listener** (port 8080) | Where CodeDeploy routes the replacement before the shift. Not open to any client IP |

## The sequence

```
1. BEFORE
   users ──► :80 ──► [blue target group] ──► blue task (v1)
                     [green target group] ──► (empty)

2. GREEN STARTS  (Install)
   CodeDeploy asks ECS for a replacement task set and registers it in the green
   group. Real users are still entirely on blue.

   users ──► :80 ──► [blue]  ──► blue task (v1)     ← all real traffic
             :8080 ─► [blue]                         ← test listener, not moved yet
                      [green] ──► green task (v2)    ← starting; target "unused"

3. HEALTH CHECKS  ← the critical step
   What gates the shift today:
     · the containers' own health checks pass (api: curl /api/v1/health,
       postgres: pg_isready, redis: ping) and ECS reports the set stable
     · AllowTestTraffic points :8080 at green, and the ALB health check
       (GET /api/v1/health → 200, every 15 s, 2 healthy / 3 unhealthy) passes

   The AppSpec has no lifecycle hooks, so nothing else is tested before the
   shift — no database, Qdrant, Redis or RAG smoke query through :8080. Those
   checks are required by deployment.md but not yet built.

   If green never becomes healthy, the deployment fails and blue never moved.

4. TRAFFIC SHIFTS  (AllowTraffic)
   Only now does the ALB repoint the production listener.

   users ──► :80 ──► [green] ──► green task (v2)   ← real traffic
             blue task still alive, receiving nothing

5. ROLLBACK WINDOW (5 minutes by default)
   Blue is kept running. Auto-rollback is configured on deployment failure and
   on the ekba-dev-5xx alarm.

6. CLEANUP
   After the window, blue terminates. Green becomes the live group, and the next
   deployment goes into the (now empty) blue group.
```

## Why the test listener matters

It lets the replacement be health-checked through the real load balancer, on the
real network path, before a single user is moved. Today only the target-group
health check runs through it; lifecycle hooks that exercise the database, Qdrant,
Redis and a RAG query there are the planned next step.

## What rollback is (and is not)

Rollback is **one traffic shift back to blue**. That is all.

It does **not** destroy infrastructure, delete data, touch secrets, or run
`terraform destroy`. Automatic rollback is CodeDeploy's (configured, not yet
exercised); `scripts/rollback.sh` does not shift traffic yet — see Step 7. The
script already refuses to proceed when `MIGRATION_BACKWARD_COMPATIBLE=no` is set,
because rolling the app back under an incompatible schema can corrupt data; the
automatic migration check is still a placeholder.

## Why migrations must be backward compatible

During step 4, v1 and v2 are *both* running against the *same* database. So:

- ✅ Add a nullable column, add a table, add an index
- ❌ Drop a column v1 still reads, rename a column, change a type incompatibly

A destructive schema change is split across two releases: release A adds the new
shape and writes to both; release B removes the old one once nothing reads it.

(In the AWS demo each task has its own Postgres sidecar, so today the two versions
do not literally share a database. The rule is kept so the design holds once they
do.)

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
| `pytest tests/ -q` | All backend tests (190 at the last deploy gate) |
| `npx playwright test` | Frontend journeys |

## AWS

| Command | What it does | Safe? |
|---|---|---|
| `./scripts/bootstrap-state.sh` | One-time: S3 state bucket + lock table + `terraform init` (baseline, dev) | Creates protected resources only |
| `./scripts/set-secrets.sh` | One-time: fill the empty secrets | Never overwrites a value |
| `./scripts/cost-check.sh` | Gross / credits / net spend, what is running, kill-switch state | Read-only (Cost Explorer calls cost $0.01 each) |
| `./scripts/deploy.sh` | Full deploy pipeline (Step 5) | Never destroys |
| `./scripts/verify.sh` | Health of the deployed API from your machine; other checks are placeholders | Read-only |
| `./scripts/rollback.sh` | Placeholder: re-runs `verify.sh`, writes an incident report | Changes nothing |
| `./scripts/seed.sh` | Demo data — **local stack only** | Refuses prod |
| `./scripts/test-e2e.sh` | Full journeys — **local stack only** | Refuses prod |
| `./scripts/destroy.sh` | Tear down the `envs/dev` stack | **The only `terraform destroy`** |
| `aws lambda invoke --function-name ekba-dev-cost-guard …` | Test the kill switch (Step 4b) | Dry-run changes nothing |
| `./scripts/deploy-frontend.sh` | Amplify frontend + CloudFront API front door + backend wiring + verification (Part C) | Never destroys; never pushes |
| `./scripts/deploy-frontend.sh --plan-only` | The gate and both plans | Changes nothing |
| `./scripts/rollback-frontend.sh [--to <sha>]` | Rebuild an earlier frontend commit on Amplify | App-level only |

---

# Troubleshooting

## Local

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

## AWS — every one of these happened during the first deployment

| Symptom | Cause | Fix |
|---|---|---|
| `SubscriptionRequiredException` from every `aws deploy …` call, in every region | Account on the Free plan | Upgrade to the Paid plan (Step 0). IAM is not the cause |
| `verify.sh`: `FAIL health endpoint`, although CodeDeploy reported success | Your public IP is no longer `allowed_cidrs` | Compare `curl -s https://checkip.amazonaws.com` with the dev tfvars, update, `deploy.sh` (Step 5) |
| `curl` to the ALB: HTTP `000`, exit code 28 | Same — the security group drops the packets | Same |
| `ROLLBACK FAILED - ESCALATE TO A HUMAN` | `rollback.sh` does not shift traffic yet; its re-run of `verify.sh` failed for the original reason | Diagnose the original `verify.sh` failure. `rollback.sh` changed nothing |
| `seed failed - continuing without demo data`, `ThrottlingException` in `/ekba/dev/service` | Bedrock quota is 0 | Support case (Step 2). The API is unaffected |
| New target `unused` / `Target.NotInUse` mid-deploy | No listener points at that group yet | Normal until `AllowTestTraffic` |
| `DeploymentTargetDoesNotExistException` | ECS target ID format | `--target-id ekba-dev:ekba-dev` |
| `InvalidParameterException … logGroupName` in Git Bash | MSYS path conversion | `MSYS_NO_PATHCONV=1 aws logs …` |
| `Too many command line arguments` from Terraform in PowerShell | PowerShell 5.1 splits `-backend-config=backend.hcl` | Quote the argument, or use Git Bash |

**How to tell "the app is broken" from "you cannot reach it":**

```bash
# 1. Does the ALB consider the target healthy? (checks from inside the VPC)
ARN=$(aws elbv2 describe-target-groups --names ekba-dev-green --query 'TargetGroups[0].TargetGroupArn' --output text)
aws elbv2 describe-target-health --target-group-arn "$ARN"      # repeat for ekba-dev-blue

# 2. Are health requests arriving, and with what status? (10.42.x.x = the ALB)
MSYS_NO_PATHCONV=1 aws logs filter-log-events --log-group-name /ekba/dev/service \
  --log-stream-name-prefix api --filter-pattern '"health"' --max-items 5 \
  --query 'events[].message' --output text

# 3. Is your IP the allowed one?
curl -s https://checkip.amazonaws.com
aws ec2 describe-security-groups --filters Name=group-name,Values=ekba-dev-alb \
  --query 'SecurityGroups[0].IpPermissions[].IpRanges[].CidrIp'
```

Healthy targets and `200`s from `10.42.x.x`, with nothing from your address,
means the application is fine and the security group is dropping you.

## Other AWS

| Symptom | Cause | Fix |
|---|---|---|
| `deploy.sh` refuses to run | Gross usage since `COST_GUARD_START` reached `COST_GUARD_SHUTDOWN_USD` ($18) | `cost-check.sh`, then `destroy.sh` |
| `ValidationException … on-demand throughput isn't supported` | Nova invoked by bare model ID | Use the inference-profile ID, `us.amazon.nova-lite-v1:0` |
| Terraform "Wrong AWS account" | Wrong profile | Check `aws sts get-caller-identity` |

## Frontend (Part C) — what the script reports and what to do

| Message from `deploy-frontend.sh` | Meaning | Fix |
|---|---|---|
| Exit code 3, "ONE-TIME MANUAL STEP" | No Amplify app named `ekba-dev-frontend` yet | Push the build files, connect the repo in the console, run again |
| "the backend is not deployed" / "AWS reports 'None'" | No ALB (destroyed, or removed by an armed cost guard) | `./scripts/destroy.sh`, `./scripts/deploy.sh`, then run again |
| "amplify.yml is not on origin/aws-deployment yet" | Build files not pushed | Commit and push them |
| "is connected to '…', expected …" | An app with this name builds another repository | Not this project's app — resolve it in the console; the script will not touch it |
| Amplify build `FAILED` at `NEXT_PUBLIC_API_URL must be an https:// URL` | The app's environment variables are not set yet — expected for the console's first build | Run `./scripts/deploy-frontend.sh`; it sets them and rebuilds |
| "your public IP … is not in allowed_cidrs" | `deploy.sh`'s `verify.sh` would time out | Update `allowed_cidrs` in `envs/dev/terraform.tfvars` first |
| "API health through CloudFront" fails with 502/504 | CloudFront cannot reach the ALB — the security group rule is not applied | Run without `--no-backend`, or `./scripts/deploy.sh` |
| "CORS preflight … allowed" fails | The live task definition lacks the Amplify origin | Same: the wiring needs a backend deploy |

---

# Cost summary

| Activity | Cost |
|---|---|
| All local development | **$0** |
| Local with real Bedrock | ~$0.0002 per question |
| The AWS stack, per hour it exists | **~$0.09** (list-price estimate: Fargate 1 vCPU / 3 GB ~$0.054, ALB ~$0.023 + LCUs, 3 public IPv4 addresses ~$0.015) |
| One 4-hour AWS demo | **~$0.37** |
| The AWS stack forgotten for a month | ~$66 |
| Protected baseline, at rest | ~$1.70/month, mostly 4 secrets × $0.40 |
| Cost guard | ~$0 (free tiers) |
| Frontend on Amplify | ~$0.04 per build (≈4 build-minutes at $0.01), cents for storage and transfer, ~$0 idle |
| CloudFront API front door | ~$0 at demo traffic (always-free tier: 1 TB and 10M requests a month) |
| Building everything (phases 2, 3, 5) | ~$4 total (original estimate) |

**Actual so far:** $1.02 of gross usage since 2026-09-01 (the credit-guard budget,
2026-09-11), all covered by credits.

**Credits and the Paid plan.** The account is on the Paid plan with credits
remaining. Credits pay for usage first; anything they do not cover — or anything
after they run out or expire — is charged to the card. Budgets that include
credits (the AWS default, and the baseline `ekba-dev-monthly`) read $0 until that
moment, which is why the cost guard measures usage *before* credits.

**Guards in place:**

- **Cost guard** (`envs/cost-guard`, currently **dry-run**): email at $10 and $15
  of gross usage and on a forecast above $18; stops the stack at $18 of gross
  usage, on any charge credits did not cover, or after 8 hours
- `deploy.sh` refuses to deploy at $18 of gross usage
- `cost-check.sh` shows gross, credits and net spend, and alarms if a NAT Gateway
  or RDS instance ever appears (they never should)
- Baseline budget `ekba-dev-monthly`: $20 with 50/80/100% alerts — after credits
- Per-user daily AI ceiling in the app ($0.50)

**The single most important habit:** run `./scripts/destroy.sh` when a demo ends.
Until the cost guard is armed, nothing else stops the ~$0.09/hour.
