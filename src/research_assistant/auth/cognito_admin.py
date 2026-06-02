"""Cognito admin operations for the invitation flow (Phase C) and the
electronic-signature password re-authentication (eCRF E7 — Part 11 §11.200).

`AdminCreateUser` is the only way a user enters the pool — the User Pool
is configured with `AllowAdminCreateUserOnly=True` (see
`scripts/cognito_setup.py`). Identity lives in Cognito; app roles live in
the local DB (see `persistence.user_repository`).

Used by the admin invite endpoint (`web/admin.py`), the
`cra create-admin` CLI (`cli.py`), and the EDC signing endpoints
(`web/edc.py`) which require password re-auth at signing time.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class CognitoAdminError(Exception):
    """Raised when a Cognito admin call fails (other than already-exists)."""


def create_cognito_user(email: str, *, region: str, user_pool_id: str) -> str:
    """Invite a user via `AdminCreateUser`. Idempotent on already-exists.

    Returns the Cognito user status (e.g. ``"FORCE_CHANGE_PASSWORD"``), or
    ``"EXISTS"`` if the user was already in the pool (no new invite sent).
    """
    client = boto3.client("cognito-idp", region_name=region)
    try:
        resp = client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
        return str(resp["User"]["UserStatus"])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "UsernameExistsException":
            logger.info("Cognito user %s already exists; invitation not re-sent", email)
            return "EXISTS"
        raise CognitoAdminError(f"AdminCreateUser failed ({code}): {e}") from e


def resend_cognito_invitation(email: str, *, region: str, user_pool_id: str) -> str:
    """Sprint U1 — re-send the Cognito invitation email for an
    already-provisioned user. Uses `AdminCreateUser` with
    `MessageAction=RESEND` (the documented way to re-deliver an invite
    for an unconfirmed user). Returns the Cognito user status.

    Raises `CognitoAdminError` if the user is already confirmed (no
    invite to resend) or boto3 surfaces another failure.
    """
    client = boto3.client("cognito-idp", region_name=region)
    try:
        resp = client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
            MessageAction="RESEND",
        )
        return str(resp["User"]["UserStatus"])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "UserNotFoundException":
            raise CognitoAdminError(
                f"Cognito user {email!r} not found; cannot resend invite."
            ) from e
        if code == "InvalidParameterException":
            raise CognitoAdminError(
                f"Cannot resend invite for {email!r} — user may already be confirmed."
            ) from e
        raise CognitoAdminError(f"AdminCreateUser RESEND failed ({code}): {e}") from e


def _secret_hash(username: str, client_id: str, client_secret: str) -> str:
    """Per Cognito docs: BASE64(HMAC-SHA256(client_secret, username + client_id))."""
    msg = (username + client_id).encode("utf-8")
    key = client_secret.encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_user_password(
    sub: str,
    password: str,
    *,
    region: str,
    user_pool_id: str,
    client_id: str,
    client_secret: str = "",
) -> bool:
    """Re-verify the user's password via `AdminInitiateAuth`.

    Used at e-signature time per 21 CFR Part 11 §11.200 — non-biometric
    signatures must require two identification components (the active
    session plus an explicit credential challenge). Returns ``True`` on
    successful re-authentication, ``False`` on bad password / unknown
    user, and raises :class:`CognitoAdminError` for any other failure
    (network, configuration, throttling).

    The pool's app-client must have ``ADMIN_USER_PASSWORD_AUTH``
    enabled. The Cognito sub → username lookup uses
    ``ListUsers(Filter='sub = "..."')``.
    """
    client = boto3.client("cognito-idp", region_name=region)
    try:
        listed = client.list_users(
            UserPoolId=user_pool_id,
            Filter=f'sub = "{sub}"',
            Limit=1,
        )
    except ClientError as e:
        raise CognitoAdminError(f"ListUsers failed: {e}") from e

    users = listed.get("Users", [])
    if not users:
        return False
    username = str(users[0]["Username"])

    auth_params: dict[str, str] = {"USERNAME": username, "PASSWORD": password}
    if client_secret:
        auth_params["SECRET_HASH"] = _secret_hash(username, client_id, client_secret)

    try:
        client.admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters=auth_params,
        )
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in {"NotAuthorizedException", "UserNotFoundException"}:
            return False
        raise CognitoAdminError(f"AdminInitiateAuth failed ({code}): {e}") from e


async def verify_user_password_async(
    sub: str,
    password: str,
    *,
    region: str,
    user_pool_id: str,
    client_id: str,
    client_secret: str = "",
) -> bool:
    """Async wrapper around :func:`verify_user_password` (boto3 is sync)."""
    return await asyncio.to_thread(
        verify_user_password,
        sub,
        password,
        region=region,
        user_pool_id=user_pool_id,
        client_id=client_id,
        client_secret=client_secret,
    )
