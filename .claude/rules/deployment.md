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
