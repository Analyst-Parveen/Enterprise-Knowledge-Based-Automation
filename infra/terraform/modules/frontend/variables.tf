variable "project_code" {
  description = "Project prefix used in resource names."
  type        = string
}

variable "environment" {
  description = "Environment the frontend and API belong to."
  type        = string
}

variable "api_origin_domain" {
  description = "DNS name of the ALB CloudFront forwards to. Changes whenever envs/dev is recreated."
  type        = string
}

variable "repository_url" {
  description = "GitHub repository URL the Amplify app builds from."
  type        = string
}

variable "branch_name" {
  description = "Branch Amplify builds and deploys on every push."
  type        = string
}

variable "amplify_app_id" {
  description = "ID of the Amplify app connected in the console. Empty until it exists; Amplify resources are managed only once set."
  type        = string
}
