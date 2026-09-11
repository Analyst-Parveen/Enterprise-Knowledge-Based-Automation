variable "aws_region" {
  description = "Region of the environment being guarded."
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
  description = "The ephemeral environment this guard stops."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging"], var.environment)
    error_message = "The cost guard protects ephemeral environments only."
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

variable "alert_email" {
  description = "Receives every budget alert and every shutdown report."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "Must be an email address."
  }
}

variable "ceiling_usd" {
  description = "Project spend ceiling, gross of credits."
  type        = number
  default     = 20

  validation {
    condition     = var.ceiling_usd > 0 && var.ceiling_usd <= 50
    error_message = "This is a $20-ceiling project. A limit above $50 is almost certainly a mistake."
  }
}

variable "shutdown_usd" {
  description = <<-EOT
    Cumulative gross usage (credits excluded) at which the environment is
    stopped. Kept below the ceiling because billing data lags by up to ~24h;
    at ~$2.20/day of burn, $18 lands the shutdown at roughly $18-20.
  EOT
  type        = number
  default     = 18

  validation {
    condition     = var.shutdown_usd > 0 && var.shutdown_usd < var.ceiling_usd
    error_message = "shutdown_usd must be positive and below ceiling_usd, to leave room for billing-data lag."
  }
}

variable "warning_thresholds_usd" {
  description = "Cumulative gross usage levels that email a warning before shutdown."
  type        = list(number)
  default     = [10, 15]

  validation {
    condition     = length(var.warning_thresholds_usd) <= 3 && alltrue([for t in var.warning_thresholds_usd : t > 0 && t < var.shutdown_usd])
    error_message = "At most 3 warnings (Budgets allows 5 notifications per budget), each below shutdown_usd."
  }
}

variable "card_charge_threshold_usd" {
  description = "Net (after-credit) monthly spend treated as a card charge. Triggers shutdown."
  type        = number
  default     = 0.01

  validation {
    condition     = var.card_charge_threshold_usd > 0 && var.card_charge_threshold_usd <= 1
    error_message = "Keep this at a cent or so - any card charge should stop the environment."
  }
}

variable "tracking_start" {
  description = "Start of cumulative gross-usage tracking."
  type        = string
  default     = "2026-09-01_00:00"

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-01_00:00$", var.tracking_start))
    error_message = "Use the first day of a month, formatted YYYY-MM-01_00:00."
  }
}

variable "max_session_hours" {
  description = "Hours the environment may exist before the hourly check stops it."
  type        = number
  default     = 8

  validation {
    condition     = var.max_session_hours >= 1 && var.max_session_hours <= 24
    error_message = "Keep the session limit between 1 and 24 hours."
  }
}

variable "dry_run" {
  description = "When true the kill switch only reports what it would do. Turn off deliberately, after a reviewed dry-run test."
  type        = bool
  default     = true
}

variable "tfstate_bucket" {
  description = "S3 bucket holding Terraform state (bootstrapped separately)."
  type        = string
}

variable "log_retention_days" {
  description = "Retention for the kill switch's own logs - long enough to audit a shutdown."
  type        = number
  default     = 14
}
