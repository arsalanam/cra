# Latest Amazon Linux 2023 AMI via SSM public parameter (no hardcoded AMI id).
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.private[0].id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # Private subnet — no public IP. Admin access is via SSM Session Manager.
  associate_public_ip_address = false

  # IMDSv2 required (token-based) so the instance role can't be scraped via a
  # forged/SSRF IMDSv1 request from inside a container.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # allow the agent container one hop to IMDS
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    region               = var.region
    repo_url             = var.repo_url
    repo_branch          = var.repo_branch
    app_secret_id        = aws_secretsmanager_secret.app.name
    deploy_key_secret_id = aws_secretsmanager_secret.deploy_key.name
    bedrock_model_id     = var.bedrock_model_id
    alb_dns_name         = aws_lb.this.dns_name
    enable_cognito_auth  = var.enable_cognito_auth
    enable_alb_auth      = var.enable_alb_auth
    cognito_user_pool_id = var.cognito_user_pool_id
    cognito_client_id    = var.cognito_client_id
    cognito_domain       = var.cognito_domain
  })

  tags = { Name = "${var.name_prefix}-app" }

  # Stateful box: the Postgres volumes live on the root disk, and app code is
  # redeployed via `git pull` on the instance (not by re-bootstrapping). So
  # ignore AMI churn (AL2023 publishes new images constantly) and user_data
  # drift so a routine `tofu apply` never silently destroys the box. Instance
  # replacement is opt-in only:
  #   tofu apply -replace=aws_instance.app
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  # The secret VALUES must exist before boot (user-data reads them).
  depends_on = [
    aws_secretsmanager_secret_version.app,
    aws_iam_role_policy.app,
    aws_nat_gateway.this,
  ]
}
