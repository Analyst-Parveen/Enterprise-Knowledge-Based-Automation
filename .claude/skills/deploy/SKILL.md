---
name: deploy
description: Deploy the Enterprise Knowledge-Based Automation platform to AWS. Use when asked to deploy, ship, release, push to AWS, or run deploy.sh. Enforces preflight account/region/state verification, local-first testing, blue-green release, and post-deploy verification.
---

# Skill: Deployment

Governed by [deployment.md](../../rules/deployment.md),
[aws-infrastructure.md](../../rules/aws-infrastructure.md), and
[terraform.md](../../rules/terraform.md).

## Never, during this workflow

- Never run `terraform destroy`. Deployment never destroys.
- Never `terraform apply -auto-approve` outside a reviewed plan file.
- Never create, overwrite, or delete secrets.
- Never touch a resource outside this project's Terraform state.
- Never skip verification to "finish faster". An unverified deploy is a failed deploy.

## Step 1 — Preflight (stop on any failure)

```bash
aws sts get-caller-identity                 # account ID must match expected
echo "$AWS_REGION"                          # region must match project region
terraform -chdir=infra/terraform/envs/$ENV workspace show
terraform -chdir=infra/terraform/envs/$ENV state list | head
git status --porcelain                      # working tree should be clean
git rev-parse HEAD                          # record the SHA for the report
```

If the account, region, workspace, or state does not match expectations —
**stop and ask the user.** Do not guess.

Two AWS preconditions learned the hard way:

```bash
aws freetier get-account-plan-state --region us-east-1   # must be PAID - the Free plan
                                                          # rejects CodeDeploy (SubscriptionRequiredException)
curl -s https://checkip.amazonaws.com                    # must match allowed_cidrs in
grep allowed_cidrs infra/terraform/envs/dev/terraform.tfvars   # envs/dev/terraform.tfvars
```

If the operator IP differs from `allowed_cidrs`, the deploy itself succeeds but
`verify.sh` cannot reach the ALB and the run is reported as failed. Tell the user,
update `allowed_cidrs` (a one-resource, in-place security-group change), then deploy.

In practice all of Steps 1–6 are `./scripts/deploy.sh`; the steps below are what
it does and what to check.

## Step 2 — Local gate

Nothing deploys that has not passed locally first.

`deploy.sh` runs, from `backend/` with the project venv:

```bash
ruff check app tests seeds && ruff format --check app tests seeds
ENVIRONMENT=dev AI_PROVIDER=local DEV_AUTH_ENABLED=true \
  pytest tests/unit tests/integration tests/security tests/evaluation -q   # 190 on 2026-09-11
(cd ../frontend && npm run typecheck)
```

It also refuses to run past the cost guard: gross usage (credits excluded) since
`COST_GUARD_START` must be below `COST_GUARD_SHUTDOWN_USD` ($18).

All must pass. If a test fails, fix the code — never the test.

## Step 3 — Build, scan, push

```bash
docker build -t ekba-dev-backend:$GIT_SHA -f infra/docker/backend.Dockerfile .
# push to ECR, then confirm the scan result
aws ecr describe-image-scan-findings --repository-name ekba-dev-backend --image-id imageTag=$GIT_SHA
```

Critical findings block the deploy. Images are tagged with the git SHA, never
deployed from `latest`. Tags are immutable: `deploy.sh` refuses to build when
`backend/` or the Dockerfile has uncommitted changes, and reuses the image when
ECR already holds that SHA.

## Step 4 — Infrastructure

```bash
cd infra/terraform/envs/$ENV
terraform fmt -check -recursive
terraform validate
terraform plan -out=tfplan
```

**Read the plan.** If it shows a destroy or replace you did not intend, stop and
report it to the user before applying. Then, and only then:

```bash
terraform apply tfplan
```

## Step 5 — Release

- Migrations first (backward compatible), then the new version. On AWS the API
  container runs `alembic upgrade head` and the seed at task start.
- CodeDeploy blue-green; traffic shifts only after health checks pass. Every
  `deploy.sh` run creates a full blue-green deployment (about 9 minutes,
  including the 5-minute rollback window), even when nothing changed.
- Keep the previous version available for the rollback window.

Follow it with:

```bash
aws deploy get-deployment --deployment-id "$ID" --query 'deploymentInfo.status'
aws deploy get-deployment-target --deployment-id "$ID" --target-id ekba-dev:ekba-dev \
  --query 'deploymentTarget.ecsTarget.lifecycleEvents[].[lifecycleEventName,status]' --output text
```

## Step 6 — Post-deploy

```bash
./scripts/verify.sh      # run by deploy.sh against the ALB URL
```

`seed.sh` and `test-e2e.sh` target the **local** stack. Do not run them as
post-deploy checks for AWS — they would test the wrong system. On AWS the task
seeds itself; with Bedrock quotas at 0 that seed fails and the API runs without
demo data, which is expected, not a deploy failure.

If verification fails, **diagnose before rolling back or changing code.** A
`verify.sh` health failure after a successful CodeDeploy stage is most often the
operator IP, not the application. Check, in order: the operator IP vs
`allowed_cidrs`; ALB target health (`describe-target-health`); the API log
(`/ekba/dev/service`, stream prefix `api`) for `/api/v1/health` 200s from
`10.42.x.x`. `deploy.sh` calls `rollback.sh` on a verify failure, but that script
does not shift traffic yet — it only re-runs `verify.sh`. Report the failure
honestly. Do not describe a deploy as successful when verification failed.

## Step 7 — Report

Write a timestamped report to `docs/reports/` with the git SHA, environment,
account, region, what ran, pass/fail per stage, resources changed, and estimated
cost impact. No secret values in the report.

## Step 8 — Cost reminder

Remind the user that the environment is ephemeral by design, burns ~$0.09/hour,
and that `./scripts/destroy.sh` should be run when the demo is finished, so
nothing expensive stays running. State whether the cost guard is armed
(`./scripts/cost-check.sh`) — while it is in dry-run, nothing stops the stack
automatically.
