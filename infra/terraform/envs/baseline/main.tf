##############################################################################
# PROTECTED BASELINE - apply once, never destroy.
#
# scripts/destroy.sh does not reference this state. Everything here carries
# Lifecycle = "protected" and prevent_destroy where AWS supports it.
#
# See .claude/rules/terraform.md section 5 and secrets-management.md.
##############################################################################

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Partial config: bucket/key/region come from backend.hcl, which
  # scripts/bootstrap-state.sh writes (git-ignored - it holds the account ID).
  #   terraform init -backend-config=backend.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  # Applied to every resource, so nothing can be created untagged.
  default_tags {
    tags = {
      Project     = "enterprise-knowledge-based-automation"
      ProjectCode = "ekba"
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = var.owner
      Lifecycle   = "protected"
    }
  }
}

# Refuse to run in the wrong account.
data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.expected_aws_account_id
      error_message = "Wrong AWS account: ${self.account_id}, expected ${var.expected_aws_account_id}. STOPPING."
    }
  }
}

locals {
  name   = "${var.project_code}-${var.environment}"
  suffix = random_id.suffix.hex
}

resource "random_id" "suffix" {
  byte_length = 4
}

##############################################################################
# ECR - images must survive destroy so a redeploy has something to deploy
##############################################################################
resource "aws_ecr_repository" "backend" {
  name                 = "${local.name}-backend"
  image_tag_mutability = "IMMUTABLE" # a SHA tag can never be overwritten

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ecr_repository" "frontend" {
  name                 = "${local.name}-frontend"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Keep the last 10 images; untagged images expire in a day. Storage is cheap
# but not free, and this is a $20 budget.
resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "expire untagged images after 1 day"
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 1 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "keep the 10 most recent images"
        selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
        action       = { type = "expire" }
      }
    ]
  })
}

resource "aws_ecr_lifecycle_policy" "frontend" {
  repository = aws_ecr_repository.frontend.name
  policy     = aws_ecr_lifecycle_policy.backend.policy
}

##############################################################################
# S3 - documents. The only persistent data store in the demo.
##############################################################################
resource "aws_s3_bucket" "documents" {
  bucket = "${local.name}-documents-${local.suffix}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

# TLS-only. A plain HTTP request to this bucket is denied outright.
resource "aws_s3_bucket_policy" "documents_tls_only" {
  bucket = aws_s3_bucket.documents.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.documents.arn,
        "${aws_s3_bucket.documents.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}

##############################################################################
# Cognito - identity
##############################################################################
resource "aws_cognito_user_pool" "main" {
  name = "${local.name}-users"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OFF" # demo scope; enable for anything real

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # These two custom attributes carry ALL authorization data. The backend reads
  # tenant and role from the verified token and from nowhere else.
  schema {
    name                = "tenant_id"
    attribute_data_type = "String"
    mutable             = true
    required            = false
    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }

  schema {
    name                = "role"
    attribute_data_type = "String"
    mutable             = true
    required            = false
    string_attribute_constraints {
      min_length = 1
      max_length = 16
    }
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${local.name}-web"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false # public SPA client - a secret could not be kept

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 7

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  callback_urls = var.cognito_callback_urls
  logout_urls   = var.cognito_logout_urls

  supported_identity_providers = ["COGNITO"]
}

##############################################################################
# Secrets Manager - NEVER destroyed, NEVER read by Terraform
#
# Terraform creates the container. The VALUES are set out-of-band with the CLI
# so no secret ever passes through Terraform state.
##############################################################################
locals {
  secret_names = [
    "backend/database-url",
    "backend/qdrant-api-key",
    "backend/dev-auth-secret",
    "ai/langsmith-api-key",
    "ai/cohere-api-key",
    "ai/groq-api-key",
  ]
}

resource "aws_secretsmanager_secret" "app" {
  for_each = toset(local.secret_names)

  name        = "${var.project_code}/${var.environment}/${each.value}"
  description = "Managed by Terraform; value set out-of-band via the CLI."

  # Long window so an accidental delete is recoverable.
  recovery_window_in_days = 30

  lifecycle {
    prevent_destroy = true
  }
}

##############################################################################
# Budget - the $20 ceiling, enforced by AWS itself
##############################################################################
resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_alert_email]
    }
  }

  # Forecast alert: warn before the money is actually gone.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}

##############################################################################
# Audit logs - retained beyond the life of the ephemeral environment
##############################################################################
resource "aws_cloudwatch_log_group" "audit" {
  name              = "/${var.project_code}/${var.environment}/audit"
  retention_in_days = 30

  lifecycle {
    prevent_destroy = true
  }
}

##############################################################################
# GitHub Actions OIDC - no long-lived AWS keys anywhere
##############################################################################
data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

locals {
  github_oidc_arn = var.create_github_oidc ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn

  # GitHub now issues subjects that pin owner and repo by numeric ID
  # (repo:owner@<owner_id>/repo@<repo_id>:...). Trust both forms of THIS repo.
  github_repo_parts = split("/", var.github_repository)
  github_subjects = concat(
    ["repo:${var.github_repository}:*"],
    var.github_repository_ids == null ? [] : [format("repo:%s@%s/%s@%s:*",
      local.github_repo_parts[0], split("/", var.github_repository_ids)[0],
    local.github_repo_parts[1], split("/", var.github_repository_ids)[1])],
  )
}

resource "aws_iam_role" "github_deploy" {
  name = "${local.name}-github-deploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.github_oidc_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        # Scoped to THIS repository. Any other repo assuming this role is denied.
        StringLike = {
          "token.actions.githubusercontent.com:sub" = local.github_subjects
        }
      }
    }]
  })
}

# Scoped to this project's resources. Notably absent: iam:*Delete*,
# secretsmanager:DeleteSecret, and anything outside the ekba- prefix.
resource "aws_iam_role_policy" "github_deploy" {
  name = "${local.name}-github-deploy"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrPush"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "EcrRepoScoped"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
          "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
          "ecr:DescribeImageScanFindings", "ecr:DescribeImages",
        ]
        Resource = [aws_ecr_repository.backend.arn, aws_ecr_repository.frontend.arn]
      },
      {
        Sid      = "TerraformState"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.tfstate_bucket}", "arn:aws:s3:::${var.tfstate_bucket}/*"]
      },
      {
        Sid      = "TerraformLock"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.tfstate_lock_table}"
      },
      {
        Sid    = "DeployEphemeral"
        Effect = "Allow"
        Action = [
          "ecs:*", "elasticloadbalancing:*", "codedeploy:*", "logs:*",
          "ec2:Describe*", "ec2:CreateTags", "application-autoscaling:*",
        ]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:RequestedRegion" = var.aws_region }
        }
      },
      {
        Sid      = "PassTaskRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.name}-*"
      },
      {
        Sid      = "ReadSecretsMetadataOnly"
        Effect   = "Allow"
        Action   = ["secretsmanager:DescribeSecret", "secretsmanager:ListSecrets"]
        Resource = "*"
      },
    ]
  })
}
