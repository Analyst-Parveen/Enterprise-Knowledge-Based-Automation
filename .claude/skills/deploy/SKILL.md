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

## Step 2 — Local gate

Nothing deploys that has not passed locally first.

```bash
docker compose -f infra/docker/docker-compose.yml up -d
ruff check backend && ruff format --check backend
mypy backend
pytest backend/tests/unit backend/tests/integration backend/tests/security
```

All must pass. If a test fails, fix the code — never the test.

## Step 3 — Build, scan, push

```bash
docker build -t ekba-backend:$GIT_SHA -f infra/docker/backend.Dockerfile .
# push to ECR, then confirm the scan result
aws ecr describe-image-scan-findings --repository-name ekba-backend --image-id imageTag=$GIT_SHA
```

Critical findings block the deploy. Images are tagged with the git SHA, never
deployed from `latest`.

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

- Migrations first (backward compatible), then the new version.
- CodeDeploy blue-green; traffic shifts only after health checks pass.
- Keep the previous version available for the rollback window.

## Step 6 — Post-deploy (all of it)

```bash
./scripts/verify.sh      # infrastructure, services, AI pipeline
./scripts/seed.sh        # dashboards must not be empty
./scripts/test-e2e.sh    # real user journeys
```

If verification or E2E fails: run `./scripts/rollback.sh`, then report the
failure honestly. Do not describe a deploy as successful when verification failed.

## Step 7 — Report

Write a timestamped report to `docs/reports/` with the git SHA, environment,
account, region, what ran, pass/fail per stage, resources changed, and estimated
cost impact. No secret values in the report.

## Step 8 — Cost reminder

Remind the user that the environment is ephemeral by design and that
`./scripts/destroy.sh` should be run when the demo is finished, so nothing
expensive stays running.
