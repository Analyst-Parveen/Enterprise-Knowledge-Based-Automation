##############################################################################
# COST GUARD - an always-on kill switch for the ephemeral environment.
#
# Two AWS Budgets feed one SNS topic that invokes a Lambda:
#
#   credit-guard       gross usage, credits EXCLUDED, cumulative. Warns at the
#                      warning thresholds, shuts down at shutdown_usd.
#   card-charge-guard  net usage, credits INCLUDED. Any charge credits do not
#                      cover means the card is being billed: shut down at once.
#
# An hourly EventBridge rule invokes the same Lambda to enforce the maximum
# demo session length.
#
# The Lambda scales ECS services to zero, deletes load balancers and stops the
# RDS instance (never deletes it - the data stays). Nothing else, and only for
# resources that pass all three ownership signals. IAM enforces the same
# boundary independently of the code.
#
# Why not native Budget Actions: they can only apply IAM/SCP policies or stop
# EC2/RDS instances. They cannot stop Fargate or delete a load balancer.
#
# Cost at rest: ~$0/month. Notification-only budgets are free, and SNS, Lambda
# and EventBridge usage (~730 invocations/month) sits inside the free tiers.
##############################################################################

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    archive = {
      source = "hashicorp/archive"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name       = "${var.project_code}-${var.environment}"
  guard      = "${local.name}-cost-guard"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # The Lambda may only mutate resources carrying all three of these tags.
  ephemeral_tags = {
    "aws:ResourceTag/ProjectCode" = var.project_code
    "aws:ResourceTag/Environment" = var.environment
    "aws:ResourceTag/Lifecycle"   = "ephemeral"
  }

  ecs_service_arns = [
    "arn:aws:ecs:${local.region}:${local.account_id}:service/${local.name}/*",
    "arn:aws:ecs:${local.region}:${local.account_id}:service/${local.name}-*/*",
  ]

  db_instance_arn = "arn:aws:rds:${local.region}:${local.account_id}:db:${local.name}-*"
}

##############################################################################
# SNS - one topic that REQUESTS a shutdown, one that REPORTS what happened.
# Kept separate so the Lambda's own report can never re-trigger it.
#
# No SSE: Budgets cannot publish to a topic encrypted with the AWS-managed
# key, and a customer-managed KMS key costs $1/month for budget notices.
##############################################################################
resource "aws_sns_topic" "trigger" {
  name = "${local.guard}-trigger"
}

# Only AWS Budgets, acting for this account, may publish a shutdown request.
resource "aws_sns_topic_policy" "trigger" {
  arn = aws_sns_topic.trigger.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowBudgetsFromThisAccount"
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "SNS:Publish"
      Resource  = aws_sns_topic.trigger.arn
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:budgets::${local.account_id}:*" }
      }
    }]
  })
}

resource "aws_sns_topic" "notify" {
  name = "${local.guard}-notify"
}

# Stays "pending confirmation" until the link in the confirmation email is clicked.
resource "aws_sns_topic_subscription" "notify_email" {
  topic_arn = aws_sns_topic.notify.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

##############################################################################
# Budgets
##############################################################################
resource "aws_budgets_budget" "credit_guard" {
  name              = "${local.name}-credit-guard"
  budget_type       = "COST"
  limit_amount      = tostring(var.ceiling_usd)
  limit_unit        = "USD"
  time_unit         = "ANNUALLY" # cumulative across months, not reset each month
  time_period_start = var.tracking_start

  # The point of this budget. With credits included (the AWS default) spend
  # reads $0 for as long as credits last, so no alert could ever fire in time.
  # Refunds are excluded too, so a refund can never mask real usage.
  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = var.warning_thresholds_usd
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "ABSOLUTE_VALUE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  # Early warning from the burn rate, before the shutdown threshold is reached.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.shutdown_usd
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }

  # SHUTDOWN.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.shutdown_usd
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.trigger.arn]
  }

  # Budgets validates that it may publish to the topic when the budget is saved.
  depends_on = [aws_sns_topic_policy.trigger]
}

resource "aws_budgets_budget" "card_charge_guard" {
  name         = "${local.name}-card-charge-guard"
  budget_type  = "COST"
  limit_amount = "1"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Credits INCLUDED (the default), so this sees only what credits did not pay
  # for - which is exactly what would be charged to the card.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.card_charge_threshold_usd
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.trigger.arn]
  }

  depends_on = [aws_sns_topic_policy.trigger]
}

