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
backend, secrets, ECR repository with images, log groups retained for audit).

## 3. State

- Remote state in S3 with versioning enabled, plus DynamoDB state locking.
- The state backend itself is **bootstrapped separately and is protected**. It is
  never destroyed by `destroy.sh`.
- One state per environment: `infra/terraform/envs/<env>`.
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

- **No `aws_nat_gateway`, `aws_db_instance`, `aws_elasticache_*`, `aws_eks_*`, or
  `aws_efs_*` resources exist anywhere in this codebase.** If one appears in a
  plan, that is a bug — stop and remove it.
- Fargate task runs in a **public subnet with `assign_public_ip = true`** so it
  reaches ECR without a NAT Gateway.
- Postgres, Qdrant, and Redis are **containers in the task definition**, not
  managed services. No persistent volumes.
- Default sizing is the smallest that works (1 vCPU / 2 GB). A larger size needs
  an explicit override with a comment justifying the cost.
- `aws_budgets_budget` with a $20 limit and 50/80/100% alerts is part of the
  protected baseline.
- CloudWatch log retention is explicit and short (1 day for ephemeral).
- The ALB security group ingress is restricted to `DEMO_ALLOWED_CIDR`, not
  `0.0.0.0/0` — this limits both exposure and traffic-driven cost.
- Every new resource must have its cost at rest stated in the change description.

See [aws-infrastructure.md](aws-infrastructure.md) section 5 for the full
exclusion table.
