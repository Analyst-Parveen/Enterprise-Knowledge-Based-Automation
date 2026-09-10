variable "name" {
  description = "Name prefix, e.g. ekba-dev."
  type        = string
}

variable "project_code" {
  type    = string
  default = "ekba"
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

# -- network ----------------------------------------------------------------
variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "alb_security_group_id" {
  type = string
}

variable "tasks_security_group_id" {
  type = string
}

# -- image and runtime ------------------------------------------------------
variable "backend_image" {
  description = "Full ECR image URI, pinned to a git SHA. Never :latest."
  type        = string

  validation {
    condition     = !endswith(var.backend_image, ":latest")
    error_message = "Refusing to deploy :latest. Pin the image to an immutable git SHA tag."
  }
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "health_check_path" {
  type    = string
  default = "/api/v1/health"
}

variable "task_cpu" {
  description = "Fargate CPU units. 1024 = 1 vCPU. Larger costs more per hour."
  type        = number
  default     = 1024

  validation {
    condition     = var.task_cpu <= 2048
    error_message = "Above 2048 CPU units this stops fitting a $20 budget. Override deliberately if you must."
  }
}

variable "task_memory" {
  description = "Fargate memory in MiB. Four containers share this."
  type        = number
  default     = 3072

  validation {
    condition     = var.task_memory <= 8192
    error_message = "Above 8192 MiB this stops fitting a $20 budget."
  }
}

variable "desired_count" {
  description = "Task count. One is correct for a demo."
  type        = number
  default     = 1

  validation {
    condition     = var.desired_count <= 2
    error_message = "More than 2 tasks multiplies the hourly burn for no demo benefit."
  }
}

# -- configuration ----------------------------------------------------------
variable "environment_variables" {
  description = "Plain (non-secret) environment variables for the container."
  type        = map(string)
  default     = {}
}

variable "secret_environment" {
  description = "Env var name -> Secrets Manager ARN. Values never touch Terraform state."
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "Every secret ARN the execution role may read."
  type        = list(string)
  default     = []
}

variable "postgres_password_secret_arn" {
  description = "Secrets Manager ARN holding the Postgres password."
  type        = string
}

variable "s3_bucket" {
  description = "Document bucket name (from the baseline state)."
  type        = string
}

variable "cognito_user_pool_arn" {
  description = "Cognito user pool ARN (from the baseline state)."
  type        = string
}

# -- operations -------------------------------------------------------------
variable "log_retention_days" {
  description = "Short for ephemeral environments - logs cost money."
  type        = number
  default     = 1
}

variable "rollback_window_minutes" {
  description = "How long the old (blue) task set stays alive after traffic shifts."
  type        = number
  default     = 5

  validation {
    condition     = var.rollback_window_minutes >= 1 && var.rollback_window_minutes <= 60
    error_message = "Keep the rollback window between 1 and 60 minutes; blue costs money while it lives."
  }
}

variable "spend_alarm_usd" {
  description = "Billing alarm threshold (us-east-1 only)."
  type        = number
  default     = 15
}
