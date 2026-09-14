variable "aws_region" {
  description = "Region of the Amplify app. CloudFront itself is global."
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
  description = "Environment whose API this frontend talks to."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging"], var.environment)
    error_message = "This frontend fronts an ephemeral demo environment only."
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

variable "api_origin_domain" {
  description = <<-EOT
    DNS name of the envs/dev ALB (no scheme). It changes every time envs/dev is
    recreated, so scripts/deploy-frontend.sh reads it from envs/dev on every run
    and passes it with -var. The CloudFront URL itself stays the same.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+\\.[a-z0-9-]+\\.elb\\.amazonaws\\.com$", var.api_origin_domain))
    error_message = "Must be a bare ALB DNS name such as ekba-dev-alb-123456789.us-west-2.elb.amazonaws.com (no http://)."
  }
}

variable "repository_url" {
  description = "GitHub repository the Amplify app builds from."
  type        = string

  validation {
    condition     = can(regex("^https://github\\.com/[^/]+/[^/]+$", var.repository_url))
    error_message = "Must be https://github.com/<owner>/<repo>."
  }
}

variable "branch_name" {
  description = "Branch Amplify builds and deploys on every push."
  type        = string
  default     = "aws-deployment"
}

variable "amplify_app_id" {
  description = "ID of the Amplify app connected in the console (d...). scripts/deploy-frontend.sh finds it by name; empty until the app exists."
  type        = string
  default     = ""

  validation {
    condition     = var.amplify_app_id == "" || can(regex("^d[a-z0-9]{6,20}$", var.amplify_app_id))
    error_message = "Must be empty or an Amplify app ID such as d1a2b3c4d5e6f7."
  }
}
