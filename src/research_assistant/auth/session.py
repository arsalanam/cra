"""Signed session-cookie helpers.

We don't re-validate the Cognito JWT on every request — that would
require a JWKS lookup (cached, but still O(crypto)) per call. Instead,
after `/auth/callback` validates the ID token once, we tuck a small
payload (sub + email + expiry) into a signed cookie. Subsequent requests
read the signed cookie; tampering invalidates the signature; expiry is
enforced by `itsdangerous`'s `max_age`.

The cookie is HTTP-only + SameSite=Lax, so it's not reachable from JS and
not auto-sent on cross-site requests (CSRF defence).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)


_COOKIE_NAME: Final = "cra_session"
_SALT: Final = "cra-session-v1"


@dataclass(frozen=True)
class SessionPayload:
    """Identity material stashed in the signed session cookie."""

    sub: str
    email: str
    # Epoch seconds of the underlying Cognito ID token's exp claim. We
    # also pass this as `max_age` to itsdangerous so the cookie can't
    # outlive the token even if the user clears nothing.
    expires_at: int


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=secret, salt=_SALT)


def write_session(
    response: Response,
    *,
    payload: SessionPayload,
    secret: str,
    secure: bool,
) -> None:
    """Set the signed session cookie on the outgoing response."""
    serializer = _serializer(secret)
    encoded = serializer.dumps({
        "sub": payload.sub,
        "email": payload.email,
        "expires_at": payload.expires_at,
    })
    # The cookie max_age tracks the Cognito ID-token lifetime in seconds.
    # We pass it both here and to itsdangerous.loads() in read_session.
    import time
    max_age = max(0, payload.expires_at - int(time.time()))
    response.set_cookie(
        key=_COOKIE_NAME,
        value=encoded,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def read_session(request: Request, *, secret: str) -> SessionPayload | None:
    """Return the validated session payload, or None if absent / invalid / expired."""
    raw = request.cookies.get(_COOKIE_NAME)
    if not raw:
        return None
    serializer = _serializer(secret)
    try:
        # max_age guards against a stolen-but-old cookie even if exp wasn't
        # baked into the payload. 24h hard ceiling on session age.
        data = serializer.loads(raw, max_age=24 * 3600)
    except SignatureExpired:
        logger.debug("Session cookie expired")
        return None
    except BadSignature:
        logger.warning("Session cookie failed signature check (tampered?)")
        return None
    try:
        return SessionPayload(
            sub=str(data["sub"]),
            email=str(data["email"]),
            expires_at=int(data["expires_at"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Session cookie payload malformed")
        return None


def clear_session(response: Response) -> None:
    """Delete the session cookie on the outgoing response."""
    response.delete_cookie(key=_COOKIE_NAME, path="/")
