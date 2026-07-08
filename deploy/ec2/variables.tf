variable "region" {
  description = "AWS region. Bedrock Haiku 4.5 inference profile + Titan embed live in us-east-1."
  type        = string
  default     = "us-east-1"
}

variable "allowed_account_ids" {
  description = "Account IDs this stack is allowed to apply against (safety pin)."
  type        = list(string)
  default     = ["157470074212"]
}

variable "name_prefix" {
  description = "Prefix for all resource names."
  type        = string
  default     = "cra-test"
}

# ── Network ────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR for the new dedicated VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "Two public subnet CIDRs (ALB + NAT), one per AZ."
  type        = list(string)
  default     = ["10.42.0.0/24", "10.42.1.0/24"]
}

variable "private_subnet_cidrs" {
  description = "Two private subnet CIDRs (EC2), one per AZ."
  type        = list(string)
  default     = ["10.42.10.0/24", "10.42.11.0/24"]
}

variable "allowed_ingress_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach the ALB on 80/443. LOCK THIS DOWN to your
    office/home IP (e.g. ["203.0.113.7/32"]). Defaulting to a value you
    must override — an unrestricted test box is a liability.
  EOT
  type        = list(string)
  # No sane default for a public entrypoint — force an explicit choice.
  default = []
  validation {
    condition     = length(var.allowed_ingress_cidrs) > 0
    error_message = "Set allowed_ingress_cidrs to at least your own IP/32 in terraform.tfvars."
  }
}

# ── Compute ────────────────────────────────────────────────────────────────

variable "instance_type" {
  description = "t3.large (8 GB) is the safe small size — sandbox plots + 2 Postgres + agent."
  type        = string
  default     = "t3.large"
}

variable "root_volume_gb" {
  description = "gp3 root volume size. 40 GB fits both Postgres images, the ~790MB sandbox image, and the library/images caches."
  type        = number
  default     = 40
}

# ── App source (git clone + deploy key) ────────────────────────────────────

variable "repo_url" {
  description = "SSH URL of the CRA repo cloned at boot."
  type        = string
  default     = "git@github.com:arsalanam/cra.git"
}

variable "repo_branch" {
  description = "Branch to deploy."
  type        = string
  default     = "main"
}

variable "github_deploy_key" {
  description = <<-EOT
    Private SSH deploy key (PEM) with read access to repo_url. If set, its
    value is written to the cra/test/deploy-key secret. Leave empty and
    populate the secret out-of-band with `aws secretsmanager put-secret-value`
    if you'd rather keep it out of local state. Register the matching PUBLIC
    key as a read-only deploy key on the GitHub repo.
  EOT
  type        = string
  default     = ""
  sensitive   = true
}

# ── App secrets (written into the cra/test/app JSON secret) ─────────────────

variable "app_secrets" {
  description = <<-EOT
    Runtime secrets serialised into the cra/test/app JSON secret. AWS keys
    are intentionally absent — the instance role provides Bedrock/Cognito/
    Secrets creds via IMDS. Fill in terraform.tfvars (kept out of git).
  EOT
  type = object({
    TAVILY_API_KEY             = optional(string, "")
    NCBI_API_KEY               = optional(string, "")
    COGNITO_CLIENT_SECRET      = optional(string, "")
    SESSION_COOKIE_SECRET      = optional(string, "")
    POSTGRES_PASSWORD          = optional(string, "cra")
    CLINICAL_POSTGRES_PASSWORD = optional(string, "cra")
  })
  default   = {}
  sensitive = true
}

# ── VPC endpoints ──────────────────────────────────────────────────────────

variable "enable_vpc_endpoints" {
  description = <<-EOT
    true → add interface endpoints (bedrock-runtime, secretsmanager,
    cognito-idp, ssm, ssmmessages, ec2messages) + an S3 gateway endpoint so
    AWS-service traffic bypasses the NAT. Tightens egress; bills ~$7/mo per
    interface endpoint. Set false on a cost-sensitive box (SSM still works
    over NAT).
  EOT
  type        = bool
  default     = true
}

# ── Bedrock / models ───────────────────────────────────────────────────────

variable "bedrock_model_id" {
  description = "Chat model. Haiku 4.5 requires the long cross-region inference-profile id."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "vision_model_id" {
  description = "Vision model used by describe_image (cross-region inference profile). Must match settings.vision_model_id."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-20250514-v1:0"
}

# ── Cognito (identity only — non-secret ids) ───────────────────────────────

variable "enable_cognito_auth" {
  description = <<-EOT
    false → land as default-user/admin (no auth), best for the first smoke
    test. Flip to true once the stack is healthy AND you've added
    https://<alb-dns>/auth/callback to the Cognito app client's allowed
    callback URLs.
  EOT
  type        = bool
  default     = false
}

variable "cognito_user_pool_id" {
  type    = string
  default = "us-east-1_HpFCZvhKV"
}

variable "cognito_client_id" {
  type    = string
  default = "32o7dbkeem2finegju5kd7jpqi"
}

variable "cognito_domain" {
  type    = string
  default = "https://cra-arsalanam.auth.us-east-1.amazoncognito.com"
}

# ── Edge auth (Option A: ALB -> Cognito) ───────────────────────────────────

variable "enable_alb_auth" {
  description = <<-EOT
    true → the ALB requires a Cognito hosted-UI login before forwarding to the
    app (edge gate, independent of the app's own enable_cognito_auth). Also
    adds ALB egress 443 for the server-side token exchange. Prereq: add
    https://<alb-dns>/oauth2/idpresponse to the app client's allowed callback
    URLs, and have users in the pool.
  EOT
  type        = bool
  default     = true
}

variable "cognito_user_pool_domain_prefix" {
  description = "Cognito hosted-UI domain PREFIX (not the full URL) for authenticate-cognito, e.g. 'cra-arsalanam'."
  type        = string
  default     = "cra-arsalanam"
}

# ── ALB tuning ─────────────────────────────────────────────────────────────

variable "alb_idle_timeout_seconds" {
  description = <<-EOT
    ALB idle timeout. The 60s default is shorter than a long agent turn
    (meta-analysis + sandbox), causing 504s. 300s comfortably exceeds the
    app's agent_timeout_seconds (120). Max allowed is 4000.
  EOT
  type        = number
  default     = 300
}

# ── TLS (self-signed) ──────────────────────────────────────────────────────

variable "server_common_name" {
  description = "CN/SAN on the self-signed cert. Cosmetic (browser warns regardless — access is via the ALB DNS name)."
  type        = string
  default     = "cra-test.internal"
}
