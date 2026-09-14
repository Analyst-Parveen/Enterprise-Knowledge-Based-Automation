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

output "db_identifier" {
  description = "RDS instance; destroy.sh snapshots it before destroying the stack."
  value       = module.database.identifier
}

output "db_address" {
  description = "Private endpoint - resolvable and reachable only from inside the VPC."
  value       = module.database.address
}

output "cost_reminder" {
  description = "Read this every time."
  value       = "Burning ${module.service.hourly_cost_estimate_usd} USD/hour for the service, plus ~0.019 USD/hour for RDS (db.t4g.micro + 20 GB gp3). Run scripts/destroy.sh when the demo ends."
}
