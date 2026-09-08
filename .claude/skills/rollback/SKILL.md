---
name: rollback
description: Safely roll back the deployed application to the previous known-good version via CodeDeploy. Use when a deploy fails verification, a release is broken in the demo environment, or asked to roll back, revert the deployment, or run rollback.sh. Application-level only - never destroys infrastructure or data.
---

# Skill: Rollback

Governed by [deployment.md](../../rules/deployment.md) section 6.

## The single most important constraint

**Rollback is application-level only.**

- Never run `terraform destroy` during rollback. Not once, not "just this VPC",
  not to "clean up first".
- Never delete data: no S3 objects, no database, no snapshots, no Qdrant
  collections.
- Never delete or overwrite secrets.
- Never modify infrastructure outside the application deployment itself.

Rolling back means pointing traffic at the previous known-good image. Nothing more.

## Step 1 — Establish the situation before acting

```bash
aws sts get-caller-identity            # correct account
echo "$AWS_REGION"                     # correct region
aws deploy list-deployments --application-name ekba-$ENV --max-items 5
```

Determine and state clearly:

- What is currently deployed (image SHA) and what is the last known-good SHA?
- What failed — health check, verification, E2E, or a user-reported issue?
- Were database migrations applied in the failing release?

## Step 2 — Migration safety check

Migrations are written to be backward compatible so a rollback does not need a
down-migration.

If the failing release applied a **non-backward-compatible** migration, **stop and
ask the user.** Rolling the application back under an incompatible schema can
corrupt data. Do not improvise a down-migration.

## Step 3 — Roll back

```bash
./scripts/rollback.sh
```

The script shifts CodeDeploy traffic back to the previous known-good deployment
group revision. Confirm the target revision before it executes.

## Step 4 — Confirm recovery

```bash
# health checks
curl -fsS "$API_URL/health"
./scripts/verify.sh
```

Verify: API liveness/readiness, database, Qdrant, Redis, and one real RAG smoke
query returning a complete response envelope.

If rollback itself fails to restore health, **stop and escalate to the user with
the evidence.** Do not start destroying or rebuilding infrastructure on your own
initiative.

## Step 5 — Report

Write a timestamped incident report to `docs/reports/`:

- What was deployed, what broke, and the evidence
- The SHA rolled back from and to
- Migration status
- Post-rollback verification results
- Root cause if known, and the follow-up needed before redeploying

No secret values in the report.

## Step 6 — Do not immediately redeploy

Fix the root cause, get it passing locally and through the test gates, then run
the deploy workflow again. Redeploying the same broken build is not a remedy.
