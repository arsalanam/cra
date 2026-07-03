output "app_url" {
  description = "Public HTTPS entrypoint (self-signed cert — expect a browser warning)."
  value       = "https://${aws_lb.this.dns_name}"
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "cognito_callback_url" {
  description = "Add this to the Cognito app client's allowed callback URLs before enabling auth."
  value       = "https://${aws_lb.this.dns_name}/auth/callback"
}

output "instance_id" {
  value = aws_instance.app.id
}

output "ssm_session_command" {
  description = "Open an admin shell on the box (no SSH / no bastion)."
  value       = "aws ssm start-session --target ${aws_instance.app.id} --region ${var.region}"
}

output "tail_bootstrap_log" {
  description = "After SSM'ing in, watch bootstrap progress."
  value       = "sudo tail -f /var/log/cloud-init-output.log"
}

output "app_secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "deploy_key_secret_arn" {
  value = aws_secretsmanager_secret.deploy_key.arn
}

output "vpc_id" {
  value = aws_vpc.this.id
}
