##############################################################################
# ALB + ECS Fargate + CodeDeploy blue-green.
#
# ONE Fargate task runs every container: API, Postgres, Qdrant and Redis. No
# RDS, no ElastiCache, no EFS. Data is ephemeral by design and re-seeded on
# every deploy - that is what makes the $20 ceiling achievable.
#
# Blue-green needs TWO target groups. CodeDeploy registers the new task set in
# the idle one, health-checks it through the test listener, and only then moves
# the production listener across. See .claude/rules/deployment.md section 4.
##############################################################################

locals {
  container_name = "api"
}

##############################################################################
# ALB
##############################################################################
resource "aws_lb" "main" {
  name               = "${var.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.alb_security_group_id]
  subnets            = var.public_subnet_ids

  # Ephemeral: no deletion protection, because destroy.sh must be able to work.
  enable_deletion_protection = false
  idle_timeout               = 120 # RAG requests can be slow

  tags = { Name = "${var.name}-alb" }
}

# Blue and green target groups. Exactly one receives production traffic.
resource "aws_lb_target_group" "blue" {
  name        = "${var.name}-blue"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = var.health_check_path
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 15 # short: this is a demo, not a busy production LB
}

resource "aws_lb_target_group" "green" {
  name        = "${var.name}-green"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = var.health_check_path
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 15
}

# Production listener - what real users hit.
resource "aws_lb_listener" "production" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.blue.arn
  }

  # CodeDeploy swaps the target group during a deployment; Terraform must not
  # fight it and swap it back on the next plan.
  lifecycle {
    ignore_changes = [default_action]
  }
}

# Test listener - CodeDeploy health-checks green here BEFORE any real traffic
# moves. This is the whole point of blue-green.
resource "aws_lb_listener" "test" {
  load_balancer_arn = aws_lb.main.arn
  port              = 8080
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.green.arn
  }

  lifecycle {
    ignore_changes = [default_action]
  }
}

##############################################################################
# Logs - 1 day retention. Ephemeral environment, minimal cost.
##############################################################################
resource "aws_cloudwatch_log_group" "service" {
  name              = "/${var.project_code}/${var.environment}/service"
  retention_in_days = var.log_retention_days
}

##############################################################################
# IAM - one role per purpose, least privilege
##############################################################################
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: what ECS itself needs to START the task (pull image, write logs).
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Secrets are injected at task start by the execution role, scoped to this
# project's secret prefix only.
resource "aws_iam_role_policy" "execution_secrets" {
  name = "${var.name}-execution-secrets"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.secret_arns
    }]
  })
}

