##############################################################################
# COST GUARD - PROTECTED, always on. A third state, deliberately.
#
# The kill switch must be armed whenever the ephemeral environment exists, so it
# cannot live in envs/dev (destroy.sh would remove it). It is kept out of
# envs/baseline so adding it changes nothing in the protected baseline.
#
# scripts/destroy.sh does not reference this state. See infra/terraform/README.md.
##############################################################################

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Partial config: bucket/key/region come from backend.hcl (git-ignored - it
  # holds the account ID).
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
      Lifecycle   = "protected"
    }
  }
}

# Refuse to run in the wrong account.
data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.expected_aws_account_id
      error_message = "Wrong AWS account: ${self.account_id}, expected ${var.expected_aws_account_id}. STOPPING."
    }
  }
}

module "cost_guard" {
  source = "../../modules/cost-guard"

  project_code = var.project_code
  environment  = var.environment
  alert_email  = var.alert_email

  ceiling_usd               = var.ceiling_usd
  shutdown_usd              = var.shutdown_usd
  warning_thresholds_usd    = var.warning_thresholds_usd
  card_charge_threshold_usd = var.card_charge_threshold_usd
  tracking_start            = var.tracking_start
  max_session_hours         = var.max_session_hours
  dry_run                   = var.dry_run

  tfstate_bucket        = var.tfstate_bucket
  environment_state_key = "${var.environment}/terraform.tfstate"
  log_retention_days    = var.log_retention_days
}
