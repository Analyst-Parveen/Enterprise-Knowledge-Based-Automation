##############################################################################
# EPHEMERAL environment - created for a demo, destroyed afterwards.
#
# This state contains ONLY disposable resources. Secrets, ECR images, the S3
# bucket, the Cognito pool and the budget live in envs/baseline and are
# unreachable from here except as read-only data lookups.
#
# scripts/destroy.sh targets THIS state and only this state.
##############################################################################

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
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

##############################################################################
# Ephemeral infrastructure
##############################################################################
module "network" {
  source = "../../modules/network"

  name          = local.name
  vpc_cidr      = var.vpc_cidr
  allowed_cidrs = var.allowed_cidrs
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

  # ":password::" selects one key out of the JSON secret. Postgres wants only
  # the password; the app wants the whole URL (below). Both come from the SAME
  # secret, so they cannot drift apart the way two separate values would.
  postgres_password_secret_arn = "${data.aws_secretsmanager_secret.app["backend/database-url"].arn}:password::"
  secret_arns                  = [for s in data.aws_secretsmanager_secret.app : s.arn]

  secret_environment = {
    # The connection string carries a password, so it is a SECRET - it must
    # never sit in environment_variables where it would land in Terraform state.
    DATABASE_URL      = "${data.aws_secretsmanager_secret.app["backend/database-url"].arn}:url::"
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

    # Data services run as sidecars in the SAME ECS task, so they are reachable
    # on localhost - no RDS, no ElastiCache, no service discovery needed.
    # DATABASE_URL is injected from Secrets Manager instead (see below), because
    # it embeds a password.
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

    CORS_ALLOWED_ORIGINS = var.cors_allowed_origins
    TRUSTED_HOSTS        = var.trusted_hosts

    DAILY_COST_CEILING_USD = tostring(var.daily_cost_ceiling_usd)
  }

  log_retention_days      = var.log_retention_days
  rollback_window_minutes = var.rollback_window_minutes
}