# Task role: what the APPLICATION may do at runtime.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task" {
  name = "${var.name}-task"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DocumentsBucketOnly"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        # Scoped to objects, not the bucket itself - the app cannot delete the bucket.
        Resource = "arn:aws:s3:::${var.s3_bucket}/*"
      },
      {
        Sid      = "ListOwnBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:aws:s3:::${var.s3_bucket}"
      },
      {
        Sid    = "BedrockInference"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        # Bedrock model ARNs are region/model scoped, not project scoped.
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/*"
      },
      {
        Sid      = "BedrockListModels"
        Effect   = "Allow"
        Action   = ["bedrock:ListFoundationModels", "bedrock:GetFoundationModel"]
        Resource = "*"
      },
      {
        Sid    = "Transcribe"
        Effect = "Allow"
        Action = [
          "transcribe:StartTranscriptionJob",
          "transcribe:GetTranscriptionJob",
        ]
        Resource = "*"
      },
      {
        Sid      = "VerifyCognitoTokens"
        Effect   = "Allow"
        Action   = ["cognito-idp:GetUser", "cognito-idp:DescribeUserPool"]
        Resource = var.cognito_user_pool_arn
      },
    ]
  })
}

##############################################################################
# ECS
##############################################################################
resource "aws_ecs_cluster" "main" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "disabled" # Container Insights bills per metric; not worth it at $20
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    # ---- the API ------------------------------------------------------
    {
      name      = local.container_name
      image     = var.backend_image
      essential = true

      # Postgres is a fresh sidecar on every task, so the schema must be created
      # at startup - the image's default CMD is only uvicorn. The demo data is
      # re-seeded here too ("re-seeded on every deploy"). The seed is non-fatal:
      # a Bedrock quota or throttling problem must not stop the API serving.
      command = [
        "sh", "-c",
        "alembic upgrade head && (python -m seeds.seed || echo 'seed failed - continuing without demo data') && exec uvicorn app.main:app --host 0.0.0.0 --port ${var.container_port}",
      ]

      portMappings = [{ containerPort = var.container_port, protocol = "tcp" }]

      environment = [
        for k, v in var.environment_variables : { name = k, value = v }
      ]

      secrets = [
        for k, v in var.secret_environment : { name = k, valueFrom = v }
      ]

      # Non-root, read-only root filesystem where the app allows it.
      user                   = "10001"
      readonlyRootFilesystem = false # uvicorn writes temp files

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.service.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -fsS http://127.0.0.1:${var.container_port}/api/v1/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      dependsOn = [
        { containerName = "postgres", condition = "HEALTHY" },
        { containerName = "qdrant", condition = "START" },
        { containerName = "redis", condition = "HEALTHY" },
      ]
    },

    # ---- Postgres as a container (no RDS) ------------------------------
    {
      name      = "postgres"
      image     = "postgres:16-alpine"
      essential = true

      environment = [
        { name = "POSTGRES_USER", value = "ekba" },
        { name = "POSTGRES_DB", value = "ekba" },
      ]
      secrets = [{ name = "POSTGRES_PASSWORD", valueFrom = var.postgres_password_secret_arn }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.service.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "postgres"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "pg_isready -U ekba"]
        interval    = 10
        timeout     = 5
        retries     = 5
        startPeriod = 30
      }
    },

    # ---- Qdrant as a container (no managed vector service) -------------
    {
      name      = "qdrant"
      image     = "qdrant/qdrant:v1.12.4"
      essential = true

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.service.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "qdrant"
        }
      }
    },

    # ---- Redis as a container (no ElastiCache) -------------------------
    {
      name      = "redis"
      image     = "redis:7-alpine"
      essential = true
      command   = ["redis-server", "--save", "", "--appendonly", "no"]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.service.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "redis"
        }
      }

      healthCheck = {
        command     = ["CMD", "redis-cli", "ping"]
        interval    = 10
        timeout     = 5
        retries     = 5
        startPeriod = 15
      }
    },
  ])
}

resource "aws_ecs_service" "app" {
  name            = var.name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # CodeDeploy owns the rollout, not ECS rolling update.
  deployment_controller {
    type = "CODE_DEPLOY"
  }

  network_configuration {
    subnets = var.public_subnet_ids
    # Public IP so the task reaches ECR/Bedrock without a NAT Gateway.
    assign_public_ip = true
    security_groups  = [var.tasks_security_group_id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.blue.arn
    container_name   = local.container_name
    container_port   = var.container_port
  }

  health_check_grace_period_seconds = 120

  # CodeDeploy mutates these during a deployment.
  lifecycle {
    ignore_changes = [task_definition, load_balancer, desired_count]
  }

  depends_on = [aws_lb_listener.production]
}

##############################################################################
# CodeDeploy - blue-green
##############################################################################
resource "aws_iam_role" "codedeploy" {
  name = "${var.name}-codedeploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codedeploy.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "codedeploy" {
  role       = aws_iam_role.codedeploy.name
  policy_arn = "arn:aws:iam::aws:policy/AWSCodeDeployRoleForECS"
}

resource "aws_codedeploy_app" "main" {
  name             = var.name
  compute_platform = "ECS"
}

resource "aws_codedeploy_deployment_group" "main" {
  app_name               = aws_codedeploy_app.main.name
  deployment_group_name  = "${var.name}-dg"
  service_role_arn       = aws_iam_role.codedeploy.arn
  deployment_config_name = "CodeDeployDefault.ECSAllAtOnce"

  deployment_style {
    deployment_option = "WITH_TRAFFIC_CONTROL"
    deployment_type   = "BLUE_GREEN"
  }

  blue_green_deployment_config {
    deployment_ready_option {
      # Shift automatically once green is healthy. Set to STOP_DEPLOYMENT for a
      # manual gate if you want to inspect green before traffic moves.
      action_on_timeout = "CONTINUE_DEPLOYMENT"
    }

    terminate_blue_instances_on_deployment_success {
      action = "TERMINATE"
      # The rollback window: blue stays alive this long after the shift.
      termination_wait_time_in_minutes = var.rollback_window_minutes
    }
  }

  # Automatic rollback if the deployment fails or an alarm fires.
  auto_rollback_configuration {
    enabled = true
    events  = ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_ALARM"]
  }

  alarm_configuration {
    enabled = true
    alarms  = [aws_cloudwatch_metric_alarm.error_rate.alarm_name]
  }

  ecs_service {
    cluster_name = aws_ecs_cluster.main.name
    service_name = aws_ecs_service.app.name
  }

  load_balancer_info {
    target_group_pair_info {
      prod_traffic_route {
        listener_arns = [aws_lb_listener.production.arn]
      }
      test_traffic_route {
        listener_arns = [aws_lb_listener.test.arn]
      }
      target_group { name = aws_lb_target_group.blue.name }
      target_group { name = aws_lb_target_group.green.name }
    }
  }
}

##############################################################################
# Observability - alarms, including one on spend
##############################################################################
resource "aws_cloudwatch_metric_alarm" "error_rate" {
  alarm_name          = "${var.name}-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_description   = "Backend 5xx rate. Wired into CodeDeploy auto-rollback."

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "latency" {
  alarm_name          = "${var.name}-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p95" # percentiles use extended_statistic, not statistic
  threshold           = 10    # seconds - RAG is not instant
  treat_missing_data  = "notBreaching"
  alarm_description   = "p95 latency above 10s."

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "estimated_charges" {
  # Billing metrics only exist in us-east-1.
  count = var.aws_region == "us-east-1" ? 1 : 0

  alarm_name          = "${var.name}-spend"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = 21600
  statistic           = "Maximum"
  threshold           = var.spend_alarm_usd
  treat_missing_data  = "notBreaching"
  alarm_description   = "Estimated charges above $${var.spend_alarm_usd}."

  dimensions = { Currency = "USD" }
}
