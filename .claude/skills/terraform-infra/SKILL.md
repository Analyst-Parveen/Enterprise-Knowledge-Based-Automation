---
name: terraform-infra
description: Plan, apply, inspect, or destroy this project's AWS infrastructure with Terraform. Use for any infrastructure change, terraform plan/apply, drift investigation, module work, or when running destroy.sh. Enforces account/region/state ownership verification and strict destroy restrictions.
---

# Skill: Terraform / Infrastructure

Governed by [terraform.md](../../rules/terraform.md) and
[aws-infrastructure.md](../../rules/aws-infrastructure.md).

## Ownership test — apply this before every action

A resource belongs to this project only if **all three** are true:

1. It is in this project's Terraform state.
2. Its name carries the `ekba-<env>-` prefix.
3. It is tagged `ProjectCode=ekba`.

If any signal is missing or contradictory: **stop and ask the user.** Never modify
or delete a resource on a guess.

## Preflight — mandatory before any mutation

```bash
aws sts get-caller-identity                       # account ID must match expected
echo "$AWS_REGION"                                # region must match
cd infra/terraform/envs/$ENV
terraform workspace show                          # workspace must match environment
terraform state list                              # confirm this project's resources
```

Any mismatch stops the workflow.

## Standard change workflow

```bash
terraform fmt -check -recursive
terraform validate
tflint ; checkov -d .                 # security scan
terraform plan -out=tfplan
```

**Then read the plan carefully.** Specifically look for:

- Any `destroy` or `replace` — if unintended, stop and report before applying.
- Changes to resources outside this project's scope.
- Anything that raises standing monthly cost.

Only after the plan is reviewed:

```bash
terraform apply tfplan
```

Never `-auto-approve` outside CI. Never apply a plan you have not read.

## Destroy — restricted workflow

> `terraform destroy` is **forbidden** during development, testing, verification,
> deployment, and rollback. It runs only through `scripts/destroy.sh`.

When the user explicitly wants the ephemeral environment torn down:

```bash
./scripts/destroy.sh
```

The workflow must, in order:

1. Verify AWS account ID, region, workspace, and state backend.
2. Produce a **destroy plan** and print every resource that would be destroyed.
3. Show the user that list and require a typed confirmation phrase — not `y`.
4. Refuse to touch anything tagged `Lifecycle = "protected"`.
5. Preserve: Secrets Manager secrets, the Terraform state backend, the ECR
   repository and its images, budgets/alarms, and retained audit log groups.
6. Destroy only resources in this project's state.
7. Write an audit record of what was destroyed to `docs/reports/`.

If the destroy plan contains anything unexpected — a resource you do not
recognise, an untagged resource, anything protected — **abort and ask.**

## Drift

`terraform plan` is the drift detector and is safe to run any time. Report drift
to the user with the specific resources involved. Do not silently apply a fix to
drift you did not cause, and never "fix" drift by deleting a resource.

## Module and style requirements

- Reusable modules in `infra/terraform/modules/`, environment composition in
  `infra/terraform/envs/<env>/`.
- No hardcoded account IDs, ARNs, secrets, or regions in module bodies.
- Pinned Terraform and provider versions; lock file committed.
- Every variable documented; sensitive outputs marked `sensitive = true`.
- Mandatory tags applied through provider `default_tags`.

## Cost discipline

**Ceiling: $20 total.** Before adding any resource, state its cost at rest and
reject anything that does not fit.

**These resources must not appear in the plan at all** — if one does, it is a bug:
`aws_nat_gateway`, `aws_db_instance`, `aws_elasticache_*`, `aws_eks_*`,
`aws_efs_*`.

Postgres, Qdrant, and Redis are containers in the Fargate task, not managed
services. The task runs in a public subnet with `assign_public_ip = true` so it
reaches ECR without a NAT Gateway. Smallest viable sizing (1 vCPU / 2 GB) by
default. ALB ingress restricted to `DEMO_ALLOWED_CIDR`, never `0.0.0.0/0`.

Run `scripts/cost-check.sh` before and after every session.

## Never

- Never edit state by hand without an explicit, recorded human decision.
- Never commit `.tfstate`, `.terraform/`, or real `.tfvars`.
- Never run a destructive AWS CLI command as a substitute for Terraform.
- Never touch the state backend, secrets, or anything outside this project.
