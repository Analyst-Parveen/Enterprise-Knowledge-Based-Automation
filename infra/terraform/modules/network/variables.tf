variable "name" {
  description = "Name prefix for every resource, e.g. ekba-dev."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block."
  type        = string
  default     = "10.42.0.0/16"
}

variable "allowed_cidrs" {
  description = "CIDRs allowed to reach the ALB. Never set this to 0.0.0.0/0 for a demo."
  type        = list(string)

  validation {
    condition     = !contains(var.allowed_cidrs, "0.0.0.0/0")
    error_message = "Refusing to expose the demo ALB to the entire internet. Set DEMO_ALLOWED_CIDR to your own IP."
  }
}
