"""Unit tests for auth.jwks + auth.tokens.

Generates a real RSA keypair per test, signs mock Cognito ID tokens with
it, and seeds the JWKS cache directly so no HTTP is involved.
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from research_assistant.auth.jwks import get_jwks_cache, reset_jwks_cache_for_testing
from research_assistant.auth.tokens import TokenValidationError, validate_id_token

REGION = "us-east-1"
POOL_ID = "us-east-1_testpool"
CLIENT_ID = "test-app-client"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
KID = "test-key-1"


def _make_keypair() -> tuple[str, dict[str, Any]]:
    """Generate an RSA keypair, return (PEM private key, JWK public key)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_jwk = jwk.construct(public_pem, "RS256").to_dict()
    pub_jwk["kid"] = KID
    pub_jwk["alg"] = "RS256"
    pub_jwk["use"] = "sig"
    return pem, pub_jwk


def _sign(
    private_pem: str,
    *,
    aud: str = CLIENT_ID,
    iss: str = ISSUER,
    exp_offset: int = 600,
    iat_offset: int = -1,
    sub: str = "user-abc",
    email: str = "user@example.com",
    extra: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": sub,
        "email": email,
        "aud": aud,
        "iss": iss,
        "iat": now + iat_offset,
        "exp": now + exp_offset,
        "token_use": "id",
    }
    if extra:
        claims.update(extra)
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_jwks_cache_for_testing()


async def test_valid_token_accepted() -> None:
    pem, pub_jwk = _make_keypair()
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([pub_jwk])
    token = _sign(pem, sub="u1", email="alice@example.com")

    claims = await validate_id_token(
        token,
        region=REGION,
        user_pool_id=POOL_ID,
        client_id=CLIENT_ID,
    )
    assert claims.sub == "u1"
    assert claims.email == "alice@example.com"
    assert claims.expires_at > int(time.time())


async def test_expired_token_rejected() -> None:
    pem, pub_jwk = _make_keypair()
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([pub_jwk])
    token = _sign(pem, exp_offset=-30, iat_offset=-60)

    with pytest.raises(TokenValidationError, match="expired"):
        await validate_id_token(
            token,
            region=REGION,
            user_pool_id=POOL_ID,
            client_id=CLIENT_ID,
        )


async def test_wrong_audience_rejected() -> None:
    pem, pub_jwk = _make_keypair()
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([pub_jwk])
    token = _sign(pem, aud="someone-elses-client-id")

    with pytest.raises(TokenValidationError, match="claim check failed"):
        await validate_id_token(
            token,
            region=REGION,
            user_pool_id=POOL_ID,
            client_id=CLIENT_ID,
        )


async def test_wrong_issuer_rejected() -> None:
    pem, pub_jwk = _make_keypair()
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([pub_jwk])
    token = _sign(pem, iss="https://example.com/some-other-pool")

    with pytest.raises(TokenValidationError, match="claim check failed"):
        await validate_id_token(
            token,
            region=REGION,
            user_pool_id=POOL_ID,
            client_id=CLIENT_ID,
        )


async def test_signed_with_unknown_key_rejected() -> None:
    _real_pem, real_pub_jwk = _make_keypair()
    rogue_pem, _ = _make_keypair()
    # Cache the REAL pub key only; sign with the ROGUE private key but
    # claim the same kid.
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([real_pub_jwk])
    token = _sign(rogue_pem)

    with pytest.raises(TokenValidationError, match="signature"):
        await validate_id_token(
            token,
            region=REGION,
            user_pool_id=POOL_ID,
            client_id=CLIENT_ID,
        )


async def test_missing_kid_rejected() -> None:
    pem, _ = _make_keypair()
    # Hand-craft a token with no kid in the header.
    token = jwt.encode(
        {
            "sub": "u1",
            "email": "a@b.c",
            "aud": CLIENT_ID,
            "iss": ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        pem,
        algorithm="RS256",
    )
    with pytest.raises(TokenValidationError, match="kid"):
        await validate_id_token(
            token,
            region=REGION,
            user_pool_id=POOL_ID,
            client_id=CLIENT_ID,
        )


async def test_token_missing_email_claim_rejected() -> None:
    pem, pub_jwk = _make_keypair()
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([pub_jwk])
    # Sign a token that has sub but no email.
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "u1",
            "aud": CLIENT_ID,
            "iss": ISSUER,
            "iat": now,
            "exp": now + 600,
            "token_use": "id",
        },
        pem,
        algorithm="RS256",
        headers={"kid": KID},
    )
    with pytest.raises(TokenValidationError, match="required claims"):
        await validate_id_token(
            token,
            region=REGION,
            user_pool_id=POOL_ID,
            client_id=CLIENT_ID,
        )


# ── at_hash (regression for the 2026-05-23 login bug) ──────────────────────
# Real Cognito code-flow ID tokens carry an `at_hash` claim; python-jose
# refuses to validate it unless the access token is passed in. These guard
# against the bug where /auth/callback validated the ID token without the
# access token, breaking every real login with "No access_token provided
# to compare against at_hash claim."


def _at_hash(access_token: str) -> str:
    """OIDC at_hash: base64url(left half of SHA-256(access_token)), no padding."""
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=").decode("ascii")


async def test_at_hash_validated_when_access_token_supplied() -> None:
    pem, pub_jwk = _make_keypair()
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([pub_jwk])
    token = _sign(pem, sub="u1", extra={"at_hash": _at_hash("the-access-token")})

    claims = await validate_id_token(
        token,
        region=REGION,
        user_pool_id=POOL_ID,
        client_id=CLIENT_ID,
        access_token="the-access-token",
    )
    assert claims.sub == "u1"


async def test_at_hash_present_but_no_access_token_rejected() -> None:
    """The exact production bug: at_hash claim + no access token → rejected."""
    pem, pub_jwk = _make_keypair()
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([pub_jwk])
    token = _sign(pem, extra={"at_hash": _at_hash("the-access-token")})

    with pytest.raises(TokenValidationError, match="claim check failed"):
        await validate_id_token(
            token,
            region=REGION,
            user_pool_id=POOL_ID,
            client_id=CLIENT_ID,
        )


async def test_at_hash_mismatch_rejected() -> None:
    pem, pub_jwk = _make_keypair()
    get_jwks_cache(REGION, POOL_ID).seed_for_testing([pub_jwk])
    token = _sign(pem, extra={"at_hash": _at_hash("the-access-token")})

    with pytest.raises(TokenValidationError, match="claim check failed"):
        await validate_id_token(
            token,
            region=REGION,
            user_pool_id=POOL_ID,
            client_id=CLIENT_ID,
            access_token="a-different-access-token",
        )
