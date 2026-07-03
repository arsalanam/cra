# Two secrets, both read by the instance role at boot:
#   cra/test/app         — JSON blob of runtime app secrets (no AWS keys)
#   cra/test/deploy-key  — GitHub SSH private key used to clone the repo
#
# recovery_window_in_days = 0 lets `tofu destroy` delete them immediately
# (default is a 30-day soft-delete that blocks recreating the same name).

resource "aws_secretsmanager_secret" "app" {
  name                    = "cra/test/app"
  description             = "CRA runtime app secrets (Tavily/NCBI/Cognito client secret/session/DB passwords)"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id     = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(var.app_secrets)
}

resource "aws_secretsmanager_secret" "deploy_key" {
  name                    = "cra/test/deploy-key"
  description             = "GitHub read-only SSH deploy key for cloning the CRA repo at boot"
  recovery_window_in_days = 0
}

# Only manage the value from Terraform if you passed one. Otherwise the secret
# is created empty and you populate it out-of-band:
#   aws secretsmanager put-secret-value --secret-id cra/test/deploy-key \
#     --secret-string file://deploy_key.pem
# Do this BEFORE the instance boots, or re-run user-data (reboot) after.
resource "aws_secretsmanager_secret_version" "deploy_key" {
  count         = var.github_deploy_key == "" ? 0 : 1
  secret_id     = aws_secretsmanager_secret.deploy_key.id
  secret_string = var.github_deploy_key
}
