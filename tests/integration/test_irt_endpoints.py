"""IRT endpoints — schedule + allocate + code-break + RBAC gating."""

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
    from sqlalchemy import select

    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        if (
            await session.scalars(select(User).where(User.cognito_sub == sub))
        ).first() is not None:
            return
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


async def _setup_deployment_with_two_subjects(client: AsyncClient) -> dict[str, str]:
    await _seed_user("sub-admin", "admin@example.com", role="admin")
    _login(client, "sub-admin", "admin@example.com")
    study = await client.post("/api/ecrf/studies", json={"name": "IRT Trial"})
    study_id = study.json()["id"]
    form = await client.post(
        f"/api/ecrf/studies/{study_id}/forms", json=_form_body()
    )
    await client.post(f"/api/ecrf/forms/{form.json()['id']}/publish")
    dep = await client.post(
        "/api/edc/deployments",
        json={"research_study_id": study_id, "name": "IRT Dep"},
    )
    dep_id = dep.json()["id"]
    site = await client.post(
        f"/api/edc/deployments/{dep_id}/sites", json={"name": "S"}
    )
    site_id = site.json()["id"]
    s1 = await client.post(
        f"/api/edc/deployments/{dep_id}/subjects",
        json={"site_id": site_id, "subject_code": "S-001"},
    )
    s2 = await client.post(
        f"/api/edc/deployments/{dep_id}/subjects",
        json={"site_id": site_id, "subject_code": "S-002"},
    )
    return {"dep": dep_id, "s1": s1.json()["id"], "s2": s2.json()["id"]}


# ── Schedule generation ────────────────────────────────────────────────


async def test_data_manager_can_generate_schedule(client: AsyncClient) -> None:
    ids = await _setup_deployment_with_two_subjects(client)
    await _seed_user("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")
    resp = await client.post(
        f"/api/edc/deployments/{ids['dep']}/randomization/schedule",
        json={
            "algorithm": "permuted_block",
            "arms": ["Drug A", "Drug B"],
            "ratio": [1, 1],
            "block_sizes": [4],
            "expected_n": 20,
            "seed": 12345,
            "blinding": "double_blind",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["algorithm"] == "permuted_block"
    assert body["seed"] == 12345


async def test_coordinator_cannot_generate_schedule(client: AsyncClient) -> None:
    ids = await _setup_deployment_with_two_subjects(client)
    await _seed_user("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    resp = await client.post(
        f"/api/edc/deployments/{ids['dep']}/randomization/schedule",
        json={
            "algorithm": "simple",
            "arms": ["A", "B"],
            "expected_n": 10,
        },
    )
    assert resp.status_code == 403


# ── Allocation ─────────────────────────────────────────────────────────


async def _build_open_label_schedule(client: AsyncClient, dep_id: str) -> None:
    await _seed_user("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")
    await client.post(
        f"/api/edc/deployments/{dep_id}/randomization/schedule",
        json={
            "algorithm": "permuted_block",
            "arms": ["Drug A", "Drug B"],
            "block_sizes": [4],
            "expected_n": 20,
            "seed": 42,
            "blinding": "open_label",
        },
    )


async def test_coordinator_can_randomize_subject(client: AsyncClient) -> None:
    ids = await _setup_deployment_with_two_subjects(client)
    await _build_open_label_schedule(client, ids["dep"])
    await _seed_user("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    resp = await client.post(
        f"/api/edc/subjects/{ids['s1']}/randomize",
        json={"factor_values": {}},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # open_label → arm visible at allocation time
    assert body["arm"] in ("Drug A", "Drug B")
    assert body["unblinded"] is True


async def test_double_randomize_returns_409(client: AsyncClient) -> None:
    ids = await _setup_deployment_with_two_subjects(client)
    await _build_open_label_schedule(client, ids["dep"])
    await _seed_user("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    first = await client.post(
        f"/api/edc/subjects/{ids['s1']}/randomize", json={"factor_values": {}}
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/edc/subjects/{ids['s1']}/randomize", json={"factor_values": {}}
    )
    assert second.status_code == 409


async def test_double_blind_allocation_masks_arm_until_codebreak(
    client: AsyncClient,
) -> None:
    ids = await _setup_deployment_with_two_subjects(client)
    await _seed_user("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")
    await client.post(
        f"/api/edc/deployments/{ids['dep']}/randomization/schedule",
        json={
            "algorithm": "simple",
            "arms": ["Drug A", "Drug B"],
            "expected_n": 5,
            "seed": 7,
            "blinding": "double_blind",
        },
    )
    await _seed_user("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    randomize = await client.post(
        f"/api/edc/subjects/{ids['s1']}/randomize", json={"factor_values": {}}
    )
    assert randomize.status_code == 201
    body = randomize.json()
    # In double_blind, the arm is masked to None.
    assert body["arm"] is None
    assert body["unblinded"] is False

    # PI breaks the code, then a fresh read returns the arm.
    await _seed_user("sub-pi", "pi@example.com", role="principal_investigator")
    _login(client, "sub-pi", "pi@example.com")
    cb = await client.post(
        f"/api/edc/subjects/{ids['s1']}/code-break",
        json={"reason": "Suspected anaphylaxis — clinical urgency."},
    )
    assert cb.status_code == 201, cb.text
    read = await client.get(f"/api/edc/subjects/{ids['s1']}/allocation")
    assert read.status_code == 200
    assert read.json()["arm"] in ("Drug A", "Drug B")
    assert read.json()["unblinded"] is True


async def test_coordinator_cannot_codebreak(client: AsyncClient) -> None:
    ids = await _setup_deployment_with_two_subjects(client)
    await _build_open_label_schedule(client, ids["dep"])
    await _seed_user("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    await client.post(
        f"/api/edc/subjects/{ids['s1']}/randomize", json={"factor_values": {}}
    )
    cb = await client.post(
        f"/api/edc/subjects/{ids['s1']}/code-break",
        json={"reason": "should be refused for coordinator"},
    )
    assert cb.status_code == 403


async def test_randomize_refused_when_no_schedule(client: AsyncClient) -> None:
    ids = await _setup_deployment_with_two_subjects(client)
    await _seed_user("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    resp = await client.post(
        f"/api/edc/subjects/{ids['s1']}/randomize", json={"factor_values": {}}
    )
    assert resp.status_code == 409


async def test_stratified_randomisation_assigns_per_stratum(
    client: AsyncClient,
) -> None:
    ids = await _setup_deployment_with_two_subjects(client)
    await _seed_user("sub-dm", "dm@example.com", role="data_manager")
    _login(client, "sub-dm", "dm@example.com")
    schedule = await client.post(
        f"/api/edc/deployments/{ids['dep']}/randomization/schedule",
        json={
            "algorithm": "stratified_permuted_block",
            "arms": ["Drug A", "Drug B"],
            "block_sizes": [2],
            "strata_factors": ["sex"],
            "expected_per_stratum": {"sex=F": 4, "sex=M": 4},
            "seed": 100,
            "blinding": "open_label",
        },
    )
    assert schedule.status_code == 201, schedule.text

    await _seed_user("sub-c", "c@example.com", role="coordinator")
    _login(client, "sub-c", "c@example.com")
    resp = await client.post(
        f"/api/edc/subjects/{ids['s1']}/randomize",
        json={"factor_values": {"sex": "F"}},
    )
    assert resp.status_code == 201
    assert resp.json()["stratum_label"] == "sex=F"
