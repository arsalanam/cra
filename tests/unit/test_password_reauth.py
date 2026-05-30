"""Cognito password re-authentication helper (eCRF E7).

Exercises the boto3-driven `verify_user_password` end-to-end via a
mocked Cognito client. The signing-endpoint integration that wraps
it (`_require_signing_reauth` in `web/edc.py`) is covered in
`tests/integration/test_signing_reauth.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError

from research_assistant.auth.cognito_admin import (
    CognitoAdminError,
    _secret_hash,
    verify_user_password,
)


class _FakeClient:
    """Minimal stand-in for the boto3 cognito-idp client."""

    def __init__(
        self,
        *,
        list_users_returns: list[dict[str, Any]] | None = None,
        auth_raises: ClientError | None = None,
    ) -> None:
        self.list_users_returns = list_users_returns or []
        self.auth_raises = auth_raises
        self.auth_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def list_users(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        return {"Users": self.list_users_returns}

    def admin_initiate_auth(self, **kwargs: Any) -> dict[str, Any]:
        self.auth_calls.append(kwargs)
        if self.auth_raises is not None:
            raise self.auth_raises
        return {"AuthenticationResult": {"AccessToken": "fake"}}


def _patch_boto3(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr(
        "research_assistant.auth.cognito_admin.boto3",
        SimpleNamespace(client=lambda *_args, **_kwargs: fake),
    )


def test_secret_hash_is_stable_b64() -> None:
    h1 = _secret_hash("alice@example.com", "client-id", "secret")
    h2 = _secret_hash("alice@example.com", "client-id", "secret")
    assert h1 == h2
    # Different inputs change the hash
    assert h1 != _secret_hash("bob@example.com", "client-id", "secret")


def test_verify_user_password_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(list_users_returns=[{"Username": "alice@example.com"}])
    _patch_boto3(monkeypatch, fake)
    ok = verify_user_password(
        "sub-1",
        "right-password",
        region="us-east-1",
        user_pool_id="pool",
        client_id="client",
        client_secret="secret",
    )
    assert ok is True
    assert fake.list_calls[0]["Filter"] == 'sub = "sub-1"'
    assert fake.auth_calls[0]["AuthFlow"] == "ADMIN_USER_PASSWORD_AUTH"
    assert fake.auth_calls[0]["AuthParameters"]["USERNAME"] == "alice@example.com"
    assert "SECRET_HASH" in fake.auth_calls[0]["AuthParameters"]


def test_verify_user_password_returns_false_on_unknown_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient(list_users_returns=[])
    _patch_boto3(monkeypatch, fake)
    ok = verify_user_password(
        "sub-missing",
        "any",
        region="us-east-1",
        user_pool_id="pool",
        client_id="client",
    )
    assert ok is False


def test_verify_user_password_returns_false_on_bad_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = ClientError(
        error_response={"Error": {"Code": "NotAuthorizedException", "Message": "bad"}},
        operation_name="AdminInitiateAuth",
    )
    fake = _FakeClient(
        list_users_returns=[{"Username": "alice@example.com"}],
        auth_raises=err,
    )
    _patch_boto3(monkeypatch, fake)
    ok = verify_user_password(
        "sub-1",
        "wrong",
        region="us-east-1",
        user_pool_id="pool",
        client_id="client",
    )
    assert ok is False


def test_verify_user_password_raises_on_other_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = ClientError(
        error_response={"Error": {"Code": "TooManyRequestsException", "Message": "slow down"}},
        operation_name="AdminInitiateAuth",
    )
    fake = _FakeClient(
        list_users_returns=[{"Username": "alice@example.com"}],
        auth_raises=err,
    )
    _patch_boto3(monkeypatch, fake)
    with pytest.raises(CognitoAdminError):
        verify_user_password(
            "sub-1",
            "ok",
            region="us-east-1",
            user_pool_id="pool",
            client_id="client",
        )


def test_verify_user_password_skips_secret_hash_when_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient(list_users_returns=[{"Username": "alice@example.com"}])
    _patch_boto3(monkeypatch, fake)
    verify_user_password(
        "sub-1",
        "pw",
        region="us-east-1",
        user_pool_id="pool",
        client_id="client",
        # No client_secret — public client config.
    )
    assert "SECRET_HASH" not in fake.auth_calls[0]["AuthParameters"]
