"""RBAC + end-to-end test for the CDISC submission endpoints.

Verifies the role separation locked by the matrix: data_manager can run
the derivation + export the bundle, PI can export but not derive,
coordinator + student are blocked.
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
                    {"id": "sex", "label": "Sex", "data_type": "text"},
                ],
            }
        ],
    }


async def _bootstrap(client: AsyncClient) -> dict[str, str]:
    """Admin seeds a study + deployment + site + subject + an AE."""
    await _seed("sub-admin", "admin@example.com", "admin")
    _login(client, "sub-admin", "admin@example.com")
    sid = (await client.post("/api/ecrf/studies", json={"name": "Test Trial"})).json()["id"]
    fid = (
        await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_body())
    ).json()["id"]
    await client.post(f"/api/ecrf/forms/{fid}/publish")
    dep = (
        await client.post("/api/edc/deployments", json={"research_study_id": sid})
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
    # Seed an AE so AE/ADSL paths have data.
    await client.post(
        f"/api/edc/subjects/{subj['id']}/adverse-events",
        json={
            "term_text": "headache",
            "severity_grade": 3,
            "outcome": "recovering",
            "relationship_to_intervention": "possible",
            "start_date": "2026-05-29T10:00:00+00:00",
        },
    )
    return {"sid": sid, "dep": dep["id"], "subject": subj["id"]}


async def test_data_manager_derives_and_exports(client: AsyncClient) -> None:
    ids = await _bootstrap(client)
    await _seed("sub-dm", "dm@example.com", "data_manager")
    _login(client, "sub-dm", "dm@example.com")

    derive = await client.post(f"/api/edc/deployments/{ids['dep']}/cdisc/derive")
    assert derive.status_code == 200, derive.text
    counts = derive.json()["counts"]
    assert counts["dm"] == 1
    assert counts["ae"] == 1
    assert counts["adsl"] == 1
    assert counts["tlf"] >= 1

    bundle = await client.get(
        f"/api/edc/deployments/{ids['dep']}/cdisc/submission-bundle.zip"
    )
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    assert bundle.content[:4] == b"PK\x03\x04"


async def test_pi_can_export_but_not_derive(client: AsyncClient) -> None:
    ids = await _bootstrap(client)
    # DM has to run the derivation first for PI to have anything to export.
    await _seed("sub-dm", "dm@example.com", "data_manager")
    _login(client, "sub-dm", "dm@example.com")
    await client.post(f"/api/edc/deployments/{ids['dep']}/cdisc/derive")

    await _seed("sub-pi", "pi@example.com", "principal_investigator")
    _login(client, "sub-pi", "pi@example.com")

    derive = await client.post(f"/api/edc/deployments/{ids['dep']}/cdisc/derive")
    assert derive.status_code == 403, derive.text

    bundle = await client.get(
        f"/api/edc/deployments/{ids['dep']}/cdisc/submission-bundle.zip"
    )
    assert bundle.status_code == 200
    assert bundle.content[:4] == b"PK\x03\x04"


async def test_coordinator_blocked_from_all_cdisc(client: AsyncClient) -> None:
    ids = await _bootstrap(client)
    await _seed("sub-c", "c@example.com", "coordinator")
    _login(client, "sub-c", "c@example.com")

    derive = await client.post(f"/api/edc/deployments/{ids['dep']}/cdisc/derive")
    assert derive.status_code == 403
    listing = await client.get(f"/api/edc/deployments/{ids['dep']}/cdisc/datasets")
    assert listing.status_code == 403
    bundle = await client.get(
        f"/api/edc/deployments/{ids['dep']}/cdisc/submission-bundle.zip"
    )
    assert bundle.status_code == 403


async def test_student_blocked_from_cdisc(client: AsyncClient) -> None:
    ids = await _bootstrap(client)
    await _seed("sub-s", "s@example.com", "student")
    _login(client, "sub-s", "s@example.com")
    derive = await client.post(f"/api/edc/deployments/{ids['dep']}/cdisc/derive")
    assert derive.status_code == 403


async def test_dataset_csv_download_after_derivation(client: AsyncClient) -> None:
    ids = await _bootstrap(client)
    await _seed("sub-dm", "dm@example.com", "data_manager")
    _login(client, "sub-dm", "dm@example.com")
    await client.post(f"/api/edc/deployments/{ids['dep']}/cdisc/derive")

    dm = await client.get(
        f"/api/edc/deployments/{ids['dep']}/cdisc/datasets/DM/export.csv"
    )
    assert dm.status_code == 200
    assert dm.headers["content-type"] == "text/csv; charset=utf-8"
    text = dm.content.decode("utf-8")
    # Header row + one data row
    assert text.splitlines()[0].startswith("STUDYID,DOMAIN,USUBJID")
    assert "S-001" in text


async def test_export_bundle_refuses_before_derivation(client: AsyncClient) -> None:
    ids = await _bootstrap(client)
    await _seed("sub-dm", "dm@example.com", "data_manager")
    _login(client, "sub-dm", "dm@example.com")
    bundle = await client.get(
        f"/api/edc/deployments/{ids['dep']}/cdisc/submission-bundle.zip"
    )
    assert bundle.status_code == 409
    assert "derive" in bundle.json()["detail"]
