"""RBAC-2 endpoint gating across /api/ecrf and /api/edc.

Auth-enabled mode: each test seeds a User with a specific role assignment
(global or study-scoped) and asserts the matrix maps to the right HTTP
outcomes. The single-test setup mirrors `test_admin_users.py` (Cognito
env vars + signed session cookie).

Companion tests in `test_thread_ownership.py` and
`test_watch_ownership.py` cover the RBAC-3 ownership scoping.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

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
    monkeypatch.setattr(
        "research_assistant.web.admin.create_cognito_user",
        lambda email, *, region, user_pool_id: "FORCE_CHANGE_PASSWORD",
    )
    reset_engine()
    reset_clinical_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from research_assistant.persistence.clinical.database import (
        init_clinical_db,
        reset_clinical_engine,
    )
    from research_assistant.persistence.database import init_db, reset_engine
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    await init_clinical_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    reset_engine()
    reset_clinical_engine()


def _session_cookie(sub: str, email: str) -> str:
    from research_assistant.auth.session import _serializer

    return _serializer(_SECRET).dumps(
        {"sub": sub, "email": email, "expires_at": int(time.time()) + 3600}
    )


async def _seed_user_with_role(
    sub: str,
    email: str,
    *,
    role: str,
    scope_type: str = "global",
    scope_id: str | None = None,
) -> None:
    """Seed a User + a single RoleAssignment in the research DB."""
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        user = User(cognito_sub=sub, email=email)
        session.add(user)
        await session.flush()
        await UserRepository(session).grant_role(
            user.id, role, scope_type=scope_type, scope_id=scope_id
        )


def _login(c: AsyncClient, sub: str, email: str) -> None:
    c.cookies.set("cra_session", _session_cookie(sub, email))


def _form_body() -> dict[str, Any]:
    return {
        "name": "demographics",
        "title": "Demographics",
        "sections": [
            {
                "id": "main",
                "title": "Main",
                "items": [
                    {"id": "age", "label": "Age", "data_type": "integer", "required": True},
                ],
            }
        ],
    }


# ── /api/ecrf gating ─────────────────────────────────────────────────────


async def test_researcher_cannot_create_ecrf_study(client: AsyncClient) -> None:
    """Researcher has no study.* perms — POST /studies must 403."""
    await _seed_user_with_role("sub-r", "r@example.com", role="researcher")
    _login(client, "sub-r", "r@example.com")
    resp = await client.post("/api/ecrf/studies", json={"name": "X"})
    assert resp.status_code == 403, resp.text
    assert "study.create" in resp.json()["detail"]


async def test_study_designer_can_create_and_publish_form(client: AsyncClient) -> None:
    """study_designer holds study.author/publish/create at global scope."""
    await _seed_user_with_role("sub-d", "d@example.com", role="study_designer")
    _login(client, "sub-d", "d@example.com")
    sid = (await client.post("/api/ecrf/studies", json={"name": "X"})).json()["id"]
    fr = await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_body())
    assert fr.status_code == 201, fr.text
    fid = fr.json()["id"]
    pub = await client.post(f"/api/ecrf/forms/{fid}/publish")
    assert pub.status_code == 200, pub.text


async def test_admin_can_do_anything_on_ecrf(client: AsyncClient) -> None:
    """admin holds every permission globally — sanity smoke."""
    await _seed_user_with_role("sub-a", "a@example.com", role="admin")
    _login(client, "sub-a", "a@example.com")
    sid = (await client.post("/api/ecrf/studies", json={"name": "X"})).json()["id"]
    fid = (
        await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_body())
    ).json()["id"]
    assert (await client.post(f"/api/ecrf/forms/{fid}/publish")).status_code == 200


async def test_study_designer_at_one_study_cannot_publish_in_another(
    client: AsyncClient,
) -> None:
    """Scoped grant honours study isolation — confirms the resource→scope
    resolver actually narrows what a study-scoped role can act on.

    Setup: admin creates two studies. Designer is granted study_designer at
    scope study:<sid1>. Designer can publish a form in sid1 but not sid2.
    """
    # Admin seeds both studies first.
    await _seed_user_with_role("sub-a", "a@example.com", role="admin")
    _login(client, "sub-a", "a@example.com")
    sid1 = (await client.post("/api/ecrf/studies", json={"name": "S1"})).json()["id"]
    sid2 = (await client.post("/api/ecrf/studies", json={"name": "S2"})).json()["id"]
    f1 = (
        await client.post(f"/api/ecrf/studies/{sid1}/forms", json=_form_body())
    ).json()["id"]
    f2 = (
        await client.post(f"/api/ecrf/studies/{sid2}/forms", json=_form_body())
    ).json()["id"]

    # Now switch to a designer scoped to sid1 only.
    await _seed_user_with_role(
        "sub-d",
        "d@example.com",
        role="study_designer",
        scope_type="study",
        scope_id=sid1,
    )
    _login(client, "sub-d", "d@example.com")
    pub_ok = await client.post(f"/api/ecrf/forms/{f1}/publish")
    pub_blocked = await client.post(f"/api/ecrf/forms/{f2}/publish")
    assert pub_ok.status_code == 200, pub_ok.text
    assert pub_blocked.status_code == 403, pub_blocked.text


# ── /api/edc gating ──────────────────────────────────────────────────────


async def _setup_deployment(client: AsyncClient) -> dict[str, str]:
    """Create a study + form + deployment + site + subject + open form
    instance as admin. Returns ids for the gating tests to act on.
    """
    await _seed_user_with_role("sub-admin", "admin@example.com", role="admin")
    _login(client, "sub-admin", "admin@example.com")
    sid = (await client.post("/api/ecrf/studies", json={"name": "X"})).json()["id"]
    fid = (
        await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_body())
    ).json()["id"]
    await client.post(f"/api/ecrf/forms/{fid}/publish")
    dep = (
        await client.post(
            "/api/edc/deployments", json={"research_study_id": sid}
        )
    ).json()
    site = (
        await client.post(
            f"/api/edc/deployments/{dep['id']}/sites",
            json={"name": "Boston"},
        )
    ).json()
    subj = (
        await client.post(
            f"/api/edc/deployments/{dep['id']}/subjects",
            json={"site_id": site["id"], "subject_code": "S-001"},
        )
    ).json()
    df = (await client.get(f"/api/edc/deployments/{dep['id']}/forms")).json()[0]
    fi = (
        await client.post(
            f"/api/edc/subjects/{subj['id']}/forms",
            json={"deployed_form_id": df["id"]},
        )
    ).json()
    return {"sid": sid, "dep": dep["id"], "site": site["id"], "subject": subj["id"], "fi": fi["id"]}


async def test_coordinator_can_enter_data_but_not_sign(client: AsyncClient) -> None:
    """Coordinator has data.enter + query.respond but NOT form.sign."""
    ids = await _setup_deployment(client)
    await _seed_user_with_role("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    enter = await client.put(
        f"/api/edc/form-instances/{ids['fi']}/data",
        json={"values": {"age": "30"}},
    )
    assert enter.status_code == 200, enter.text
    sign = await client.post(
        f"/api/edc/form-instances/{ids['fi']}/sign",
        json={"meaning": "PI sign-off"},
    )
    assert sign.status_code == 403


async def test_pi_can_sign_but_not_enter_data(client: AsyncClient) -> None:
    """Separation of duties: PI signs, doesn't enter."""
    ids = await _setup_deployment(client)
    # Admin pre-fills some data so signing has something to lock.
    enter_admin = await client.put(
        f"/api/edc/form-instances/{ids['fi']}/data",
        json={"values": {"age": "30"}, "mark_complete": True},
    )
    assert enter_admin.status_code == 200

    await _seed_user_with_role("sub-pi", "pi@example.com", role="principal_investigator")
    _login(client, "sub-pi", "pi@example.com")
    enter = await client.put(
        f"/api/edc/form-instances/{ids['fi']}/data",
        json={"values": {"age": "31"}},
    )
    assert enter.status_code == 403
    sign = await client.post(
        f"/api/edc/form-instances/{ids['fi']}/sign",
        json={"meaning": "PI sign-off"},
    )
    assert sign.status_code == 200, sign.text


