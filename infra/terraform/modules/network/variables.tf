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

variable "cloudfront_origin_ingress" {
  description = <<-EOT
    Also admit HTTP on port 80 from CloudFront's origin-facing IP ranges, so the
    CloudFront distribution in envs/frontend can reach the ALB. This makes the API
    reachable through CloudFront from anywhere - every endpoint but /health still
    needs a valid Cognito token. The managed prefix list counts as ~46 of the
    security group's 60 inbound rules.
  EOT
  type        = bool
  default     = false
}
