"""Phase C/D: POST /api/admin/users invitation endpoint + admin role gate.

Cognito's AdminCreateUser is monkeypatched — these tests cover the HTTP
contract, the role gate, and that a pending_invitation is persisted, not
the AWS call.
"""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

_SECRET = "test-session-secret-must-be-long-enough-for-itsdangerous"
_COGNITO_ENV = {
    "COGNITO_REGION": "us-east-1",
    "COGNITO_USER_POOL_ID": "us-east-1_TESTPOOL",
    "COGNITO_CLIENT_ID": "test-client-id",
    "COGNITO_CLIENT_SECRET": "test-client-secret",
    "COGNITO_DOMAIN": "https://cra-test.auth.us-east-1.amazoncognito.com",
    "COGNITO_REDIRECT_URI": "http://localhost:8000/auth/callback",
    "SESSION_COOKIE_SECRET": _SECRET,
}


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_assistant.persistence.database import reset_engine

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for k, v in _COGNITO_ENV.items():
        monkeypatch.setenv(k, v)
    # No real AWS calls — stub the Cognito provisioning.
    monkeypatch.setattr(
        "research_assistant.web.admin.create_cognito_user",
        lambda email, *, region, user_pool_id: "FORCE_CHANGE_PASSWORD",
    )
    reset_engine()


async def _client() -> AsyncClient:
    from research_assistant.persistence.database import init_db
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _session_cookie(sub: str, email: str) -> str:
    from research_assistant.auth.session import _serializer

    return _serializer(_SECRET).dumps(
        {"sub": sub, "email": email, "expires_at": int(time.time()) + 3600}
    )


async def _seed_user(sub: str, email: str, *, role: str | None) -> None:
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User, UserRole

    async with get_db_session() as session:
        user = User(cognito_sub=sub, email=email)
        session.add(user)
        await session.flush()
        if role is not None:
            session.add(UserRole(user_id=user.id, role=role))


async def test_invite_user_records_pending_invitation() -> None:
    from research_assistant.persistence.database import get_db_session, reset_engine
    from research_assistant.persistence.models import PendingInvitation

    client = await _client()
    await _seed_user("admin-sub", "admin@example.com", role="admin")
    async with client as c:
        c.cookies.set("cra_session", _session_cookie("admin-sub", "admin@example.com"))
        resp = await c.post(
            "/api/admin/users",
            json={"email": "Invitee@Example.com", "roles": ["admin"]},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "invitee@example.com"  # normalized to lowercase
    assert body["roles"] == ["admin"]
    assert body["cognito_status"] == "FORCE_CHANGE_PASSWORD"

    async with get_db_session() as session:
        inv = (
            await session.execute(
                select(PendingInvitation).where(
                    PendingInvitation.email == "invitee@example.com"
                )
            )
        ).scalar_one()
        assert inv.consumed_at is None
    reset_engine()


async def test_invite_user_rejects_invalid_email() -> None:
    from research_assistant.persistence.database import reset_engine

    client = await _client()
    await _seed_user("admin-sub", "admin@example.com", role="admin")
    async with client as c:
        c.cookies.set("cra_session", _session_cookie("admin-sub", "admin@example.com"))
        resp = await c.post("/api/admin/users", json={"email": "not-an-email"})
    assert resp.status_code == 422
    reset_engine()


async def test_invite_requires_authentication() -> None:
    from research_assistant.persistence.database import reset_engine

    client = await _client()
    async with client as c:  # no session cookie
        resp = await c.post("/api/admin/users", json={"email": "x@example.com"})
    assert resp.status_code == 401
    reset_engine()


async def test_invite_requires_admin_role() -> None:
    from research_assistant.persistence.database import reset_engine

    client = await _client()
    await _seed_user("plain-sub", "plain@example.com", role="researcher")
    async with client as c:
        c.cookies.set("cra_session", _session_cookie("plain-sub", "plain@example.com"))
        resp = await c.post("/api/admin/users", json={"email": "x@example.com"})
    assert resp.status_code == 403
    reset_engine()
