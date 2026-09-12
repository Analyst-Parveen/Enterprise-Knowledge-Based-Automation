# Rule: Terraform

## 1. Ownership model

This project owns **only** resources created by its own Terraform state. Anything
else in the AWS account belongs to someone else and is out of scope, permanently.

Ownership is established by three signals, all of which must agree:

1. The resource is present in this project's Terraform state.
2. The resource name carries the project prefix: `ekba-<env>-`.
3. The resource carries the mandatory tags below.

If any signal is missing or ambiguous, **stop and ask**. Do not modify or delete.

## 2. Mandatory tags

Set via `default_tags` on the AWS provider so no resource can omit them:

```hcl
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "enterprise-knowledge-based-automation"
      ProjectCode = "ekba"
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = var.owner
      Lifecycle   = "ephemeral"   # or "protected"
    }
  }
}
```

`Lifecycle = "protected"` marks resources that survive `destroy.sh` (state
backend, secrets, ECR repository with images, log groups retained for audit,
budgets, and the whole cost guard).

## 3. State

- Remote state in S3 with versioning enabled, plus DynamoDB state locking.
- The state backend itself is **bootstrapped separately and is protected**. It is
  never destroyed by `destroy.sh`.
- One state per environment: `infra/terraform/envs/<env>`. Two further states
  are protected and never destroyed: `envs/baseline` (ECR, S3, Cognito, secrets,
  budget, audit logs, OIDC role) and `envs/cost-guard` (the kill switch, which
  must outlive every destroy of `envs/dev`). A fourth, `envs/frontend` (Amplify
  and the CloudFront API front door), is persistent and applied only through
  `scripts/deploy-frontend.sh`. `destroy.sh` targets `envs/<env>` only. See
  [infra/terraform/README.md](../../infra/terraform/README.md).
- Never edit state by hand. `terraform state rm`, `import`, or `taint` require an
  explicit human decision recorded in the change.
- Never commit `.tfstate`, `.tfstate.backup`, `.terraform/`, or `*.tfvars`
  containing real values.

## 4. Change workflow

Every infrastructure change follows this order without exception:

```
1. terraform fmt -check -recursive
2. terraform validate
3. tflint / checkov (security scan)
4. terraform plan -out=tfplan          <- REVIEW THE PLAN
5. inspect plan for destroys and replacements
6. terraform apply tfplan              <- only the reviewed plan file
```

- Never `terraform apply -auto-approve` outside CI, and in CI only against a
  plan file produced in the same run.
- If a plan shows a **destroy or replace** of anything you did not intend to
  change, stop. Do not apply. Investigate first.
- Never target-apply (`-target`) to work around a broken plan except as a
  deliberate, explained recovery step.

## 5. Destroy restrictions

> **`terraform destroy` is forbidden during normal development, testing,
> verification, deployment, and rollback.**

It is permitted only through `scripts/destroy.sh`, and only for resources owned by
this project's Terraform state, in the verified account and region.

`destroy.sh` must:

1. Verify the AWS account ID against the expected value.
2. Verify the region.
3. Verify the Terraform workspace and state backend.
4. Produce a **destroy plan** and print every resource to be destroyed.
5. Require explicit interactive confirmation (typed phrase, not `y`).
6. Refuse to touch anything tagged `Lifecycle = "protected"`.
7. Never delete secrets, ECR repositories containing images needed for redeploy,
   or the state backend.
8. Write an audit record of what was destroyed into `docs/reports/`.

Anything not in this project's state is out of scope for destroy, always.

## 6. Structure and style

- Reusable building blocks in `infra/terraform/modules/`.
- Environment composition in `infra/terraform/envs/<env>/`.
- No hardcoded account IDs, ARNs, secrets, or region strings in module bodies.
  Use variables and data sources.
- Pin the Terraform version and provider versions. Commit the lock file.
- Every variable has a `description` and, where sensible, a `validation` block.
- Every output that carries a secret is marked `sensitive = true`.
- Prefer data sources over hardcoded lookups of existing resources, and never
  create a resource that shadows something already in the account.

## 7. Cost guardrails in code — $20 ceiling

The Terraform must make overspending structurally difficult, not merely
discouraged.

- **No `aws_nat_gateway`, `aws_elasticache_*`, `aws_eks_*`, or `aws_efs_*`
  resources exist anywhere in this codebase.** If one appears in a plan, that is
  a bug — stop and remove it.
- **Exactly one `aws_db_instance`** exists: `modules/database`, used by
  `envs/dev` as `ekba-<env>-postgres` (approved 2026-09-11 to replace the
  Postgres sidecar). It must stay: `db.t4g.micro` (the variable validation
  allows `db.t4g.small` at most), single-AZ, 20 GB gp3, `publicly_accessible =
  false`, private subnets, a security group that admits only the task security
  group, storage encrypted, and a **write-only** master password
  (`password_wo`) read ephemerally from Secrets Manager — never a password in
  state. A second instance, Multi-AZ, a larger class or public access is a bug.
- The database's data outlives `destroy.sh` only as a **manual snapshot**:
  `destroy.sh` takes it before destroying and aborts if it cannot, and
  `deploy.sh` passes the newest one as `restore_snapshot_id`. The instance
  ignores later changes to that variable, so a new snapshot never replaces a
  running database.
- Fargate task runs in a **public subnet with `assign_public_ip = true`** so it
  reaches ECR without a NAT Gateway. The private subnets have no internet route
  and hold only RDS.
- Qdrant and Redis are **containers in the task definition**, not managed
  services. No persistent volumes.
- Default sizing is the smallest that works: 1 vCPU / 3 GB (`task_memory =
  3072`), because three containers share the task. A larger size needs an
  explicit override with a comment justifying the cost.
- `aws_budgets_budget` with a $20 limit and 50/80/100% alerts is part of the
  protected baseline. The cost-guard budgets exclude credits
  (`cost_types { include_credit = false }`) — a budget that includes credits
  reads $0 while they last.
- CloudWatch log retention is explicit and short (1 day for ephemeral).
- The ALB security group ingress is restricted to the `allowed_cidrs` variable
  (validated to never contain `0.0.0.0/0`) — this limits both exposure and
  traffic-driven cost. Local deploys set it in `envs/dev/terraform.tfvars`; the
  GitHub deploy workflow sets it from the `DEMO_ALLOWED_CIDR` repository
  variable. It is a single operator IP, so it must be updated when that IP
  changes.
- Every new resource must have its cost at rest stated in the change description.

See [aws-infrastructure.md](aws-infrastructure.md) section 5 for the full
exclusion table.
