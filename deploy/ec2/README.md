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

> **Where commands run.** Two environments are involved:
> - 🪟 **Your Windows machine — PowerShell**, always from `D:\researchwork\cra\deploy\ec2`.
>   `tofu`, `aws`, `ssh-keygen` all run here.
> - 🐧 **The EC2 box — Linux shell** (Amazon Linux 2023), reached with
>   `aws ssm start-session`. Blocks marked "on the box" run there, not on Windows.

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

## Prerequisites 🪟 (Windows / PowerShell)

Start every local step from the EC2 directory:

```powershell
Set-Location D:\researchwork\cra\deploy\ec2
```

1. **OpenTofu ≥ 1.6** (`tofu`) on `PATH`, and AWS credentials for your target
   account in your shell (pin it via `allowed_account_ids` in
   `terraform.tfvars`) — verify:
   ```powershell
   tofu version
   aws sts get-caller-identity
   ```
2. **AWS Session Manager plugin** (required for `aws ssm start-session`):
   <https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html>
3. **A GitHub read-only deploy key** (OpenSSH ships with Windows 10/11):
   ```powershell
   ssh-keygen -t ed25519 -f deploy_key -C "cra-ec2-deploy"
   #   → press Enter twice for an empty passphrase
   ```
   Add `deploy_key.pub` as a **Deploy key** on the GitHub repo (Settings →
   Deploy keys → Add, read-only). Put the **private** key `deploy_key` into
   `github_deploy_key` in `terraform.tfvars` (or populate the secret manually —
   see below). `deploy_key*` is gitignored, so it won't be committed.

## Deploy 🪟 (Windows / PowerShell)

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
#   then edit terraform.tfvars:
#     allowed_ingress_cidrs → your public IP as ["x.x.x.x/32"]  (command below)
#     app_secrets           → Tavily / NCBI / session / DB passwords
#     github_deploy_key      → contents of the deploy_key file (or leave "")
tofu init
tofu plan
tofu apply
```

Find your public IP for `allowed_ingress_cidrs`:

```powershell
(Invoke-RestMethod https://checkip.amazonaws.com).Trim()
#   → put "<that-ip>/32" in terraform.tfvars, e.g. allowed_ingress_cidrs = ["203.0.113.7/32"]
```

> Tip: to load the private key straight from the file instead of pasting it,
> set `github_deploy_key = file("deploy_key")` in `terraform.tfvars`.

Outputs give you the URL and the SSM command:

```powershell
tofu output app_url                 # https://<alb-dns>  (accept the self-signed warning)
tofu output ssm_session_command     # ready-to-run start-session command
```

First boot takes a few minutes (dnf, image builds). Open a shell on the box
(🪟 PowerShell — needs the Session Manager plugin):

```powershell
aws ssm start-session --target (tofu output -raw instance_id) --region us-east-1
```

Then, 🐧 **on the box**, watch bootstrap and check the stack:

```bash
sudo tail -f /var/log/cloud-init-output.log      # look for "bootstrap complete"
sudo docker compose --env-file /opt/cra/deploy/ec2/.env \
  -f /opt/cra/deploy/ec2/docker-compose.ec2.yml ps
```

The ALB target turns **healthy** once `/api/health` returns 200; then the URL
serves the app (as `default-user`/admin while auth is disabled).

### Populating the deploy key out-of-band (optional) 🪟

If you left `github_deploy_key = ""`, set it before the instance boots. In
PowerShell, quote the `-target` argument and use a backtick for line-continuation:

```powershell
tofu apply "-target=aws_secretsmanager_secret.deploy_key"
aws secretsmanager put-secret-value --secret-id cra/test/deploy-key `
  --secret-string file://deploy_key --region us-east-1
tofu apply
```

## Turning Cognito auth on 🪟

1. In your Cognito app client (`cognito_user_pool_id` from `terraform.tfvars`),
   add the callback URL:
   ```powershell
   tofu output cognito_callback_url
   ```
2. Set `enable_cognito_auth = true` in `terraform.tfvars`.
3. `tofu apply` — user-data changes, so the **instance is replaced** and comes
   back with auth on. (Volumes are on the instance root; back up first if you
   have test data you care about — see caveats.)

## Redeploying app changes 🐧 (on the box, via SSM)

The box clones a **shallow** copy of `repo_branch`. To pick up new commits:

```bash
cd /opt/cra && sudo git pull
sudo docker compose --env-file deploy/ec2/.env -f deploy/ec2/docker-compose.ec2.yml up -d --build
```

For an immutable redeploy, bump anything in `user-data.sh.tftpl` and run
`tofu apply` from Windows (replaces the instance).

## Teardown 🪟 (Windows / PowerShell)

```powershell
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

## Windows notes

- All 🪟 commands are **PowerShell** (your default shell). `Copy-Item`,
  `Invoke-RestMethod`, and backtick line-continuation are PowerShell-native;
  the Unix `cp` / `curl` / `\` continuations do **not** behave the same here.
- `tofu`, `aws`, and `ssh-keygen` are the same binaries on Windows — only the
  surrounding shell syntax differs.
- Paths in `.tf` files and `docker-compose.ec2.yml` use `/opt/cra/...` because
  they run on the **Linux** EC2 host, not on Windows. Don't translate those.
