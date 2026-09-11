##############################################################################
# FRONTEND - persistent, cheap at rest. Its own state, like cost-guard.
#
# The Amplify app and the CloudFront API front door must survive every destroy
# of envs/dev: the site URL and the API URL stay stable, and only
# api_origin_domain is updated when the ALB is recreated. scripts/destroy.sh
# does not reference this state.
#
# Drive it with ./scripts/deploy-frontend.sh, which supplies api_origin_domain
# and amplify_app_id. Order on first use:
#   1. no Amplify app yet          -> CloudFront only (the HTTPS API URL)
#   2. connect the repo in the Amplify console (GitHub App) - the one manual step
#   3. rerun: the app is found by name, the plan shows "will be imported", apply,
#      then the backend CORS/ingress wiring and an Amplify build
##############################################################################

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # Partial config: bucket/key/region come from backend.hcl (git-ignored - it
  # holds the account ID). key = "frontend/terraform.tfstate".
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
      Lifecycle   = "protected" # survives destroy.sh; not touched by the cost guard
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

module "frontend" {
  source = "../../modules/frontend"

  project_code      = var.project_code
  environment       = var.environment
  api_origin_domain = var.api_origin_domain
  repository_url    = var.repository_url
  branch_name       = var.branch_name
  amplify_app_id    = var.amplify_app_id
}

# The app and branch are created by the console's GitHub connection, then taken
# over here. These blocks do nothing until amplify_app_id is set, and the plan
# shows "will be imported" for review before anything is recorded in state.
import {
  for_each = var.amplify_app_id == "" ? toset([]) : toset([var.amplify_app_id])
  to       = module.frontend.aws_amplify_app.this[0]
  id       = each.value
}

import {
  for_each = var.amplify_app_id == "" ? toset([]) : toset(["${var.amplify_app_id}/${var.branch_name}"])
  to       = module.frontend.aws_amplify_branch.this[0]
  id       = each.value
}
