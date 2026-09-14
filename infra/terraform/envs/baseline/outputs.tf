output "ecr_backend_url" {
  description = "ECR repository URL for the backend image."
  value       = aws_ecr_repository.backend.repository_url
}

output "ecr_frontend_url" {
  description = "ECR repository URL for the frontend image."
  value       = aws_ecr_repository.frontend.repository_url
}

output "s3_bucket" {
  description = "Document bucket name. Set this as S3_BUCKET in .env."
  value       = aws_s3_bucket.documents.id
}

output "cognito_user_pool_id" {
  description = "Set this as COGNITO_USER_POOL_ID in .env."
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "Set this as COGNITO_CLIENT_ID in .env."
  value       = aws_cognito_user_pool_client.web.id
}

output "cognito_issuer" {
  description = "Expected JWT issuer. The backend verifies tokens against this."
  value       = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

output "secret_arns" {
  description = "Secret containers created. Values are set out-of-band, never by Terraform."
  value       = { for k, v in aws_secretsmanager_secret.app : k => v.arn }
}

output "github_deploy_role_arn" {
  description = "Set this as the AWS_DEPLOY_ROLE_ARN secret in GitHub."
  value       = aws_iam_role.github_deploy.arn
}

output "audit_log_group" {
  description = "Retained audit log group."
  value       = aws_cloudwatch_log_group.audit.name
}

output "next_steps" {
  description = "What to do after this applies."
  value       = <<-EOT
    Baseline created. This state is PROTECTED and must never be destroyed.

    1. Set the secret values out-of-band (they never pass through Terraform):
         aws secretsmanager put-secret-value \
           --secret-id ${var.project_code}/${var.environment}/backend/database-url \
           --secret-string '<value>'

    2. Copy the outputs above into your .env.

    3. Add AWS_DEPLOY_ROLE_ARN to your GitHub repository secrets.

    4. Deploy the ephemeral stack:
         terraform -chdir=../dev init && terraform -chdir=../dev apply
  EOT
}
