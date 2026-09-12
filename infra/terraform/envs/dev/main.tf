##############################################################################
# EPHEMERAL environment - created for a demo, destroyed afterwards.
#
# This state contains ONLY disposable resources. Secrets, ECR images, the S3
# bucket, the Cognito pool and the budget live in envs/baseline and are
# unreachable from here except as read-only data lookups.
#
# The RDS database lives here too, so it goes with the stack. Its data does
# not: scripts/destroy.sh snapshots it first and deploy.sh restores the newest
# snapshot (var.restore_snapshot_id).
#
# scripts/destroy.sh targets THIS state and only this state.
##############################################################################

terraform {
  # 1.11+: write-only arguments (the RDS password never enters state).
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 5.100 is the locked version; password_wo on aws_db_instance and the
      # ephemeral Secrets Manager read both need a recent 5.x.
      version = "~> 5.100"
    }
  }

  # Partial config: bucket/key/region come from backend.hcl, which
  # scripts/bootstrap-state.sh writes (git-ignored - it holds the account ID).
  #   terraform init -backend-config=backend.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "enterprise-knowledge-based-automation"
      ProjectCode = "ekba"
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = var.owner
      Lifecycle   = "ephemeral" # <- what makes destroy.sh safe to run
    }
  }
}

# Abort the plan if we are pointed at the wrong account.
data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.expected_aws_account_id
      error_message = "Wrong AWS account: ${self.account_id}, expected ${var.expected_aws_account_id}. STOPPING."
    }
  }
}

locals {
  name = "${var.project_code}-${var.environment}"
}

##############################################################################
# Read-only lookups into the protected baseline.
#
# These are DATA sources, not resources. Terraform can read them; it can never
# destroy them from this state.
##############################################################################
data "aws_s3_bucket" "documents" {
  bucket = var.s3_bucket
}

data "aws_cognito_user_pools" "main" {
  name = "${local.name}-users"
}

data "aws_secretsmanager_secret" "app" {
  for_each = toset([
    "backend/database-url",
    "backend/qdrant-api-key",
    "backend/dev-auth-secret",
    "ai/langsmith-api-key",
  ])

  name = "${var.project_code}/${var.environment}/${each.value}"
}

# The database password, read at plan/apply time and never persisted: an
# ephemeral resource is not written to state or to the saved plan. It is the
# same "password" key the Postgres sidecar used, so no new secret is needed.
ephemeral "aws_secretsmanager_secret_version" "database" {
  secret_id = data.aws_secretsmanager_secret.app["backend/database-url"].id
}

##############################################################################
# Ephemeral infrastructure
##############################################################################
module "network" {
  source = "../../modules/network"

  name                      = local.name
  vpc_cidr                  = var.vpc_cidr
  allowed_cidrs             = var.allowed_cidrs
  cloudfront_origin_ingress = var.cloudfront_origin_ingress
}

module "database" {
  source = "../../modules/database"

  name               = "${local.name}-postgres"
  private_subnet_ids = module.network.private_subnet_ids
  security_group_id  = module.network.db_security_group_id

  instance_class        = var.db_instance_class
  allocated_storage     = var.db_allocated_storage
  backup_retention_days = var.db_backup_retention_days
  restore_snapshot_id   = var.restore_snapshot_id

  master_password = jsondecode(ephemeral.aws_secretsmanager_secret_version.database.secret_string)["password"]
}

module "service" {
  source = "../../modules/service"

  name         = local.name
  project_code = var.project_code
  environment  = var.environment
  aws_region   = var.aws_region

  vpc_id                  = module.network.vpc_id
  public_subnet_ids       = module.network.public_subnet_ids
  alb_security_group_id   = module.network.alb_security_group_id
  tasks_security_group_id = module.network.tasks_security_group_id

  backend_image = var.backend_image
  task_cpu      = var.task_cpu
  task_memory   = var.task_memory
  desired_count = var.desired_count

  s3_bucket             = var.s3_bucket
  cognito_user_pool_arn = "arn:aws:cognito-idp:${var.aws_region}:${data.aws_caller_identity.current.account_id}:userpool/${tolist(data.aws_cognito_user_pools.main.ids)[0]}"

  secret_arns = [for s in data.aws_secretsmanager_secret.app : s.arn]

  database = {
    host            = module.database.address
    port            = module.database.port
    name            = module.database.db_name
    user            = module.database.username
    password_secret = "${data.aws_secretsmanager_secret.app["backend/database-url"].arn}:password::"
  }

  secret_environment = {
    DEV_AUTH_SECRET   = data.aws_secretsmanager_secret.app["backend/dev-auth-secret"].arn
    QDRANT_API_KEY    = data.aws_secretsmanager_secret.app["backend/qdrant-api-key"].arn
    LANGSMITH_API_KEY = data.aws_secretsmanager_secret.app["ai/langsmith-api-key"].arn
  }

  # Non-secret configuration only. Anything sensitive goes through
  # secret_environment above so it never enters Terraform state.
  environment_variables = {
    ENVIRONMENT  = var.environment
    PROJECT_CODE = var.project_code
    LOG_LEVEL    = "INFO"

    # Qdrant and Redis run as sidecars in the SAME ECS task, so they are
    # reachable on localhost - no ElastiCache, no service discovery needed.
    # PostgreSQL is RDS: DATABASE_URL is assembled in the container from the
    # DB_* settings and the DB_PASSWORD secret (see module.service).
    QDRANT_URL = "http://localhost:6333"
    REDIS_URL  = "redis://localhost:6379/0"

    S3_BUCKET  = var.s3_bucket
    AWS_REGION = var.aws_region

    COGNITO_USER_POOL_ID = tolist(data.aws_cognito_user_pools.main.ids)[0]
    COGNITO_CLIENT_ID    = var.cognito_client_id
    COGNITO_REGION       = var.aws_region

    # Bedrock only - both chat and embeddings.
    AI_PROVIDER                    = "bedrock"
    BEDROCK_REGION                 = var.bedrock_region
    BEDROCK_CHAT_PRIMARY_MODEL_ID  = var.bedrock_chat_primary_model_id
    BEDROCK_CHAT_FALLBACK_MODEL_ID = var.bedrock_chat_fallback_model_id
    BEDROCK_VISION_MODEL_ID        = var.bedrock_vision_model_id
    BEDROCK_EMBEDDING_MODEL_ID     = var.bedrock_embedding_model_id
    BEDROCK_EMBEDDING_DIMENSION    = tostring(var.bedrock_embedding_dimension)
    TRANSCRIBE_REGION              = var.aws_region

    # Dev auth is OFF on AWS. Cognito is the only accepted issuer.
    DEV_AUTH_ENABLED = "false"

    # The Amplify frontend's origin is appended by scripts/deploy-frontend.sh
    # (frontend.auto.tfvars), so every later deploy.sh keeps it.
    CORS_ALLOWED_ORIGINS = var.amplify_origin == "" ? var.cors_allowed_origins : "${var.cors_allowed_origins},${var.amplify_origin}"
    TRUSTED_HOSTS        = var.trusted_hosts

    DAILY_COST_CEILING_USD = tostring(var.daily_cost_ceiling_usd)
  }

  log_retention_days      = var.log_retention_days
  rollback_window_minutes = var.rollback_window_minutes
}
