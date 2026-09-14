output "lambda_function_name" {
  description = "Invoke directly with {\"trigger\": \"manual\"} to test (respects dry_run)."
  value       = aws_lambda_function.guard.function_name
}

output "trigger_topic_arn" {
  description = "Budgets publish shutdown requests here."
  value       = aws_sns_topic.trigger.arn
}

output "notify_topic_arn" {
  description = "Shutdown reports are published here."
  value       = aws_sns_topic.notify.arn
}

output "budget_names" {
  description = "Budgets created by the guard."
  value       = [aws_budgets_budget.credit_guard.name, aws_budgets_budget.card_charge_guard.name]
}

output "dry_run" {
  description = "Whether the kill switch is only reporting."
  value       = var.dry_run
}
