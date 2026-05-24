"""Cognito admin operations for the invitation flow (Phase C).

`AdminCreateUser` is the only way a user enters the pool — the User Pool
is configured with `AllowAdminCreateUserOnly=True` (see
`scripts/cognito_setup.py`). Identity lives in Cognito; app roles live in
the local DB (see `persistence.user_repository`).

Used by the admin invite endpoint (`web/admin.py`) and the
`cra create-admin` CLI (`cli.py`).
"""

from __future__ import annotations

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
