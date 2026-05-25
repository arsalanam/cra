"""Integration tests for /auth/* endpoints.

Covers the two cases the app distinguishes:
  • auth disabled (no Cognito configured) — /auth/me returns the
    default-user placeholder; /auth/login returns 503.
  • auth enabled (Cognito vars set) — /auth/login redirects to the
    hosted UI with the right params; /auth/me requires a session cookie.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_assistant.persistence.database import reset_engine

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    reset_engine()


async def _client(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> AsyncClient:
    """Build a TestClient against a fresh app with the given env."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from research_assistant.persistence.database import init_db
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Auth disabled (no Cognito) ─────────────────────────────────────────────


@pytest.fixture
async def disabled_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """Cognito unset → auth disabled."""
    client = await _client(
        monkeypatch,
        {
            "COGNITO_USER_POOL_ID": "",
            "COGNITO_CLIENT_ID": "",
        },
    )
    async with client as c:
        yield c
    from research_assistant.persistence.database import reset_engine

    reset_engine()


async def test_me_returns_placeholder_when_auth_disabled(
    disabled_client: AsyncClient,
) -> None:
    """current_user short-circuits to the default-user placeholder."""
    resp = await disabled_client.get("/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sub"] == "default-user"
    assert body["email"] == "dev@local"


async def test_login_503_when_auth_disabled(disabled_client: AsyncClient) -> None:
    resp = await disabled_client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


# ── Auth enabled ──────────────────────────────────────────────────────────


_COGNITO_ENV = {
    "COGNITO_REGION": "us-east-1",
    "COGNITO_USER_POOL_ID": "us-east-1_TESTPOOL",
    "COGNITO_CLIENT_ID": "test-client-id",
    "COGNITO_CLIENT_SECRET": "test-client-secret",
    "COGNITO_DOMAIN": "https://cra-test.auth.us-east-1.amazoncognito.com",
    "COGNITO_REDIRECT_URI": "http://localhost:8000/auth/callback",
    "SESSION_COOKIE_SECRET": "test-session-secret-must-be-long-enough-for-itsdangerous",
}


@pytest.fixture
async def enabled_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    client = await _client(monkeypatch, _COGNITO_ENV)
    async with client as c:
        yield c
    from research_assistant.persistence.database import reset_engine

    reset_engine()


async def test_login_redirects_to_cognito_hosted_ui(
    enabled_client: AsyncClient,
) -> None:
    resp = await enabled_client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "cra-test.auth.us-east-1.amazoncognito.com"
    assert parsed.path == "/oauth2/authorize"

    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["test-client-id"]
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == ["openid email profile"]
    assert qs["redirect_uri"] == ["http://localhost:8000/auth/callback"]
    assert len(qs["state"][0]) >= 16  # random state

    # State cookie set for the callback to verify
    cookies = resp.headers.get_list("set-cookie")
    assert any("cra_oauth_state=" in c for c in cookies)


async def test_me_returns_401_without_session(enabled_client: AsyncClient) -> None:
    resp = await enabled_client.get("/auth/me")
    assert resp.status_code == 401
    assert "login=" in resp.headers.get("www-authenticate", "")


async def test_callback_rejects_mismatched_state(enabled_client: AsyncClient) -> None:
    # No state cookie set → callback should refuse.
    resp = await enabled_client.get(
        "/auth/callback?code=anything&state=does-not-match",
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "state mismatch" in resp.json()["detail"].lower()


# ── Callback → first-login matcher (Phase C) ───────────────────────────────


def _stub_token_exchange(monkeypatch: pytest.MonkeyPatch, *, sub: str, email: str) -> None:
    """Make /auth/callback's token exchange + validation succeed without AWS."""
    from research_assistant.auth import IdentityClaims

    async def fake_validate(
        token: str,
        *,
        region: str,
        user_pool_id: str,
        client_id: str,
        access_token: str | None = None,
    ) -> IdentityClaims:
        return IdentityClaims(sub=sub, email=email, expires_at=9_999_999_999)

    class _Resp:
        status_code = 200
        text = ""

        def json(self) -> dict[str, str]:
            return {"id_token": "idtok", "access_token": "acctok"}

    class _Client:
        def __init__(self, *a: object, **k: object) -> None: ...
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def post(self, *a: object, **k: object) -> _Resp:
            return _Resp()

    monkeypatch.setattr("research_assistant.web.auth.validate_id_token", fake_validate)
    monkeypatch.setattr("research_assistant.web.auth.httpx.AsyncClient", _Client)


async def _drive_callback(client: AsyncClient, *, code: str = "abc") -> object:
    """Hit /auth/login to get a state cookie, then /auth/callback with that state."""
    login = await client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    return await client.get(f"/auth/callback?code={code}&state={state}", follow_redirects=False)


async def test_callback_rejects_uninvited_user(
    enabled_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_token_exchange(monkeypatch, sub="sub-stranger", email="stranger@example.com")
    resp = await _drive_callback(enabled_client)
    assert resp.status_code == 403
    assert "no invitation" in resp.json()["detail"].lower()


async def test_callback_provisions_invited_user(
    enabled_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        await UserRepository(session).create_invitation("invited@example.com", ["admin"])

    _stub_token_exchange(monkeypatch, sub="sub-invited", email="invited@example.com")
    resp = await _drive_callback(enabled_client)

    assert resp.status_code == 302  # logged in, redirected home
    async with get_db_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_sub("sub-invited")
        assert user is not None
        assert user.email == "invited@example.com"
        assert await repo.list_roles(user.id) == ["admin"]
