variable "project_code" {
  description = "Project prefix used in resource names and the ProjectCode tag."
  type        = string
}

variable "environment" {
  description = "The ephemeral environment this guard protects."
  type        = string
}

variable "alert_email" {
  description = "Receives every budget alert and every shutdown report."
  type        = string
}

variable "ceiling_usd" {
  description = "Project spend ceiling in USD, gross of credits. The credit-guard budget limit."
  type        = number
}

variable "shutdown_usd" {
  description = "Cumulative gross usage (credits excluded) at which the environment is stopped."
  type        = number
}

variable "warning_thresholds_usd" {
  description = "Cumulative gross usage levels that send an email warning before shutdown."
  type        = list(number)
}

variable "card_charge_threshold_usd" {
  description = "Net (after-credit) spend in a month that counts as a card charge and triggers shutdown."
  type        = number
}

variable "tracking_start" {
  description = "Start of cumulative tracking, format YYYY-MM-01_00:00."
  type        = string
}

variable "max_session_hours" {
  description = "Hours the environment may exist before the hourly check stops it."
  type        = number
}

variable "dry_run" {
  description = "When true the Lambda reports what it would do and changes nothing."
  type        = bool
}

variable "tfstate_bucket" {
  description = "Terraform state bucket. The Lambda reads the environment state from it to prove ownership."
  type        = string
}

variable "environment_state_key" {
  description = "Object key of the protected environment's Terraform state."
  type        = string
}

variable "log_retention_days" {
  description = "Retention for the Lambda's log group."
  type        = number
}
