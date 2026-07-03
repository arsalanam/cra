# CRA on EC2 — private subnet + ALB (Option A)

Single-box test deployment of the whole compose stack (agent + Postgres +
clinical-Postgres + docker-out-of-docker sandbox) on one EC2 instance in a
**private** subnet, exposed to the internet only through an **internet-facing
ALB** (self-signed HTTPS). No static AWS keys — the instance role provides
Bedrock / Cognito / Secrets Manager access via IMDS. Admin access is via **SSM
Session Manager** (no SSH, no bastion).

```
Internet ──443──▶ ALB (public subnets, your-IP only) ──8000──▶ EC2 (private subnet)
                                                                   │ NAT ─▶ PubMed / Bedrock /
                                                                   │        Tavily / Cognito / GitHub
                                                          instance role ─▶ Bedrock · Secrets · Cognito · SSM
```

## What Terraform creates

| File | Resources |
|---|---|
| `vpc.tf` | VPC, 2 public + 2 private subnets (2 AZ), IGW, 1 NAT gateway, route tables |
| `vpc_endpoints.tf` | Interface endpoints (Bedrock/Secrets/Cognito/SSM×3) + S3 gateway so AWS traffic bypasses NAT — toggle `enable_vpc_endpoints` |
| `security_groups.tf` | ALB SG (your-IP → 443/80), app SG (ALB → 8000 only, no SSH) |
| `alb.tf` | Self-signed cert → ACM, ALB, target group (`/api/health`), 443 listener + 80→443 redirect |
| `iam.tf` | Instance role: Bedrock (dual ARN) + Secrets read + Cognito admin + SSM core |
| `secrets.tf` | `cra/test/app` (JSON) + `cra/test/deploy-key` secrets |
| `ec2.tf` | `t3.large` AL2023, private, IMDSv2-required, encrypted gp3, `user-data.sh.tftpl` |
| `docker-compose.ec2.yml` | Standalone stack (instance-role auth, no `~/.aws`) used on the box |

## Prerequisites

1. **OpenTofu ≥ 1.6** (`tofu`) and AWS credentials for account `157470074212`
   in your shell (`aws sts get-caller-identity` should show it).
2. **A GitHub read-only deploy key** for the repo:
   ```bash
   ssh-keygen -t ed25519 -f deploy_key -N "" -C "cra-ec2-deploy"
   ```
   Add `deploy_key.pub` as a **Deploy key** on the GitHub repo (Settings →
   Deploy keys → Add, read-only). Put the **private** key into
   `github_deploy_key` in `terraform.tfvars` (or populate the secret manually
   after apply — see below).

## Deploy

```bash
cd deploy/ec2
cp terraform.tfvars.example terraform.tfvars
#   → set allowed_ingress_cidrs to "$(curl -s https://checkip.amazonaws.com)/32"
#   → fill app_secrets + github_deploy_key
tofu init
tofu plan
tofu apply
```

Outputs give you the URL and the SSM command:

```bash
tofu output app_url                 # https://<alb-dns>  (accept the self-signed warning)
tofu output ssm_session_command     # open a shell on the box
```

First boot takes a few minutes (dnf, image builds). Watch it:

```bash
aws ssm start-session --target "$(tofu output -raw instance_id)" --region us-east-1
sudo tail -f /var/log/cloud-init-output.log      # look for "bootstrap complete"
docker compose --env-file /opt/cra/deploy/ec2/.env \
  -f /opt/cra/deploy/ec2/docker-compose.ec2.yml ps
```

The ALB target turns **healthy** once `/api/health` returns 200; then the URL
serves the app (as `default-user`/admin while auth is disabled).

### Populating the deploy key out-of-band (optional)

If you left `github_deploy_key = ""`, set it before the instance boots:

```bash
aws secretsmanager put-secret-value --secret-id cra/test/deploy-key \
  --secret-string file://deploy_key --region us-east-1
```

(Apply the secrets first with `tofu apply -target=aws_secretsmanager_secret.deploy_key`,
put the value, then run the full `tofu apply`.)

## Turning Cognito auth on

1. In the Cognito app client (`us-east-1_HpFCZvhKV`), add the callback URL:
   `tofu output cognito_callback_url`.
2. Set `enable_cognito_auth = true` in `terraform.tfvars`.
3. `tofu apply` — user-data changes, so the **instance is replaced** and comes
   back with auth on. (Volumes are on the instance root; back up first if you
   have test data you care about — see caveats.)

## Redeploying app changes

The box clones a **shallow** copy of `repo_branch`. To pick up new commits:

```bash
# on the box, via SSM:
cd /opt/cra && sudo git pull
sudo docker compose --env-file deploy/ec2/.env -f deploy/ec2/docker-compose.ec2.yml up -d --build
```

For an immutable redeploy, bump anything in `user-data.sh.tftpl` and
`tofu apply` (replaces the instance).

## Teardown

```bash
tofu destroy
```

Secrets use `recovery_window_in_days = 0`, so they delete immediately (no
30-day soft-delete blocking a re-apply of the same names).

## Caveats (test-grade, by design)

- **Data lives on the instance root volume**, not RDS/EBS-snapshot-managed.
  Replacing the instance (auth flip, user-data edit) wipes the two Postgres
  volumes. Fine for testing; move Postgres to RDS for anything you must keep.
- **Self-signed TLS** → browser warnings and a Cognito callback you must
  click through. Swap to an ACM DNS-validated cert (needs a Route53 domain)
  when you want clean HTTPS.
- **Single NAT / single instance** → no HA. Intentional for a small box.
- **Secrets in local state.** `terraform.tfstate` holds the secret values in
  plaintext; it's gitignored. Use an encrypted S3 backend if that matters.
- **NAT egress is open** to `0.0.0.0/0`. AWS-service API calls already avoid
  it via the VPC endpoints (`enable_vpc_endpoints`); NAT then carries only
  external calls (PubMed / Tavily / GitHub / dnf), which you can tighten with
  an egress allow-list or proxy as your controlled exfil boundary.
- **VPC endpoints bill ~$7/mo each** (six interface endpoints). Set
  `enable_vpc_endpoints = false` for the cheapest box — SSM still reaches the
  instance over NAT.
