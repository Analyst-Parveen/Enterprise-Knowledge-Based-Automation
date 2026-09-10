# Terraform

Two separate states, deliberately.

## `envs/baseline/` — PROTECTED, never destroyed

ECR repository, S3 document bucket, Cognito user pool, Secrets Manager secrets,
the AWS Budget, and retained audit log groups.

Everything here is tagged `Lifecycle = "protected"`. `scripts/destroy.sh` does
not point at this state and cannot reach it. Apply it once, then leave it alone.

## `envs/dev/` — EPHEMERAL, destroyed after every demo

VPC, subnets, security groups, ALB, ECS cluster, task definition, service,
CodeDeploy application, and the task IAM roles.

Everything here is tagged `Lifecycle = "ephemeral"` and costs ~$0.072/hour while
it exists. `scripts/destroy.sh` targets this state only.

## Why two states

A single state would put secrets and ECR images one `terraform destroy` away
from deletion. Splitting them makes the safety rule structural rather than
procedural: the destroy workflow simply has no reference to the protected
resources.

## Order

```
1. terraform -chdir=envs/baseline init && apply     # once, ever
2. terraform -chdir=envs/dev init && apply          # per demo
3. scripts/destroy.sh                               # after the demo
4. go to 2
```
