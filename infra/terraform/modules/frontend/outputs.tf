output "api_url" {
  description = "HTTPS URL of the API. Baked into the frontend as NEXT_PUBLIC_API_URL."
  value       = local.api_url
}

output "api_distribution_id" {
  description = "CloudFront distribution in front of the ALB."
  value       = aws_cloudfront_distribution.api.id
}

output "site_url" {
  description = "Amplify URL of the deployed branch. Add it to the backend CORS allow-list."
  value       = local.site_origin
}
