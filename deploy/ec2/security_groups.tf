# ALB SG — public entrypoint, locked to your allowed CIDRs.
resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb"
  description = "ALB ingress from allowed CIDRs only"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${var.name_prefix}-alb" }
}

resource "aws_security_group_rule" "alb_in_https" {
  type              = "ingress"
  security_group_id = aws_security_group.alb.id
  protocol          = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_blocks       = var.allowed_ingress_cidrs
  description       = "HTTPS from allowed CIDRs"
}

resource "aws_security_group_rule" "alb_in_http" {
  type              = "ingress"
  security_group_id = aws_security_group.alb.id
  protocol          = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_blocks       = var.allowed_ingress_cidrs
  description       = "HTTP (redirected to HTTPS) from allowed CIDRs"
}

resource "aws_security_group_rule" "alb_out_to_app" {
  type                     = "egress"
  security_group_id        = aws_security_group.alb.id
  protocol                 = "tcp"
  from_port                = 8000
  to_port                  = 8000
  source_security_group_id = aws_security_group.app.id
  description              = "Forward to the agent container"
}

# authenticate-cognito: the ALB itself calls the Cognito token endpoint
# (server-side code exchange). Without outbound 443 the login hangs at the
# token step. Only present when edge auth is enabled.
resource "aws_security_group_rule" "alb_out_https" {
  count             = var.enable_alb_auth ? 1 : 0
  type              = "egress"
  security_group_id = aws_security_group.alb.id
  protocol          = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "ALB egress to Cognito token endpoint (authenticate-cognito)"
}

# App/instance SG — no public ingress. Only the ALB may reach :8000; SSM
# handles admin access, so no SSH rule at all.
resource "aws_security_group" "app" {
  name        = "${var.name_prefix}-app"
  description = "EC2 app tier - ingress only from the ALB"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${var.name_prefix}-app" }
}

resource "aws_security_group_rule" "app_in_from_alb" {
  type                     = "ingress"
  security_group_id        = aws_security_group.app.id
  protocol                 = "tcp"
  from_port                = 8000
  to_port                  = 8000
  source_security_group_id = aws_security_group.alb.id
  description              = "Agent HTTP from the ALB only"
}

resource "aws_security_group_rule" "app_out_all" {
  type              = "egress"
  security_group_id = aws_security_group.app.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Outbound via NAT (Bedrock, PubMed, Tavily, Cognito, SSM, GitHub)"
}
