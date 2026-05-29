"""RBAC-2 role administration: GET /admin/users, POST/DELETE roles."""

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


async def _seed(sub: str, email: str, role: str) -> str:
    """Seed a user with a single role at global scope; return its local id."""
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        u = User(cognito_sub=sub, email=email)
        session.add(u)
        await session.flush()
        await UserRepository(session).grant_role(u.id, role)
        return u.id


async def test_admin_can_list_users_with_roles(client: AsyncClient) -> None:
    await _seed("sub-admin", "admin@example.com", "admin")
    await _seed("sub-student", "student@example.com", "student")

    client.cookies.set("cra_session", _cookie("sub-admin", "admin@example.com"))
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 200
    rows = resp.json()
    by_email = {r["email"]: r for r in rows}
    assert {a["role"] for a in by_email["student@example.com"]["role_assignments"]} == {"student"}
    assert {a["role"] for a in by_email["admin@example.com"]["role_assignments"]} == {"admin"}


async def test_non_admin_cannot_list_users(client: AsyncClient) -> None:
    await _seed("sub-r", "r@example.com", "researcher")
    client.cookies.set("cra_session", _cookie("sub-r", "r@example.com"))
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 403


async def test_admin_can_grant_and_revoke_role(client: AsyncClient) -> None:
    await _seed("sub-admin", "admin@example.com", "admin")
    target_id = await _seed("sub-t", "target@example.com", "researcher")

    client.cookies.set("cra_session", _cookie("sub-admin", "admin@example.com"))
    grant = await client.post(
        f"/api/admin/users/{target_id}/roles",
        json={"role": "study_designer", "scope_type": "global"},
    )
    assert grant.status_code == 201, grant.text
    aid = grant.json()["id"]

    # Verify it landed in the list.
    listing = await client.get("/api/admin/users")
    target_row = next(r for r in listing.json() if r["id"] == target_id)
    assert {a["role"] for a in target_row["role_assignments"]} >= {"study_designer"}

    # Revoke it.
    rev = await client.delete(f"/api/admin/users/{target_id}/roles/{aid}")
    assert rev.status_code == 204
    listing2 = await client.get("/api/admin/users")
    target_row2 = next(r for r in listing2.json() if r["id"] == target_id)
    assert "study_designer" not in {a["role"] for a in target_row2["role_assignments"]}


async def test_grant_rejects_unknown_role(client: AsyncClient) -> None:
    await _seed("sub-admin", "admin@example.com", "admin")
    target_id = await _seed("sub-t", "target@example.com", "researcher")
    client.cookies.set("cra_session", _cookie("sub-admin", "admin@example.com"))
    resp = await client.post(
        f"/api/admin/users/{target_id}/roles",
        json={"role": "ghost", "scope_type": "global"},
    )
    assert resp.status_code == 422
    assert "Unknown role" in resp.json()["detail"]


async def test_grant_at_study_scope_requires_scope_id(client: AsyncClient) -> None:
    await _seed("sub-admin", "admin@example.com", "admin")
    target_id = await _seed("sub-t", "target@example.com", "researcher")
    client.cookies.set("cra_session", _cookie("sub-admin", "admin@example.com"))
    resp = await client.post(
        f"/api/admin/users/{target_id}/roles",
        json={"role": "data_manager", "scope_type": "study"},
    )
    assert resp.status_code == 422


async def test_revoke_with_wrong_user_id_is_404(client: AsyncClient) -> None:
    """The assignment exists, but URL ties it to a different user_id —
    must 404 so a malformed admin URL can't accidentally revoke the wrong
    user's grant."""
    await _seed("sub-admin", "admin@example.com", "admin")
    other_id = await _seed("sub-other", "other@example.com", "researcher")
    target_id = await _seed("sub-t", "target@example.com", "researcher")

    client.cookies.set("cra_session", _cookie("sub-admin", "admin@example.com"))
    grant = await client.post(
        f"/api/admin/users/{target_id}/roles",
        json={"role": "auditor", "scope_type": "global"},
    )
    aid = grant.json()["id"]

    bad = await client.delete(f"/api/admin/users/{other_id}/roles/{aid}")
    assert bad.status_code == 404
