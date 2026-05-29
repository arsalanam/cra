"""RBAC enforcement for the eCRF safety subsystem.

Locks the separation-of-duties posture from the role-permission matrix:
  • coordinator records AEs and deviations but cannot classify or sign-off
  • PI classifies AEs as serious and closes deviations
  • data_manager authors CAPAs and runs the FDA 3500A report
  • monitor logs deviations during site visits but does not classify
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


async def _seed(sub: str, email: str, role: str) -> None:
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        u = User(cognito_sub=sub, email=email)
        session.add(u)
        await session.flush()
        await UserRepository(session).grant_role(u.id, role)


def _login(c: AsyncClient, sub: str, email: str) -> None:
    c.cookies.set("cra_session", _cookie(sub, email))


def _form_body() -> dict[str, Any]:
    return {
        "name": "demographics",
        "title": "Demographics",
        "sections": [
            {
                "id": "main",
                "title": "Main",
                "items": [
                    {"id": "age", "label": "Age", "data_type": "integer", "required": False},
                ],
            }
        ],
    }


async def _setup_subject(client: AsyncClient) -> dict[str, str]:
    """Bootstrap a study + deployment + site + subject as admin."""
    await _seed("sub-admin", "admin@example.com", "admin")
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
            f"/api/edc/deployments/{dep['id']}/sites", json={"name": "Boston"}
        )
    ).json()
    subj = (
        await client.post(
            f"/api/edc/deployments/{dep['id']}/subjects",
            json={"site_id": site["id"], "subject_code": "S-001"},
        )
    ).json()
    return {"sid": sid, "dep": dep["id"], "site": site["id"], "subject": subj["id"]}


def _ae_body() -> dict[str, Any]:
    return {
        "term_text": "severe headache",
        "severity_grade": 3,
        "outcome": "recovering",
        "relationship_to_intervention": "possible",
        "start_date": "2026-05-29T10:00:00+00:00",
        "narrative": "Patient hospitalised for 2 days.",
    }


async def test_coordinator_records_ae_but_cannot_classify(
    client: AsyncClient,
) -> None:
    ids = await _setup_subject(client)
    await _seed("sub-c", "c@example.com", "coordinator")
    _login(client, "sub-c", "c@example.com")
    rec = await client.post(
        f"/api/edc/subjects/{ids['subject']}/adverse-events", json=_ae_body()
    )
    assert rec.status_code == 201, rec.text
    ae_id = rec.json()["id"]
    # Coordinator cannot override the classification.
    cls = await client.patch(f"/api/edc/ae/{ae_id}", json={"is_serious": False})
    assert cls.status_code == 403


async def test_pi_classifies_ae_and_can_run_3500a(client: AsyncClient) -> None:
    ids = await _setup_subject(client)
    # Coordinator records the AE.
    await _seed("sub-c", "c@example.com", "coordinator")
    _login(client, "sub-c", "c@example.com")
    ae = (
        await client.post(
            f"/api/edc/subjects/{ids['subject']}/adverse-events", json=_ae_body()
        )
    ).json()
    assert ae["is_serious"] is True  # grade-3 auto-classified

    # PI reclassifies + has sae.report.
    await _seed("sub-pi", "pi@example.com", "principal_investigator")
    _login(client, "sub-pi", "pi@example.com")
    cls = await client.patch(
        f"/api/edc/ae/{ae['id']}", json={"is_serious": False}
    )
    assert cls.status_code == 200, cls.text
    assert cls.json()["is_serious"] is False
    # PI can also run the 3500A report.
    rep = await client.get(f"/api/edc/ae/{ae['id']}/report/fda-3500a/pdf")
    assert rep.status_code == 200
    assert rep.headers["content-type"] == "application/pdf"
    assert rep.content[:5] == b"%PDF-"


async def test_monitor_logs_deviation_but_does_not_classify(
    client: AsyncClient,
) -> None:
    ids = await _setup_subject(client)
    await _seed("sub-m", "m@example.com", "monitor")
    _login(client, "sub-m", "m@example.com")
    rec = await client.post(
        f"/api/edc/subjects/{ids['subject']}/deviations",
        json={
            "classification": "minor",
            "category": "visit_window",
            "description": "Visit 7 days late",
        },
    )
    assert rec.status_code == 201, rec.text
    dev_id = rec.json()["id"]
    # Monitor cannot reclassify.
    cls = await client.patch(
        f"/api/edc/deviations/{dev_id}", json={"classification": "major"}
    )
    assert cls.status_code == 403


async def test_data_manager_authors_capa_pi_closes_deviation(
    client: AsyncClient,
) -> None:
    ids = await _setup_subject(client)
    # Coordinator logs the deviation.
    await _seed("sub-c", "c@example.com", "coordinator")
    _login(client, "sub-c", "c@example.com")
    dev = (
        await client.post(
            f"/api/edc/subjects/{ids['subject']}/deviations",
            json={
                "classification": "major",
                "category": "eligibility",
                "description": "Ineligible subject enrolled",
            },
        )
    ).json()

    # Data manager adds a CAPA.
    await _seed("sub-dm", "dm@example.com", "data_manager")
    _login(client, "sub-dm", "dm@example.com")
    capa = await client.post(
        f"/api/edc/deviations/{dev['id']}/capa",
        json={"action_text": "Site retraining"},
    )
    assert capa.status_code == 201, capa.text
    capa_id = capa.json()["id"]
    # DM completes the CAPA (owner=self default).
    await client.post(f"/api/edc/capa/{capa_id}/complete")

    # DM cannot close the deviation — that's PI.
    dm_close = await client.post(f"/api/edc/deviations/{dev['id']}/close")
    assert dm_close.status_code == 403

    # PI closes the deviation.
    await _seed("sub-pi", "pi@example.com", "principal_investigator")
    _login(client, "sub-pi", "pi@example.com")
    pi_close = await client.post(f"/api/edc/deviations/{dev['id']}/close")
    assert pi_close.status_code == 200, pi_close.text
    assert pi_close.json()["status"] == "closed"


async def test_student_blocked_from_all_safety_endpoints(client: AsyncClient) -> None:
    ids = await _setup_subject(client)
    await _seed("sub-s", "s@example.com", "student")
    _login(client, "sub-s", "s@example.com")
    rec_ae = await client.post(
        f"/api/edc/subjects/{ids['subject']}/adverse-events", json=_ae_body()
    )
    assert rec_ae.status_code == 403
    rec_dev = await client.post(
        f"/api/edc/subjects/{ids['subject']}/deviations",
        json={"classification": "minor", "category": "other", "description": "x"},
    )
    assert rec_dev.status_code == 403
