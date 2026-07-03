# Tooling + provider pins. OpenTofu (or Terraform) with local state.
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Local state by design (single-operator test deploy). *.tfstate is
  # gitignored — see .gitignore. Switch to an S3 backend here if more than
  # one machine will run applies.
}
