output "api_url" {
  description = "HTTPS API URL (CloudFront -> ALB). The frontend's NEXT_PUBLIC_API_URL."
  value       = module.frontend.api_url
}

output "site_url" {
  description = "Amplify URL of the deployed branch - add it to cors_allowed_origins in envs/dev."
  value       = module.frontend.site_url
}

output "api_distribution_id" {
  description = "CloudFront distribution in front of the ALB."
  value       = module.frontend.api_distribution_id
}
