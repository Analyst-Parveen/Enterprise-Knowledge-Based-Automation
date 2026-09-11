output "lambda_function_name" {
  description = "The kill switch."
  value       = module.cost_guard.lambda_function_name
}

output "budget_names" {
  description = "Budgets created by the guard."
  value       = module.cost_guard.budget_names
}

output "dry_run" {
  description = "true = report only. false = the kill switch will act."
  value       = module.cost_guard.dry_run
}

output "next_steps" {
  description = "What to do after this applies."
  value       = <<-EOT
    1. Click the SNS confirmation link emailed to you, or shutdown reports
       will not arrive (budget alerts arrive either way).

    2. Test the kill switch. In dry-run it reports and changes nothing:
         aws lambda invoke --function-name ${module.cost_guard.lambda_function_name} \
           --cli-binary-format raw-in-base64-out \
           --payload '{"trigger":"manual"}' out.json

    3. Only after reviewing that report, set dry_run = false, plan and apply.
  EOT
}
