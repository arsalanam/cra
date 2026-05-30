"""End-to-end gating for the study-level lock (eCRF E7 — validation pack)."""

from __future__ import annotations

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

    # Bypass signing-reauth so this test focuses on the lock gates.
    async def _noop(_sub: str, _password: str) -> None:
        return None

    monkeypatch.setattr("research_assistant.web.edc._require_signing_reauth", _noop)
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


async def _seed_user(sub: str, email: str, *, role: str) -> None:
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        user = User(cognito_sub=sub, email=email)
        session.add(user)
        await session.flush()
        await UserRepository(session).grant_role(
            user.id, role, scope_type="global", scope_id=None
        )


def _session_cookie(sub: str, email: str) -> str:
    import time as _time

    from research_assistant.auth.session import _serializer

    return _serializer(_SECRET).dumps(
        {"sub": sub, "email": email, "expires_at": int(_time.time()) + 3600}
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


async def _setup_deployment(client: AsyncClient) -> dict[str, str]:
    # admin role: creates the deployment + form + subject.
    await _seed_user("sub-admin", "admin@example.com", role="admin")
    _login(client, "sub-admin", "admin@example.com")

    study = await client.post("/api/ecrf/studies", json={"name": "Lock Test"})
    assert study.status_code == 201, study.text
    study_id = study.json()["id"]

    form = await client.post(
        f"/api/ecrf/studies/{study_id}/forms", json=_form_body()
    )
    assert form.status_code == 201, form.text
    pub = await client.post(f"/api/ecrf/forms/{form.json()['id']}/publish")
    assert pub.status_code == 200, pub.text

    dep = await client.post(
        "/api/edc/deployments",
        json={"research_study_id": study_id, "name": "Lock Dep"},
    )
    assert dep.status_code == 201, dep.text
    dep_id = dep.json()["id"]

    site = await client.post(
        f"/api/edc/deployments/{dep_id}/sites", json={"name": "Site A"}
    )
    assert site.status_code == 201, site.text

    subj = await client.post(
        f"/api/edc/deployments/{dep_id}/subjects",
        json={"site_id": site.json()["id"], "subject_code": "S-001"},
    )
    assert subj.status_code == 201, subj.text
    subj_id = subj.json()["id"]

    forms = await client.get(f"/api/edc/deployments/{dep_id}/forms")
    deployed_id = forms.json()[0]["id"]

    fi = await client.post(
        f"/api/edc/subjects/{subj_id}/forms",
        json={"deployed_form_id": deployed_id},
    )
    assert fi.status_code == 201, fi.text
    return {"dep": dep_id, "subj": subj_id, "fi": fi.json()["id"]}


# ── tests ────────────────────────────────────────────────────────────────


async def test_data_manager_can_lock_unlock(client: AsyncClient) -> None:
    ids = await _setup_deployment(client)
    await _seed_user("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")

    status = await client.get(f"/api/edc/deployments/{ids['dep']}/lock-status")
    assert status.status_code == 200
    assert status.json()["locked"] is False

    lock = await client.post(
        f"/api/edc/deployments/{ids['dep']}/lock",
        json={"reason": "interim analysis"},
    )
    assert lock.status_code == 201, lock.text

    status2 = await client.get(f"/api/edc/deployments/{ids['dep']}/lock-status")
    assert status2.json()["locked"] is True

    unlock = await client.post(
        f"/api/edc/deployments/{ids['dep']}/unlock",
        json={"reason": "resume"},
    )
    assert unlock.status_code == 200, unlock.text


async def test_coordinator_cannot_lock(client: AsyncClient) -> None:
    ids = await _setup_deployment(client)
    await _seed_user("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")

    lock = await client.post(
        f"/api/edc/deployments/{ids['dep']}/lock",
        json={"reason": "should fail"},
    )
    assert lock.status_code == 403


async def test_locked_study_refuses_submit_data(client: AsyncClient) -> None:
    ids = await _setup_deployment(client)
    # Lock as DM.
    await _seed_user("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")
    await client.post(
        f"/api/edc/deployments/{ids['dep']}/lock", json={"reason": "lock"}
    )

    # Try to submit as admin (who otherwise has data.enter): should 409.
    _login(client, "sub-admin", "admin@example.com")
    resp = await client.put(
        f"/api/edc/form-instances/{ids['fi']}/data",
        json={"values": {"age": "30"}, "mark_complete": True},
    )
    assert resp.status_code == 409
    assert "locked" in resp.text.lower()


async def test_locked_study_refuses_signing(client: AsyncClient) -> None:
    ids = await _setup_deployment(client)
    # Pre-fill data so the form is complete.
    await client.put(
        f"/api/edc/form-instances/{ids['fi']}/data",
        json={"values": {"age": "30"}, "mark_complete": True},
    )
    # Lock.
    await _seed_user("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")
    await client.post(
        f"/api/edc/deployments/{ids['dep']}/lock", json={"reason": "lock"}
    )

    # Try to sign as admin.
    _login(client, "sub-admin", "admin@example.com")
    sign = await client.post(
        f"/api/edc/form-instances/{ids['fi']}/sign",
        json={"meaning": "PI sign", "password": ""},
    )
    assert sign.status_code == 409


async def test_locked_study_refuses_sdv_verify(client: AsyncClient) -> None:
    ids = await _setup_deployment(client)
    await client.put(
        f"/api/edc/form-instances/{ids['fi']}/data",
        json={"values": {"age": "30"}},
    )
    # Lock.
    await _seed_user("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")
    await client.post(
        f"/api/edc/deployments/{ids['dep']}/lock", json={"reason": "lock"}
    )

    # Monitor tries SDV after the lock.
    await _seed_user("sub-m", "m@example.com", role="monitor")
    _login(client, "sub-m", "m@example.com")
    verify = await client.post(
        f"/api/edc/form-instances/{ids['fi']}/verify",
        json={"item_ids": ["age"]},
    )
    assert verify.status_code == 409
