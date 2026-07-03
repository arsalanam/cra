# EC2 instance role. Replaces static AWS keys entirely — the agent resolves
# Bedrock / Cognito / Secrets Manager creds from IMDS. No ~/.aws on the box.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name_prefix}-instance"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = { Name = "${var.name_prefix}-instance" }
}

# Session Manager (browser/CLI shell, no SSH, no bastion).
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "app" {
  # Bedrock. Inference profiles fan out cross-region, so invoke permission is
  # required on BOTH the inference-profile ARN AND the underlying
  # foundation-model ARNs (the #1 Bedrock IAM trap). Region wildcard covers
  # wherever the profile routes.
  statement {
    sid     = "BedrockInvoke"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:aws:bedrock:*:${local.account_id}:inference-profile/${var.bedrock_model_id}",
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0",
    ]
  }

  # Secrets Manager — read only the two cra/test/* secrets this stack owns.
  statement {
    sid       = "SecretsRead"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:${local.account_id}:secret:cra/test/*"]
  }

  # Cognito admin — only the boto3 Admin* calls the user-admin module makes
  # (invite / resend / enable-disable). Hosted-UI OIDC login itself needs no
  # IAM. Drop this statement if you won't exercise invites on this box.
  statement {
    sid = "CognitoAdmin"
    actions = [
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminGetUser",
      "cognito-idp:ListUsers",
      "cognito-idp:AdminUpdateUserAttributes",
      "cognito-idp:AdminAddUserToGroup",
      "cognito-idp:AdminRemoveUserFromGroup",
      "cognito-idp:AdminDisableUser",
      "cognito-idp:AdminEnableUser",
    ]
    resources = ["arn:aws:cognito-idp:${var.region}:${local.account_id}:userpool/${var.cognito_user_pool_id}"]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "${var.name_prefix}-app"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name_prefix}-instance"
  role = aws_iam_role.instance.name
}
