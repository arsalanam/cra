"""RBAC-3: thread ownership — a user cannot read or act on another user's thread.

We return 404 (not 403) for foreign threads so callers cannot probe
existence by id. Same shape as a real not-found.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

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
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_assistant.persistence.clinical.database import reset_clinical_engine
    from research_assistant.persistence.database import reset_engine

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("CLINICAL_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for k, v in _COGNITO_ENV.items():
        monkeypatch.setenv(k, v)
    reset_engine()
    reset_clinical_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from research_assistant.persistence.clinical.database import init_clinical_db
    from research_assistant.persistence.database import init_db
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    await init_clinical_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _cookie(sub: str, email: str) -> str:
    from research_assistant.auth.session import _serializer

    return _serializer(_SECRET).dumps(
        {"sub": sub, "email": email, "expires_at": int(time.time()) + 3600}
    )


async def _seed(sub: str, email: str, role: str = "researcher") -> None:
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        u = User(cognito_sub=sub, email=email)
        session.add(u)
        await session.flush()
        await UserRepository(session).grant_role(u.id, role)


async def test_list_threads_only_returns_callers_threads(client: AsyncClient) -> None:
    await _seed("sub-A", "a@example.com")
    await _seed("sub-B", "b@example.com")

    # A creates a thread.
    client.cookies.set("cra_session", _cookie("sub-A", "a@example.com"))
    await client.post("/api/threads", json={"title": "A's thread"})

    # B logs in — should see no threads.
    client.cookies.set("cra_session", _cookie("sub-B", "b@example.com"))
    listing = (await client.get("/api/threads")).json()
    assert listing == []


async def test_get_foreign_thread_is_404(client: AsyncClient) -> None:
    await _seed("sub-A", "a@example.com")
    await _seed("sub-B", "b@example.com")

    client.cookies.set("cra_session", _cookie("sub-A", "a@example.com"))
    tid = (await client.post("/api/threads", json={"title": "A's thread"})).json()["id"]

    client.cookies.set("cra_session", _cookie("sub-B", "b@example.com"))
    resp = await client.get(f"/api/threads/{tid}")
    assert resp.status_code == 404
    resp_msgs = await client.get(f"/api/threads/{tid}/messages")
    assert resp_msgs.status_code == 404
    resp_del = await client.delete(f"/api/threads/{tid}")
    assert resp_del.status_code == 404


async def test_owner_can_still_see_their_thread(client: AsyncClient) -> None:
    await _seed("sub-A", "a@example.com")
    client.cookies.set("cra_session", _cookie("sub-A", "a@example.com"))
    tid = (await client.post("/api/threads", json={"title": "Mine"})).json()["id"]
    got = await client.get(f"/api/threads/{tid}")
    assert got.status_code == 200
    assert got.json()["id"] == tid
