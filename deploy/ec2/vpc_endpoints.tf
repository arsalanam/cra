# Interface + gateway VPC endpoints so AWS-service API traffic (Bedrock,
# Secrets Manager, Cognito, SSM) stays on the AWS network instead of
# traversing the NAT gateway / public internet. This is the "controlled
# egress" hardening step: NAT is then only for genuinely external calls
# (PubMed / NCBI / Tavily / EuropePMC / GitHub / dnf).
#
# Toggle off with enable_vpc_endpoints=false to save cost — interface
# endpoints bill ~$7/mo each, so all six ≈ NAT-scale spend. On a small test
# box that trade may not be worth it; it's here because it tightens the
# security posture you asked for. SSM keeps working via NAT when off.

locals {
  interface_endpoints = var.enable_vpc_endpoints ? toset([
    "bedrock-runtime", # Bedrock InvokeModel (chat + Titan embeddings)
    "secretsmanager",  # boot-time secret fetch + any runtime reads
    "cognito-idp",     # user-admin Admin* calls
    "ssm",             # Session Manager
    "ssmmessages",     # Session Manager data channel
    "ec2messages",     # SSM agent control channel
  ]) : toset([])
}

# SG for the interface endpoints — 443 from inside the VPC only.
resource "aws_security_group" "endpoints" {
  count       = var.enable_vpc_endpoints ? 1 : 0
  name        = "${var.name_prefix}-vpce"
  description = "HTTPS to interface VPC endpoints from within the VPC"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${var.name_prefix}-vpce" }
}

resource "aws_security_group_rule" "endpoints_in_https" {
  count             = var.enable_vpc_endpoints ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.endpoints[0].id
  protocol          = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_blocks       = [var.vpc_cidr]
  description       = "HTTPS from the VPC"
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = local.interface_endpoints
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints[0].id]
  private_dns_enabled = true
  tags                = { Name = "${var.name_prefix}-vpce-${each.key}" }
}

# S3 gateway endpoint — free; used by the SSM agent and any S3 traffic.
# Attaches to the private route table (route, not ENI).
resource "aws_vpc_endpoint" "s3" {
  count             = var.enable_vpc_endpoints ? 1 : 0
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = { Name = "${var.name_prefix}-vpce-s3" }
}
