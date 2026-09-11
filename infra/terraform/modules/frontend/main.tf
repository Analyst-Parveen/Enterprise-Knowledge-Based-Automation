##############################################################################
# FRONTEND - AWS Amplify Hosting for the static Next.js export, plus an HTTPS
# front door for the API.
#
# Why CloudFront: Amplify serves the site over HTTPS, and a browser will not let
# an HTTPS page call the plain-HTTP ALB (mixed content). Amplify's own reverse
# proxy only supports HTTPS targets. A CloudFront distribution with its default
# *.cloudfront.net certificate gives the API an HTTPS URL without a domain name,
# and its address stays stable when the ephemeral ALB is recreated - only
# api_origin_domain changes.
#
# The Amplify app is connected to GitHub once in the console (the AWS Amplify
# GitHub App), then imported here. No GitHub token ever passes through Terraform
# state. Until amplify_app_id is set, only the CloudFront resources exist.
#
# Cost at rest: ~$0. CloudFront and Amplify bill per request, build minute and
# GB served; a demo stays within cents.
##############################################################################

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

locals {
  name        = "${var.project_code}-${var.environment}"
  api_url     = "https://${aws_cloudfront_distribution.api.domain_name}"
  manage_app  = var.amplify_app_id != ""
  site_origin = local.manage_app ? "https://${var.branch_name}.${aws_amplify_app.this[0].default_domain}" : null
}

##############################################################################
# API front door
##############################################################################

# Nothing is cached (default TTL 0, and the API sends no Cache-Control). The
# Authorization header has to be in the cache key: that is the only way
# CloudFront forwards it to the origin on GET requests. Keying on it also means
# a response could never be served to a different user.
resource "aws_cloudfront_cache_policy" "api" {
  name        = "${local.name}-api-no-cache"
  comment     = "API passthrough: no caching, Authorization forwarded"
  min_ttl     = 0
  default_ttl = 0
  max_ttl     = 1 # a non-zero max is required to put a header in the cache key

  parameters_in_cache_key_and_forwarded_to_origin {
    headers_config {
      header_behavior = "whitelist"
      headers {
        items = ["Authorization"]
      }
    }
    cookies_config {
      cookie_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "all"
    }
  }
}

# Forwards every viewer header except Host, so the ALB sees its own DNS name as
# the Host - which the API's TRUSTED_HOSTS (*.elb.amazonaws.com) accepts.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "api" {
  enabled         = true
  comment         = "${local.name} API - HTTPS in front of the demo ALB"
  price_class     = "PriceClass_100"
  is_ipv6_enabled = true

  origin {
    domain_name = var.api_origin_domain
    origin_id   = "alb"

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only" # the ALB has no HTTPS listener
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60 # RAG and agent requests can be slow
      origin_keepalive_timeout = 5
    }
  }

  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = aws_cloudfront_cache_policy.api.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    compress                 = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "${local.name}-api" }
}

##############################################################################
# Amplify Hosting
##############################################################################
resource "aws_amplify_app" "this" {
  count = local.manage_app ? 1 : 0

  name       = "${local.name}-frontend"
  repository = var.repository_url
  platform   = "WEB" # static hosting - the export has no server-side routes

  environment_variables = {
    AMPLIFY_MONOREPO_APP_ROOT = "frontend" # must match appRoot in amplify.yml
    NEXT_PUBLIC_API_URL       = local.api_url
    NEXT_TELEMETRY_DISABLED   = "1"
  }

  # The export writes one HTML file per route (chat.html, ...), and Amplify
  # already serves /chat from chat.html. A catch-all rewrite to index.html -
  # the usual SPA rule - would render the landing page at every URL, so the
  # only rule is a real 404 for unknown paths.
  custom_rule {
    source = "/<*>"
    status = "404"
    target = "/404.html"
  }

  # The static export cannot send headers itself. script-src and style-src
  # need 'unsafe-inline' for the inline bootstrap scripts Next.js emits.
  custom_headers = yamlencode({
    customHeaders = [{
      pattern = "**/*"
      headers = [
        { key = "Strict-Transport-Security", value = "max-age=31536000; includeSubDomains" },
        { key = "X-Content-Type-Options", value = "nosniff" },
        { key = "X-Frame-Options", value = "DENY" },
        { key = "Referrer-Policy", value = "no-referrer" },
        {
          key = "Content-Security-Policy"
          value = join("; ", [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self' data:",
            "connect-src 'self' ${local.api_url}",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
          ])
        },
      ]
    }]
  })

  # amplify.yml in the repository is the build spec; the copy the console
  # stores at connect time is not managed here.
  lifecycle {
    ignore_changes = [build_spec]
  }
}

resource "aws_amplify_branch" "this" {
  count = local.manage_app ? 1 : 0

  app_id            = aws_amplify_app.this[0].id
  branch_name       = var.branch_name
  framework         = "Next.js - SSG"
  enable_auto_build = true # every push to the branch builds and deploys
}
