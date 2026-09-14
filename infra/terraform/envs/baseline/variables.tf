variable "aws_region" {
  description = "AWS region for the baseline. Must match the ephemeral environment."
  type        = string
  default     = "us-west-2"
}

variable "expected_aws_account_id" {
  description = "The account this is allowed to apply into. A mismatch aborts the plan."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_aws_account_id))
    error_message = "Must be a 12-digit AWS account ID."
  }
}

variable "environment" {
  description = "Environment name."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "project_code" {
  description = "Short project prefix used for every resource name and the ownership tag."
  type        = string
  default     = "ekba"
}

variable "owner" {
  description = "Owner tag value."
  type        = string
  default     = "resume-project"
}

variable "budget_limit_usd" {
  description = "Hard monthly spend ceiling. The whole architecture is built around this."
  type        = number
  default     = 20

  validation {
    condition     = var.budget_limit_usd > 0 && var.budget_limit_usd <= 50
    error_message = "This is a $20-budget project. A limit above $50 is almost certainly a mistake."
  }
}

variable "budget_alert_email" {
  description = "Email address for budget alerts at 50/80/100%."
  type        = string
}

variable "github_repository" {
  description = "owner/repo allowed to assume the deploy role via OIDC."
  type        = string

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repository))
    error_message = "Must be in the form owner/repo."
  }
}

variable "github_repository_ids" {
  description = "Numeric \"<owner_id>/<repo_id>\" of github_repository. GitHub's ID-based OIDC subject (repo:owner@id/repo@id:...) needs them; null trusts only the name-based subject."
  type        = string
  default     = null

  validation {
    condition     = var.github_repository_ids == null || can(regex("^[0-9]+/[0-9]+$", var.github_repository_ids))
    error_message = "Must be in the form <owner_id>/<repo_id>, both numeric."
  }
}

variable "create_github_oidc" {
  description = "Create the GitHub OIDC provider. Set false if one already exists in the account."
  type        = bool
  default     = true
}

variable "tfstate_bucket" {
  description = "S3 bucket holding Terraform state (bootstrapped separately)."
  type        = string
}

variable "tfstate_lock_table" {
  description = "DynamoDB table used for state locking."
  type        = string
  default     = "ekba-tfstate-lock"
}

variable "cognito_callback_urls" {
  description = "Allowed OAuth callback URLs."
  type        = list(string)
  default     = ["http://localhost:3000/"]
}

variable "cognito_logout_urls" {
  description = "Allowed logout URLs."
  type        = list(string)
  default     = ["http://localhost:3000/"]
}
