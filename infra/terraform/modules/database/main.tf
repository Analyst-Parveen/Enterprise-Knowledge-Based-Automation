##############################################################################
# Database - one small, private RDS PostgreSQL instance.
#
# Replaces the Postgres sidecar, whose data died with every ECS task. The
# instance lives in the ephemeral dev stack, so it survives task replacement
# and every CodeDeploy release, and it is removed by scripts/destroy.sh like
# the rest of the stack. destroy.sh first takes a manual snapshot (which
# Terraform does not own, so it survives), and deploy.sh restores the newest
# snapshot the next time the instance is created - see restore_snapshot_id.
#
# Cost (us-west-2, on-demand, single-AZ): db.t4g.micro ~$0.016/hour plus
# 20 GB gp3 ~$2.30/month, billed only while the stack exists. Snapshots
# between sessions: ~$0.095/GB-month of snapshot data (a few cents here).
#
# The master password is the "password" key of the existing Secrets Manager
# secret ekba/<env>/backend/database-url. It reaches RDS as a write-only
# argument, so it never enters Terraform state or plan output.
##############################################################################

resource "aws_db_subnet_group" "main" {
  name        = var.name
  description = "Private subnets for ${var.name} PostgreSQL"
  subnet_ids  = var.private_subnet_ids

  tags = { Name = var.name }
}

resource "aws_db_instance" "main" {
  #checkov:skip=CKV_AWS_157:Single-AZ on purpose - a demo database inside a $20 ceiling
  #checkov:skip=CKV_AWS_293:No deletion protection - destroy.sh removes it after taking a snapshot
  #checkov:skip=CKV_AWS_118:No enhanced monitoring - billed per metric
  #checkov:skip=CKV_AWS_353:No Performance Insights - not needed for a demo database
  #checkov:skip=CKV_AWS_161:Password auth from Secrets Manager; the app does not use IAM DB auth
  #checkov:skip=CKV_AWS_129:No log exports - CloudWatch ingestion cost
  #checkov:skip=CKV2_AWS_30:No query logging - CloudWatch ingestion cost
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  # First creation builds an empty database; after a destroy it is rebuilt
  # from the newest snapshot. Both are ignored afterwards (see lifecycle), so
  # a newer snapshot never forces a replacement of a running instance.
  snapshot_identifier = var.restore_snapshot_id == "" ? null : var.restore_snapshot_id
  db_name             = var.restore_snapshot_id == "" ? var.db_name : null

  username            = var.username
  password_wo         = var.master_password
  password_wo_version = var.master_password_version

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = false
  multi_az               = false
  port                   = 5432

  backup_retention_period  = var.backup_retention_days
  delete_automated_backups = true
  copy_tags_to_snapshot    = true

  # destroy.sh takes a named manual snapshot before destroying, and refuses to
  # continue if it cannot - so Terraform's own final snapshot is not needed.
  skip_final_snapshot = true
  deletion_protection = false

  auto_minor_version_upgrade   = true
  apply_immediately            = true
  performance_insights_enabled = false
  monitoring_interval          = 0

  lifecycle {
    # engine_version: the config pins the MAJOR version; AWS owns the minor
    # (auto_minor_version_upgrade), so the running 16.x must not look like drift.
    ignore_changes = [snapshot_identifier, db_name, engine_version]
  }
}
