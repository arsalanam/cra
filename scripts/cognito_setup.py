"""One-shot AWS Cognito setup for the Clinical Research Assistant.

Creates (idempotently — reuses existing resources with matching names):
  • A User Pool with email-as-username, strong password policy, email-only
    sign-in.
  • An App Client (confidential, code-flow, scopes openid+email+profile).
  • A hosted-UI domain.

Prints the seven env vars to paste into deploy/compose/.env.

Usage:
    # Uses your default AWS credentials + AWS_DEFAULT_REGION env var.
    uv run --with boto3 python scripts/cognito_setup.py

    # Or with explicit options:
    uv run --with boto3 python scripts/cognito_setup.py \\
        --region us-east-1 \\
        --pool-name cra-users \\
        --client-name cra-app \\
        --domain-prefix cra-arsalanam \\
        --callback-url http://localhost:8000/auth/callback \\
        --logout-url http://localhost:8000/

Requirements:
  • IAM permissions: cognito-idp:CreateUserPool, DescribeUserPool,
    ListUserPools, CreateUserPoolClient, DescribeUserPoolClient,
    ListUserPoolClients, CreateUserPoolDomain, DescribeUserPoolDomain.
  • The hosted-UI domain-prefix must be GLOBALLY unique within the region.
    If creation fails with "already exists", pick a different prefix.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover
    print("boto3 not installed. Run with `uv run --with boto3 ...`.", file=sys.stderr)
    sys.exit(1)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cognito-setup")


def _find_pool_by_name(client: Any, name: str) -> str | None:
    """Return the existing pool id with matching name, or None."""
    paginator = client.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for p in page.get("UserPools", []):
            if p["Name"] == name:
                return str(p["Id"])
    return None


def _ensure_pool(client: Any, name: str) -> str:
    existing = _find_pool_by_name(client, name)
    if existing:
        log.info("User pool %r already exists: %s (reusing)", name, existing)
        return existing
    log.info("Creating user pool %r…", name)
    resp = client.create_user_pool(
        PoolName=name,
        UsernameAttributes=["email"],
        AutoVerifiedAttributes=["email"],
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 12,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
                "TemporaryPasswordValidityDays": 7,
            }
        },
        AdminCreateUserConfig={
            # We're admin-invitation-only — disable self-signup.
            "AllowAdminCreateUserOnly": True,
            "InviteMessageTemplate": {
                "EmailSubject": "You've been invited to the Clinical Research Assistant",
                "EmailMessage": (
                    "Hello,\n\n"
                    "You've been invited to use the Clinical Research Assistant. "
                    "Sign in with this temporary password and you will be asked "
                    "to set a new one:\n\n"
                    "  Username: {username}\n"
                    "  Temporary password: {####}\n\n"
                    "If you weren't expecting this invitation, ignore this email."
                ),
            },
        },
        Schema=[
            {"Name": "email", "Required": True, "Mutable": True, "AttributeDataType": "String"},
        ],
        MfaConfiguration="OFF",  # operator can flip to OPTIONAL later
    )
    pool_id = str(resp["UserPool"]["Id"])
    log.info("Created user pool %s", pool_id)
    return pool_id


def _find_client_by_name(client: Any, pool_id: str, name: str) -> dict[str, Any] | None:
    paginator = client.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=pool_id, MaxResults=60):
        for c in page.get("UserPoolClients", []):
            if c["ClientName"] == name:
                full = client.describe_user_pool_client(
                    UserPoolId=pool_id, ClientId=c["ClientId"]
                )
                return dict(full["UserPoolClient"])
    return None


def _ensure_app_client(
    client: Any,
    pool_id: str,
    name: str,
    callback_url: str,
    logout_url: str,
) -> dict[str, str]:
    existing = _find_client_by_name(client, pool_id, name)
    if existing:
        log.info("App client %r already exists: %s (reusing)", name, existing["ClientId"])
        return {
            "client_id": str(existing["ClientId"]),
            "client_secret": str(existing.get("ClientSecret", "")),
        }
    log.info("Creating app client %r…", name)
    resp = client.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=name,
        GenerateSecret=True,  # confidential client
        ExplicitAuthFlows=[
            "ALLOW_REFRESH_TOKEN_AUTH",
            "ALLOW_USER_SRP_AUTH",
        ],
        AllowedOAuthFlowsUserPoolClient=True,
        AllowedOAuthFlows=["code"],
        AllowedOAuthScopes=["openid", "email", "profile"],
        CallbackURLs=[callback_url],
        LogoutURLs=[logout_url],
        SupportedIdentityProviders=["COGNITO"],
        AccessTokenValidity=60,
        IdTokenValidity=60,
        RefreshTokenValidity=30,
        TokenValidityUnits={
            "AccessToken": "minutes",
            "IdToken": "minutes",
            "RefreshToken": "days",
        },
        PreventUserExistenceErrors="ENABLED",
    )
    out = resp["UserPoolClient"]
    log.info("Created app client %s", out["ClientId"])
    return {
        "client_id": str(out["ClientId"]),
        "client_secret": str(out["ClientSecret"]),
    }


def _ensure_domain(client: Any, pool_id: str, domain_prefix: str) -> str:
    # describe_user_pool_domain returns Status=='UNKNOWN' if it doesn't exist.
    try:
        resp = client.describe_user_pool_domain(Domain=domain_prefix)
        info = resp.get("DomainDescription", {})
        if info.get("UserPoolId") == pool_id and info.get("Status") in (
            "ACTIVE", "CREATING", "UPDATING"
        ):
            log.info("Hosted-UI domain %r already configured (reusing)", domain_prefix)
            return domain_prefix
        if info.get("UserPoolId") and info.get("UserPoolId") != pool_id:
            raise SystemExit(
                f"Domain prefix {domain_prefix!r} is owned by a different "
                f"pool ({info['UserPoolId']}). Pick a different --domain-prefix."
            )
    except ClientError:
        pass  # treat as missing

    log.info("Creating hosted-UI domain %r…", domain_prefix)
    try:
        client.create_user_pool_domain(Domain=domain_prefix, UserPoolId=pool_id)
    except ClientError as e:
        if "AliasExists" in str(e) or "already exists" in str(e).lower():
            raise SystemExit(
                f"Domain prefix {domain_prefix!r} is already taken in this region. "
                "Pick a different --domain-prefix."
            ) from e
        raise
    log.info("Created hosted-UI domain")
    return domain_prefix


def _print_env_block(
    *,
    region: str,
    pool_id: str,
    client_id: str,
    client_secret: str,
    domain_prefix: str,
    callback_url: str,
) -> None:
    domain_url = f"https://{domain_prefix}.auth.{region}.amazoncognito.com"
    print()
    print("=" * 78)
    print("Paste the following into deploy/compose/.env")
    print("=" * 78)
    print(f"COGNITO_REGION={region}")
    print(f"COGNITO_USER_POOL_ID={pool_id}")
    print(f"COGNITO_CLIENT_ID={client_id}")
    print(f"COGNITO_CLIENT_SECRET={client_secret}")
    print(f"COGNITO_DOMAIN={domain_url}")
    print(f"COGNITO_REDIRECT_URI={callback_url}")
    print("# Generate with:  python -c \"import secrets; print(secrets.token_urlsafe(64))\"")
    print("SESSION_COOKIE_SECRET=<paste-a-random-64-byte-string>")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--region", required=True, help="AWS region, e.g. us-east-1")
    parser.add_argument("--pool-name", default="cra-users")
    parser.add_argument("--client-name", default="cra-app")
    parser.add_argument(
        "--domain-prefix",
        required=True,
        help="Globally-unique hosted-UI subdomain (e.g. cra-yourname).",
    )
    parser.add_argument(
        "--callback-url",
        default="http://localhost:8000/auth/callback",
    )
    parser.add_argument(
        "--logout-url",
        default="http://localhost:8000/",
    )
    args = parser.parse_args(argv)

    client = boto3.client("cognito-idp", region_name=args.region)

    pool_id = _ensure_pool(client, args.pool_name)
    client_info = _ensure_app_client(
        client, pool_id, args.client_name, args.callback_url, args.logout_url,
    )
    _ensure_domain(client, pool_id, args.domain_prefix)

    _print_env_block(
        region=args.region,
        pool_id=pool_id,
        client_id=client_info["client_id"],
        client_secret=client_info["client_secret"],
        domain_prefix=args.domain_prefix,
        callback_url=args.callback_url,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
