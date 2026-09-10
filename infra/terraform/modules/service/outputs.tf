output "alb_dns_name" {
  description = "Public DNS of the load balancer."
  value       = aws_lb.main.dns_name
}

output "app_url" {
  description = "Where the demo is reachable."
  value       = "http://${aws_lb.main.dns_name}"
}

output "test_url" {
  description = "Test listener - CodeDeploy health-checks green here before shifting traffic."
  value       = "http://${aws_lb.main.dns_name}:8080"
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "service_name" {
  value = aws_ecs_service.app.name
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.app.arn
}

output "codedeploy_app_name" {
  value = aws_codedeploy_app.main.name
}

output "codedeploy_deployment_group" {
  value = aws_codedeploy_deployment_group.main.deployment_group_name
}

output "log_group" {
  value = aws_cloudwatch_log_group.service.name
}

output "hourly_cost_estimate_usd" {
  description = "Rough burn while this stack exists."
  value       = "~0.072 (ALB ~0.023 + Fargate 1vCPU/3GB ~0.049)"
}
