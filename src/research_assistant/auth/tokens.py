"""Cognito ID-token validation.

Validates the signature using the pool's JWKS, plus the three claims that
matter for authentication: `iss` (issued by our pool), `aud` (intended
for our app client), `exp` (not expired).

We use python-jose because it handles JWK → RSA-key conversion natively.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError

from .jwks import get_jwks_cache

logger = logging.getLogger(__name__)


class TokenValidationError(Exception):
    """Raised when an ID token fails any check (signature, claims, expiry)."""


@dataclass(frozen=True)
class IdentityClaims:
    """The subset of ID-token claims the rest of the app cares about."""

    sub: str  # stable Cognito user id; the key for our local User row
    email: str  # verified email (Cognito enforces this since email is the username)
    expires_at: int  # epoch seconds — used to time the session cookie

    @classmethod
    def from_claims(cls, claims: dict[str, object]) -> IdentityClaims:
        sub = str(claims.get("sub") or "")
        email = str(claims.get("email") or "")
        exp_raw = claims.get("exp")
        if not sub or not email or not isinstance(exp_raw, int | float):
            raise TokenValidationError("ID token missing required claims (sub / email / exp)")
        return cls(sub=sub, email=email, expires_at=int(exp_raw))


async def validate_id_token(
    token: str,
    *,
    region: str,
    user_pool_id: str,
    client_id: str,
    access_token: str | None = None,
) -> IdentityClaims:
    """Verify signature + standard claims; return typed claims on success.

    `access_token` is required to verify the ID token's `at_hash` claim,
    which Cognito always emits in the authorization-code flow. python-jose
    raises if the token carries `at_hash` but no access token is supplied
    to compare against. Pass the access token from the same /oauth2/token
    response so the ID↔access token binding is actually verified.
    """
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise TokenValidationError(f"malformed token header: {e}") from e

    kid = header.get("kid")
    if not kid:
        raise TokenValidationError("token header missing kid")

    cache = get_jwks_cache(region=region, user_pool_id=user_pool_id)
    try:
        jwk = await cache.get_key(kid)
    except KeyError as e:
        raise TokenValidationError(str(e)) from e

    try:
        claims = jwt.decode(
            token,
            key=jwk,
            algorithms=[header.get("alg", "RS256")],
            audience=client_id,
            issuer=issuer,
            access_token=access_token,
            options={"require_exp": True, "require_iat": True},
        )
    except ExpiredSignatureError as e:
        raise TokenValidationError("token expired") from e
    except JWTClaimsError as e:
        raise TokenValidationError(f"claim check failed: {e}") from e
    except JWTError as e:
        raise TokenValidationError(f"signature verification failed: {e}") from e

    return IdentityClaims.from_claims(claims)