##############################################################################
# Lambda
##############################################################################
data "archive_file" "lambda" {
  type        = "zip"
  source_file = "${path.module}/lambda/handler.py"
  output_path = "${path.module}/build/cost-guard.zip"
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.guard}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "lambda" {
  name = local.guard

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# The ONLY three mutating permissions are ecs:UpdateService,
# elasticloadbalancing:DeleteLoadBalancer and rds:StopDBInstance, each
# restricted by ARN pattern AND by the live ephemeral tags. There is no
# rds:Delete* at all. Even a bug in the code cannot reach the protected
# baseline, another environment, or anything outside this project.
resource "aws_iam_role_policy" "lambda" {
  name = local.guard
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DiscoverAndDescribe"
        Effect = "Allow"
        # Neither API supports resource-level permissions.
        Action   = ["tag:GetResources", "elasticloadbalancing:DescribeLoadBalancers"]
        Resource = "*"
      },
      {
        Sid      = "DescribeEnvironmentServices"
        Effect   = "Allow"
        Action   = ["ecs:DescribeServices"]
        Resource = local.ecs_service_arns
      },
      {
        Sid       = "ScaleEphemeralServicesToZero"
        Effect    = "Allow"
        Action    = ["ecs:UpdateService"]
        Resource  = local.ecs_service_arns
        Condition = { StringEquals = local.ephemeral_tags }
      },
      {
        Sid       = "DeleteEphemeralLoadBalancers"
        Effect    = "Allow"
        Action    = ["elasticloadbalancing:DeleteLoadBalancer"]
        Resource  = "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:loadbalancer/app/${local.name}-*/*"
        Condition = { StringEquals = local.ephemeral_tags }
      },
      {
        Sid      = "DescribeEnvironmentDatabases"
        Effect   = "Allow"
        Action   = ["rds:DescribeDBInstances"]
        Resource = local.db_instance_arn
      },
      {
        # Stop, never delete: a stopped instance keeps its data.
        Sid       = "StopEphemeralDatabases"
        Effect    = "Allow"
        Action    = ["rds:StopDBInstance"]
        Resource  = local.db_instance_arn
        Condition = { StringEquals = local.ephemeral_tags }
      },
      {
        Sid      = "ReadEnvironmentStateOnly"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.tfstate_bucket}/${var.environment_state_key}"
      },
      {
        Sid      = "Report"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.notify.arn
      },
      {
        Sid      = "OwnLogsOnly"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.lambda.arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "guard" {
  function_name    = local.guard
  description      = "Stops ${local.name} (ECS to zero, ALB deleted, RDS stopped) on budget or session-limit breach."
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  architectures    = ["arm64"]
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 60
  memory_size      = 128

  environment {
    variables = {
      DRY_RUN           = var.dry_run ? "true" : "false"
      PROJECT_CODE      = var.project_code
      ENVIRONMENT       = var.environment
      STATE_BUCKET      = var.tfstate_bucket
      STATE_KEY         = var.environment_state_key
      TRIGGER_TOPIC_ARN = aws_sns_topic.trigger.arn
      NOTIFY_TOPIC_ARN  = aws_sns_topic.notify.arn
      MAX_SESSION_HOURS = tostring(var.max_session_hours)
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.lambda]
}

# Budget alarm -> Lambda
resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowBudgetTriggerTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.guard.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.trigger.arn
}

resource "aws_sns_topic_subscription" "trigger_lambda" {
  topic_arn = aws_sns_topic.trigger.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.guard.arn

  depends_on = [aws_lambda_permission.sns]
}

##############################################################################
# Maximum session length - hourly check
##############################################################################
resource "aws_cloudwatch_event_rule" "session_limit" {
  name                = "${local.guard}-session-limit"
  description         = "Stops ${local.name} once it has existed longer than ${var.max_session_hours}h."
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "session_limit" {
  rule  = aws_cloudwatch_event_rule.session_limit.name
  arn   = aws_lambda_function.guard.arn
  input = jsonencode({ trigger = "session-limit" })
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowSessionLimitSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.guard.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.session_limit.arn
}
