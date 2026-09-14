# RDS Migration — Postgres sidecar to Amazon RDS

**Date:** 2026-09-12 · **Environment:** dev · **Region:** us-west-2
**Branch:** aws-deployment

## What changed and why

PostgreSQL on AWS used to be a `postgres:16-alpine` **sidecar container** inside
the ECS task, with no volume: every task replacement — and therefore every
deploy — started from an empty database, and the task re-created the schema and
demo data at startup. Relational data could not survive a release.

It is now **Amazon RDS PostgreSQL 16** (`ekba-dev-postgres`), so tenants, users,
documents, ingestion jobs, conversations, usage and audit rows survive task
replacement and deploys. The database instance still belongs to the ephemeral
`dev` stack, so it costs nothing between sessions; its **data** crosses sessions
as a manual snapshot that `destroy.sh` takes and `deploy.sh` restores.

This was an explicitly approved exception to the project rule that forbade RDS
(`.claude/rules/terraform.md` §7, `aws-infrastructure.md` §5); those rules have
been rewritten to permit exactly one small instance and to forbid everything
larger.

## What was created

| Resource | Detail |
|---|---|
| `aws_db_instance.main` | `ekba-dev-postgres` — PostgreSQL 16.13, `db.t4g.micro`, 20 GB gp3, single-AZ, encrypted at rest, `publicly_accessible = false`, backups 1 day, `auto_minor_version_upgrade = true` |
| `aws_db_subnet_group.main` | `ekba-dev-postgres`, across the two new private subnets |
| `aws_subnet.private[0..1]` | `10.42.10.0/24` (us-west-2a), `10.42.11.0/24` (us-west-2b) |
| `aws_route_table.private` + 2 associations | No internet route at all — local VPC routing only |
| `aws_security_group.db` | `ekba-dev-db`: ingress 5432 **only** from `ekba-dev-tasks`; no CIDR rule, no egress rule |

New Terraform module: `infra/terraform/modules/database`. Nothing existing was
destroyed or replaced except the ECS task definition, which gets a new revision
on every deploy by design.

## How the application connects

The master password is the `password` key of the existing Secrets Manager secret
`ekba/dev/backend/database-url`. Terraform reads it through an **ephemeral**
`aws_secretsmanager_secret_version` and passes it to the **write-only**
`password_wo` argument, so it appears in neither the state file nor the plan
(verified: the plan renders `password_wo = (write-only attribute)`, and a search
of the plan output for the password value returned 0 matches).

The API container receives `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` as plain
configuration and `DB_PASSWORD` from Secrets Manager, then assembles
`DATABASE_URL` itself at startup and connects with `ssl=require`. No connection
string containing a password exists in Terraform, the task definition or the
image. The old `url` key of that secret is now unused; no secret value was
overwritten or deleted.

## Migrations

Unchanged mechanism: the container runs `alembic upgrade head` at startup, from
the existing `0001_initial_schema` migration — no schema was hand-written or
guessed. On the first task against RDS the log shows:

```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, Initial schema - the 10 entities from PROJECT.md section 14.
```

It is a no-op on every later start, and the seed upserts by stable IDs, so a
persistent database is never duplicated.

## Verification

Done in two deploys, so the sidecar was only removed once RDS was proven to work.

**Phase A — RDS added, API switched to it, sidecar still present** (deployment `d-4V95QF2WK`):

- `alembic upgrade head` ran against RDS and created the schema:
  `Running upgrade  -> 0001, Initial schema`.
- CodeDeploy blue/green **Succeeded**; all eight lifecycle events succeeded.
- `verify.sh`: **5 checks passed** —
  `readiness: PostgreSQL + Redis + Qdrant` (a real `SELECT 1` through the ALB),
  `RDS ekba-dev-postgres is available`,
  `RDS is private (not publicly accessible) and encrypted at rest`,
  `RDS security group admits only the backend task security group`,
  plus the liveness check.
- `/api/v1/health/ready` through CloudFront: `postgres: healthy`.

**Phase B — Postgres sidecar removed** (deployment `d-9QKEOC3WK`):

- The task now runs exactly three containers — `api`, `qdrant`, `redis` — task
  `healthStatus: HEALTHY`, and CloudWatch has **no `postgres` log stream** for it.