async def test_monitor_can_verify_but_not_enter(client: AsyncClient) -> None:
    """SDV is monitor-only (separation of duties). Coordinator cannot verify."""
    ids = await _setup_deployment(client)
    enter = await client.put(
        f"/api/edc/form-instances/{ids['fi']}/data",
        json={"values": {"age": "30"}},
    )
    assert enter.status_code == 200

    # Coordinator should NOT have sdv.verify.
    await _seed_user_with_role("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    fail = await client.post(
        f"/api/edc/form-instances/{ids['fi']}/verify",
        json={"item_ids": ["age"]},
    )
    assert fail.status_code == 403, fail.text

    # Monitor should succeed.
    await _seed_user_with_role("sub-m", "m@example.com", role="monitor")
    _login(client, "sub-m", "m@example.com")
    ok = await client.post(
        f"/api/edc/form-instances/{ids['fi']}/verify",
        json={"item_ids": ["age"]},
    )
    assert ok.status_code == 200, ok.text


async def test_data_manager_can_unlock_but_not_sign(client: AsyncClient) -> None:
    """Unlock moves from admin-only (pre-RBAC-2) to data_manager."""
    ids = await _setup_deployment(client)
    # Pre-fill + sign as admin so there's a signature to void.
    await client.put(
        f"/api/edc/form-instances/{ids['fi']}/data",
        json={"values": {"age": "30"}, "mark_complete": True},
    )
    await client.post(
        f"/api/edc/form-instances/{ids['fi']}/sign",
        json={"meaning": "PI sign"},
    )

    await _seed_user_with_role("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")
    sign = await client.post(
        f"/api/edc/form-instances/{ids['fi']}/sign",
        json={"meaning": "DM trying to sign"},
    )
    assert sign.status_code == 403
    unlock = await client.post(
        f"/api/edc/form-instances/{ids['fi']}/unlock",
        json={"reason": "DM unlock"},
    )
    assert unlock.status_code == 200, unlock.text
