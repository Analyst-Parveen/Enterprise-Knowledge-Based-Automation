output "address" {
  description = "Private DNS name of the instance. Resolvable and reachable only inside the VPC."
  value       = aws_db_instance.main.address
}

output "port" {
  value = aws_db_instance.main.port
}

output "identifier" {
  value = aws_db_instance.main.identifier
}

output "arn" {
  value = aws_db_instance.main.arn
}

output "db_name" {
  value = var.db_name
}

output "username" {
  value = aws_db_instance.main.username
}
