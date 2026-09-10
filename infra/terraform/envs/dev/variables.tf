variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "expected_aws_account_id" {
  description = "A mismatch aborts the plan before anything is created."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_aws_account_id))
    error_message = "Must be a 12-digit AWS account ID."
  }
}

variable "environment" {
  type    = string
  default = "dev"

  validation {
    # This composition is for ephemeral demos only.
    condition     = contains(["dev", "staging"], var.environment)
    error_message = "This ephemeral stack is not intended for prod."
  }
}

variable "project_code" {
  type    = string
  default = "ekba"
}

variable "owner" {
  type    = string
  default = "resume-project"
}

# -- network ----------------------------------------------------------------
variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "allowed_cidrs" {
  description = "CIDRs allowed to reach the demo ALB. Use your own IP: curl -s https://checkip.amazonaws.com"
  type        = list(string)
}

# -- image ------------------------------------------------------------------
variable "backend_image" {
  description = "ECR image URI pinned to a git SHA."
  type        = string
}

# -- compute (kept small on purpose) ----------------------------------------
variable "task_cpu" {
  type    = number
  default = 1024
}

variable "task_memory" {
  type    = number
  default = 3072
}

variable "desired_count" {
  type    = number
  default = 1
}

# -- baseline references ----------------------------------------------------
variable "s3_bucket" {
  description = "Document bucket from the baseline output."
  type        = string
}

variable "cognito_client_id" {
  description = "Cognito app client ID from the baseline output."
  type        = string
}

# -- AI ---------------------------------------------------------------------
variable "bedrock_region" {
  description = "Region where the chosen Bedrock models are actually available."
  type        = string
  default     = "us-west-2"
}

variable "bedrock_chat_primary_model_id" {
  description = <<-EOT
    Chat model. Amazon Nova requires an INFERENCE PROFILE, so the ID carries a
    region-group prefix (us./eu./apac./global.). A bare "amazon.nova-lite-v1:0"
    returns ValidationException. GPT-4 is not on Bedrock at all.
  EOT
  type        = string
  default     = "us.amazon.nova-lite-v1:0"
}

variable "bedrock_chat_fallback_model_id" {
  description = "Cheaper model used when the primary is unavailable. Text-only is fine here."
  type        = string
  default     = "us.amazon.nova-micro-v1:0"
}

variable "bedrock_vision_model_id" {
  description = "Must support IMAGE input. nova-micro and titan-embed are text-only."
  type        = string
  default     = "us.amazon.nova-lite-v1:0"

  validation {
    # nova-micro and titan-embed cannot accept images; the prefix may be
    # us./eu./apac./global., so match on the suffix rather than the start.
    condition     = !can(regex("(gpt-oss|nova-micro|titan-embed)", var.bedrock_vision_model_id))
    error_message = "That model is text-only and cannot accept images. Use a vision-capable model such as us.amazon.nova-lite-v1:0."
  }
}

variable "bedrock_embedding_model_id" {
  description = "Dedicated embedding model. Never a chat or vision model."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"

  validation {
    condition     = can(regex("embed", var.bedrock_embedding_model_id))
    error_message = "This must be an embedding model. A chat model is never used for embeddings."
  }
}

variable "bedrock_embedding_dimension" {
  description = "Must match the Qdrant collection. Changing it requires a new collection."
  type        = number
  default     = 1024
}

# -- security ---------------------------------------------------------------
variable "cors_allowed_origins" {
  description = "Comma-separated allow-list. Never a wildcard."
  type        = string
  default     = "http://localhost:3000"

  validation {
    condition     = !can(regex("\\*", var.cors_allowed_origins))
    error_message = "CORS must be an explicit allow-list, never a wildcard."
  }
}

variable "trusted_hosts" {
  description = "Host allow-list. Browser traffic reaches the API with the ALB DNS name as Host."
  type        = string
  default     = "localhost,127.0.0.1,*.elb.amazonaws.com"
}

variable "daily_cost_ceiling_usd" {
  description = "Per-user daily AI spend ceiling enforced in the application."
  type        = number
  default     = 0.50
}

# -- operations -------------------------------------------------------------
variable "log_retention_days" {
  type    = number
  default = 1
}

variable "rollback_window_minutes" {
  type    = number
  default = 5
}