- All eight lifecycle events succeeded and traffic shifted.
- `/api/v1/health/ready` through CloudFront: `postgres: healthy`.

**Data survives task replacement.** A read-only one-off ECS task counted rows
before and after the phase-B replacement (it connects from the task security
group; nothing else can reach the database):

| | Before (04:07 UTC) | After (04:15 UTC) |
|---|---|---|
| `alembic_version` | 0001 | 0001 |
| tenants / users | 2 / 4 | 2 / 4 |
| documents / ingestion_jobs | 12 / 12 | 12 / 12 |
| conversations / messages | 8 / 16 | 8 / 16 |
| audit_events | 9 | 9 |
| oldest tenant `created_at` | 2026-09-12 03:58:09 UTC | 2026-09-12 03:58:09 UTC |

That timestamp was written by the **first** RDS task; it is unchanged after a
full task replacement, and the phase-B task ran **no** migration (`alembic`
printed no `Running upgrade` line), which is only possible if the schema and data
were already there.

**Cost guard** (`ekba-dev-cost-guard`, still `DRY_RUN=true`) — manual dry-run
invocation after the change:

```
WOULD scale ECS service ekba-dev/ekba-dev from 1 to 0 tasks
WOULD delete load balancer ekba-dev-alb (and its listeners)
WOULD stop RDS instance ekba-dev-postgres (status available; data and storage kept)
refused: []   errors: []
```

Its IAM policy gained `rds:DescribeDBInstances` and `rds:StopDBInstance` on
`db:ekba-dev-*`, conditioned on the ephemeral tags. There is no `rds:Delete*`.
39 Lambda unit tests pass (27 before, 12 new), and the 190 backend tests passed
in the deploy gate.
## Cost

| Item | Cost |
|---|---|
| `db.t4g.micro`, single-AZ | ~$0.016 / hour (~$11.68 / month) |
| 20 GB gp3 storage | ~$2.30 / month (~$0.003 / hour) |
| Automated backups, 1 day | $0 (within the allocated-storage allowance) |
| Manual snapshots between sessions | ~$0.095 per GB of snapshot data per month — cents here |
| **While the stack runs** | **~$0.019 / hour**, taking the whole stack from ~$0.09 to **~$0.11 / hour** (~$0.44 per 4-hour session) |
| **If left running 24/7** | **~$14 / month** — it must not be |

Gross usage since 2026-09-01 was **$2.86** at the time of this change, against the
$18 shutdown threshold.

## Rules and documentation updated

`.claude/rules/terraform.md` (§7 now permits exactly one `aws_db_instance` and
pins its shape), `.claude/rules/aws-infrastructure.md` (§4 network, §5 cost and
persistence), `.claude/rules/deployment.md` (§10 status), the `deploy`,
`terraform-infra` and `verify` skills, `CLAUDE.md`, `README.md`, `PROJECT.md`,
`RUNBOOK.md` (new "Step 8b — The database"), `infra/terraform/README.md`,
`.env.example`, `docker-compose.yml` header, the admin Deployments page
(burn rate), and the Hinglish handbook (`docs/handbook/`, new section 5.17 plus
architecture, cost and troubleshooting updates).

## Known gaps, honestly

1. **Qdrant and Redis are still task-local.** Vectors are lost on every task
   replacement while PostgreSQL rows survive, so a document uploaded on AWS keeps
   its row and S3 file but stops being retrievable until it is uploaded again.
   Only seed documents are re-indexed at startup. A re-index job is not built.
2. **The snapshot/restore path has not been exercised end to end.** `destroy.sh`
   takes a snapshot and `deploy.sh` restores the newest one, but no destroy has
   run since the change. The failure mode is safe: no snapshot, no destroy.
3. **`deploy.yml` still cannot run.** Its OIDC role has no `rds:*` and no
   `secretsmanager:GetSecretValue`, which Terraform now needs. Widening it is a
   protected-baseline change and was not made.
4. **Bedrock quotas are still 0**, so the startup seed fails at the embedding step
   exactly as before. The API is unaffected.
5. **Blue and green now share one database.** Migrations must stay backward
   compatible; this was already the rule, and is now literally enforced by
   reality.
