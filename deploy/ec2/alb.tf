# Self-signed cert imported into ACM so the ALB can serve HTTPS on its raw
# DNS name (no domain required). Browsers warn — expected for a test box;
# the Cognito callback still works once you accept the cert.
resource "tls_private_key" "selfsigned" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "selfsigned" {
  private_key_pem = tls_private_key.selfsigned.private_key_pem

  subject {
    common_name  = var.server_common_name
    organization = "CRA Test"
  }

  dns_names             = [var.server_common_name]
  validity_period_hours = 8760 # 1 year
  allowed_uses          = ["key_encipherment", "digital_signature", "server_auth"]
}

resource "aws_acm_certificate" "selfsigned" {
  private_key      = tls_private_key.selfsigned.private_key_pem
  certificate_body = tls_self_signed_cert.selfsigned.cert_pem
  tags             = { Name = "${var.name_prefix}-selfsigned" }
}

# ── ALB ─────────────────────────────────────────────────────────────────────
resource "aws_lb" "this" {
  name               = "${var.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # Default is 60s, which is shorter than a single long agent turn
  # (meta-analysis: PubMed search + Bedrock + sandbox forest plot). Too low
  # and the ALB returns 504 while the backend is still working.
  idle_timeout = var.alb_idle_timeout_seconds

  tags = { Name = "${var.name_prefix}-alb" }
}

resource "aws_lb_target_group" "app" {
  name        = "${var.name_prefix}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.this.id
  target_type = "instance"

  health_check {
    path                = "/api/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }

  tags = { Name = "${var.name_prefix}-tg" }
}

resource "aws_lb_target_group_attachment" "app" {
  target_group_arn = aws_lb_target_group.app.arn
  target_id        = aws_instance.app.id
  port             = 8000
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.selfsigned.arn

  # Edge auth (Option A): require a Cognito hosted-UI login before the app is
  # reachable. ALB completes the OAuth code exchange server-side against the
  # Cognito token endpoint — that needs ALB egress 443 (security_groups.tf).
  # The app client must list https://<alb-dns>/oauth2/idpresponse as an
  # allowed callback URL.
  dynamic "default_action" {
    for_each = var.enable_alb_auth ? [1] : []
    content {
      type  = "authenticate-cognito"
      order = 1
      authenticate_cognito {
        user_pool_arn              = "arn:aws:cognito-idp:${var.region}:${data.aws_caller_identity.current.account_id}:userpool/${var.cognito_user_pool_id}"
        user_pool_client_id        = var.cognito_client_id
        user_pool_domain           = var.cognito_user_pool_domain_prefix
        on_unauthenticated_request = "authenticate"
        scope                      = "openid"
        session_timeout            = 3600
      }
    }
  }

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
    order            = var.enable_alb_auth ? 2 : 1
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
