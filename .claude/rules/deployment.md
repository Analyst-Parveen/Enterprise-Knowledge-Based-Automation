# Rule: Deployment

## 1. The lifecycle

```
deploy -> test -> verify -> live demo -> destroy project resources -> audit -> deploy again
```

This loop must work repeatedly and identically. Reproducibility is a hard
requirement, and it is also the cost strategy: nothing expensive stays running
between demos.

Entry points are the scripts in `scripts/`. Nothing else drives a deployment.

| Script | Purpose |
|---|---|
| `deploy.sh` | Create/update infrastructure, deploy app, verify, test |
| `verify.sh` | Verify AWS infra, services, application, AI pipeline |
| `test-e2e.sh` | Full end-to-end tests |
| `seed.sh` | Seed development/demo data |
| `rollback.sh` | Safely roll back the application |
| `destroy.sh` | Destroy **only** this project's temporary infrastructure |

## 2. Preconditions for any deploy

1. Local Docker Compose stack passes lint, unit, integration, and security tests.
2. AWS account ID and region verified (see
   [aws-infrastructure.md](aws-infrastructure.md) section 1).
3. Terraform workspace and state verified.
4. Secrets exist in Secrets Manager; none are being created inline or committed.
5. Image built, scanned in ECR, and free of critical findings.

## 3. CI/CD pipeline

```
Push -> Test -> Security checks -> Build -> Docker -> Scan -> ECR
     -> Terraform validation -> Deploy -> Health checks
     -> E2E verification -> Rollback if needed
```

- GitHub Actions authenticates to AWS via **OIDC**. No stored AWS access keys.
- Images are tagged with the immutable git SHA. `latest` is never deployed from.
- Terraform runs `fmt`, `validate`, security scan, then `plan`. The plan is
  reviewed before apply.
- A pipeline stage that fails stops the pipeline. No `continue-on-error` on a
  security or test gate.

## 4. Release strategy

- AWS CodeDeploy performs blue-green deployment.
- Traffic shifts only after health checks pass on the new version.
- Health checks cover: API liveness/readiness, database connectivity, Qdrant
  connectivity, Redis connectivity, and one real RAG smoke query.
- The previous version stays available for the rollback window before termination.
- Database migrations are applied **before** the new version takes traffic and are
  written to be backward compatible with the running version, so a rollback does
  not require a down-migration.

## 5. Post-deploy sequence

`deploy.sh` is not finished until all of these have run:

```
1. Health checks              (is it up?)
2. verify.sh                  (is the infrastructure and AI pipeline correct?)
3. seed.sh                    (are the dashboards populated?)
4. test-e2e.sh                (do real user journeys work?)
5. report written to docs/reports/
```

If verification or E2E fails, `rollback.sh` runs. A deploy that "succeeded" but
failed verification is a failed deploy — report it as such.

## 6. Rollback

- `rollback.sh` returns the application to the previous known-good image via
  CodeDeploy.
- **Rollback is application-level only.** It never runs `terraform destroy` and
  never deletes data, buckets, secrets, or infrastructure.
- After rollback: re-run health checks and `verify.sh`, then record what happened
  in `docs/reports/`.

## 7. Destroy

`destroy.sh` is the only path that may run `terraform destroy`, and only for
resources owned by this project's Terraform state. See
[terraform.md](terraform.md) section 5 for the full requirements: account/region/
state verification, printed destroy plan, typed confirmation, protection of
`Lifecycle = "protected"` resources, preservation of secrets and state backend,
and an audit record.

**`terraform destroy` never runs during development, testing, verification,
deployment, or rollback.**

## 8. Reporting

Every lifecycle run writes a timestamped report to `docs/reports/` containing:
the git SHA, the environment, the AWS account and region, what ran, what passed
and failed, resources created or destroyed, and the estimated cost impact.
Reports never contain secret values.

## 9. Escalation

Stop and ask the user before:

- Deploying to an unverified account or region.
- Applying a Terraform plan that destroys or replaces something unexpected.
- Any manual out-of-band change to deployed infrastructure.
- Any action that would leave expensive resources running after a demo.
- Setting the cost guard's `dry_run` to `false`.

## 10. Implementation status (2026-09-11)

The requirements above stand. This is how far the implementation has got, so
nobody mistakes a requirement for a working feature:

| Requirement | Status |
|---|---|
| Blue-green via CodeDeploy, traffic shifts after health checks (§4) | **Working.** Two deployments succeeded. The gate is the ECS container health checks plus the ALB target-group check on `/api/v1/health` (liveness). |
| Health checks cover DB, Qdrant, Redis and a RAG smoke query (§4) | **Not built.** The AppSpec has no lifecycle hooks. `/api/v1/health/ready` checks Postgres, Redis and Qdrant but nothing calls it during a deploy. |
| Post-deploy `verify.sh` (§5) | **Partly.** Runs from the operator's machine: `/api/v1/health`, `/api/v1/health/ready` (which runs `SELECT 1` on RDS plus Redis and Qdrant pings), and read-only RDS posture checks (available, not public, encrypted, security group admits only the task SG). The AI-pipeline checks are still placeholders. It depends on the operator IP being in `allowed_cidrs`. |
| Post-deploy `seed.sh` and `test-e2e.sh` (§5) | **Deliberately not run by `deploy.sh`.** Both target the local stack; on AWS the task migrates and seeds itself at startup, because RDS sits in private subnets nothing outside the VPC can reach. |
| `rollback.sh` returns to the previous image via CodeDeploy (§6) | **Not built.** The script prints `CodeDeploy rollback not yet implemented`, re-runs `verify.sh`, and changes nothing. CodeDeploy auto-rollback (deployment failure, `ekba-dev-5xx` alarm) is configured but not yet exercised. |
| CI pipeline (§3) | `deploy.yml` exists and has **not been run**. Its cost-guard step reads spend after credits and lacks `ce:GetCostAndUsage`; its health check cannot reach an ALB restricted to one operator IP; and its OIDC role (`DeployEphemeral`) grants no `rds:*` and no `secretsmanager:GetSecretValue`, both of which the database now needs. Widening that role is a protected-baseline change and has not been made. |
| Account prerequisite | The account must be on the **Paid** plan. On the Free plan every CodeDeploy call returns `SubscriptionRequiredException`. |
| Cost enforcement | `deploy.sh` refuses at $18 gross usage. The cost guard is applied **in dry-run**. |
| Transport ([security.md](security.md) §3: HTTPS everywhere) | **Partly.** The ALB itself still serves only HTTP (`:80`, `:8080`). Browser traffic from the Amplify frontend goes through CloudFront over HTTPS (`envs/frontend`), and CloudFront reaches the ALB over HTTP. Implemented, not applied yet. |
| Frontend deployment | `scripts/deploy-frontend.sh` (Amplify Hosting + CloudFront + backend wiring + verification) and `scripts/rollback-frontend.sh`. **Applied and live.** The one-time Amplify↔GitHub connection is a manual console step. |
| Database (RDS) | **Working.** `ekba-dev-postgres` replaced the Postgres sidecar on 2026-09-11. Data survives task replacement and deploys. The snapshot-on-destroy / restore-on-deploy path is implemented in `destroy.sh` and `deploy.sh` but has **not been exercised end to end** — no destroy has run since. Its failure mode is safe: if the snapshot cannot be taken, `destroy.sh` aborts before destroying anything. |
| Vector/cache persistence | **Not built, by design.** Qdrant and Redis remain task-local, so vectors are lost on every task replacement while PostgreSQL rows survive. Documents uploaded on AWS stop being retrievable until re-ingested; only seed documents are re-indexed at startup. |
