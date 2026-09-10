output "app_url" {
  description = "Open this in a browser during the demo."
  value       = module.service.app_url
}

output "test_url" {
  description = "Test listener where CodeDeploy validates green before shifting traffic."
  value       = module.service.test_url
}

output "cluster_name" {
  value = module.service.cluster_name
}

output "service_name" {
  value = module.service.service_name
}

output "codedeploy_app_name" {
  value = module.service.codedeploy_app_name
}

output "codedeploy_deployment_group" {
  value = module.service.codedeploy_deployment_group
}

output "log_group" {
  value = module.service.log_group
}

output "cost_reminder" {
  description = "Read this every time."
  value       = "Burning ${module.service.hourly_cost_estimate_usd} USD/hour. Run scripts/destroy.sh when the demo ends."
}
