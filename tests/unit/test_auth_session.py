"""Unit tests for the signed session-cookie helpers."""

from __future__ import annotations

import time

import pytest
from fastapi import Request, Response

from research_assistant.auth.session import (
    SessionPayload,
    clear_session,
    read_session,
    write_session,
)

SECRET = "test-cookie-secret-do-not-use-in-prod-but-long-enough"


def _request_with_cookies(cookies: dict[str, str]) -> Request:
    """Minimal Request stub that exposes the .cookies attribute."""
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", cookie_str.encode())] if cookie_str else [],
    }
    return Request(scope)


def _payload(expires_in: int = 600) -> SessionPayload:
    return SessionPayload(
        sub="user-xyz",
        email="user@example.com",
        expires_at=int(time.time()) + expires_in,
    )


def test_write_and_read_roundtrip() -> None:
    resp = Response()
    write_session(resp, payload=_payload(), secret=SECRET, secure=False)

    cookie_header = resp.headers.get("set-cookie") or ""
    assert "cra_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    # Pull the cookie value back out
    cookie_value = cookie_header.split("cra_session=", 1)[1].split(";", 1)[0]

    req = _request_with_cookies({"cra_session": cookie_value})
    out = read_session(req, secret=SECRET)
    assert out is not None
    assert out.sub == "user-xyz"
    assert out.email == "user@example.com"


def test_read_returns_none_when_no_cookie() -> None:
    req = _request_with_cookies({})
    assert read_session(req, secret=SECRET) is None


def test_tampered_cookie_rejected() -> None:
    resp = Response()
    write_session(resp, payload=_payload(), secret=SECRET, secure=False)
    cookie_value = resp.headers["set-cookie"].split("cra_session=", 1)[1].split(";", 1)[0]
    # Flip a character in the middle to break the signature.
    tampered = cookie_value[:-3] + ("AAA" if not cookie_value.endswith("AAA") else "BBB")

    req = _request_with_cookies({"cra_session": tampered})
    assert read_session(req, secret=SECRET) is None


def test_wrong_secret_rejects_cookie() -> None:
    resp = Response()
    write_session(resp, payload=_payload(), secret=SECRET, secure=False)
    cookie_value = resp.headers["set-cookie"].split("cra_session=", 1)[1].split(";", 1)[0]

    req = _request_with_cookies({"cra_session": cookie_value})
    assert read_session(req, secret="a-completely-different-secret") is None


def test_clear_session_sets_deletion_header() -> None:
    resp = Response()
    clear_session(resp)
    cookie_header = resp.headers.get("set-cookie") or ""
    assert "cra_session=" in cookie_header
    # FastAPI's delete_cookie emits Max-Age=0 (and/or an expired date).
    assert "Max-Age=0" in cookie_header or "expires=" in cookie_header.lower()


def test_secure_flag_set_only_when_requested() -> None:
    """The Secure flag should NOT appear for http://localhost dev use."""
    resp = Response()
    write_session(resp, payload=_payload(), secret=SECRET, secure=False)
    assert "Secure" not in (resp.headers.get("set-cookie") or "")

    resp_https = Response()
    write_session(resp_https, payload=_payload(), secret=SECRET, secure=True)
    assert "Secure" in (resp_https.headers.get("set-cookie") or "")


def test_expired_payload_still_decodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cookie max_age guards against age; the payload's expires_at field
    is informational. Even an `expires_at` in the past round-trips
    (the caller can decide what to do with it)."""
    resp = Response()
    write_session(
        resp,
        payload=SessionPayload(sub="u", email="e@x.com", expires_at=0),
        secret=SECRET,
        secure=False,
    )
    cookie_value = resp.headers["set-cookie"].split("cra_session=", 1)[1].split(";", 1)[0]
    req = _request_with_cookies({"cra_session": cookie_value})
    out = read_session(req, secret=SECRET)
    assert out is not None
    assert out.expires_at == 0
