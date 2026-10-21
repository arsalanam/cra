provider "aws" {
  region = var.region

  # Guardrail: refuse to apply against the wrong account. Set
  # var.allowed_account_ids to your account id in terraform.tfvars; leave it
  # empty to disable the check.
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = {
      Project     = "cra"
      Environment = "test"
      ManagedBy   = "opentofu"
      Stack       = "cra-ec2"
    }
  }
}

# tls provider needs no configuration — used to mint the self-signed cert.
provider "tls" {}
