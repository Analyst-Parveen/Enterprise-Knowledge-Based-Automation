variable "name" {
  description = "Name prefix for the DB instance and subnet group, e.g. ekba-dev-postgres."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets (two AZs) for the DB subnet group."
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "A DB subnet group needs subnets in at least two availability zones."
  }
}

variable "security_group_id" {
  description = "Security group that admits PostgreSQL from the backend tasks only."
  type        = string
}

variable "engine_version" {
  description = "PostgreSQL major version. 16 matches the local Docker Compose image."
  type        = string
  default     = "16"
}

variable "instance_class" {
  description = "RDS instance class. db.t4g.micro is the cheapest that runs PostgreSQL 16."
  type        = string
  default     = "db.t4g.micro"

  validation {
    condition     = can(regex("^db\\.t4g\\.(micro|small)$", var.instance_class))
    error_message = "Only db.t4g.micro or db.t4g.small fit the $20 ceiling. A bigger class needs an explicit, justified change."
  }
}

variable "allocated_storage" {
  description = "Storage in GB. 20 is the gp3 minimum."
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Database created on first launch. Must match the app's DATABASE_URL."
  type        = string
  default     = "ekba"
}

variable "username" {
  description = "Master username. Must match the app's DATABASE_URL."
  type        = string
  default     = "ekba"
}

variable "master_password" {
  description = "Master password, read from Secrets Manager. Write-only: never stored in state."
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "master_password_version" {
  description = "Bump to push a changed master password to RDS (write-only arguments are otherwise not re-sent)."
  type        = number
  default     = 1
}

variable "backup_retention_days" {
  description = "Automated backup retention. 1 day stays inside the free backup allowance."
  type        = number
  default     = 1
}

variable "restore_snapshot_id" {
  description = "Manual snapshot to build a NEW instance from. Empty = empty database. Ignored while the instance exists."
  type        = string
  default     = ""
}
