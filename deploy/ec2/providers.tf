provider "aws" {
  region = var.region

  # Guardrail: refuse to apply against the wrong account. The CRA test
  # account is 157470074212 — override via var.allowed_account_ids only if
  # you deliberately target another account.
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
