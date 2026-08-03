"""Integration tests for the participant ePRO API (eCRF E4b)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from research_assistant.persistence.clinical.database import reset_clinical_engine
from research_assistant.persistence.database import reset_engine


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("CLINICAL_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
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
    reset_engine()
    reset_clinical_engine()


def _epro_form() -> dict:
    return {
        "name": "diary",
        "title": "Symptom Diary",
        "epro": True,
        "sections": [
            {
                "id": "daily",
                "title": "Daily",
                "items": [
                    {
                        "id": "pain",
                        "label": "Pain (0-10)",
                        "data_type": "integer",
                        "required": True,
                        "edit_checks": [
                            {
                                "id": "pain_range",
                                "severity": "hard",
                                "expression": "is_blank(pain) or (pain >= 0 and pain <= 10)",
                                "message": "Enter a value from 0 to 10",
                            }
                        ],
                    }
                ],
            }
        ],
    }


async def _setup(client: AsyncClient) -> tuple[str, str, str]:
    """Publish an ePRO study, deploy, add a subject, issue a token.
    Returns (token, deployment_id, deployed_form_id)."""
    sid = (await client.post("/api/ecrf/studies", json={"name": "ePRO study"})).json()["id"]
    fid = (await client.post(f"/api/ecrf/studies/{sid}/forms", json=_epro_form())).json()["id"]
    await client.post(f"/api/ecrf/forms/{fid}/publish")
    dep = (await client.post("/api/edc/deployments", json={"research_study_id": sid})).json()["id"]
    deployed = (await client.get(f"/api/edc/deployments/{dep}/forms")).json()[0]["id"]
    site = (await client.post(f"/api/edc/deployments/{dep}/sites", json={"name": "A"})).json()["id"]
    subj = (
        await client.post(
            f"/api/edc/deployments/{dep}/subjects", json={"site_id": site, "subject_code": "S1"}
        )
    ).json()["id"]
    token = (await client.post(f"/api/edc/subjects/{subj}/epro-access")).json()["token"]
    return token, dep, deployed


async def test_bad_token_rejected(client: AsyncClient) -> None:
    r = await client.get("/api/epro/session?token=not-a-real-token")
    assert r.status_code == 401


async def test_consent_gate_then_capture(client: AsyncClient) -> None:
    token, _dep, deployed = await _setup(client)

    # Session shows the ePRO form and that consent is required.
    s = (await client.get(f"/api/epro/session?token={token}")).json()
    assert s["consent_required"] is True
    assert len(s["forms"]) == 1
    assert s["forms"][0]["title"] == "Symptom Diary"

    # Opening a form before consent is blocked.
    pre = await client.post(f"/api/epro/forms?token={token}&deployed_form_id={deployed}")
    assert pre.status_code == 403

    # Consent, then open + submit.
    assert (await client.post(f"/api/epro/consent?token={token}")).status_code == 200
    assert (await client.get(f"/api/epro/session?token={token}")).json()[
        "consent_required"
    ] is False

    fi = (await client.post(f"/api/epro/forms?token={token}&deployed_form_id={deployed}")).json()
    ok = await client.put(
        f"/api/epro/form-instances/{fi['id']}/data?token={token}",
        json={"values": {"pain": "4"}, "mark_complete": True},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "complete"

    # A hard edit-check still applies to participant entry.
    bad = await client.put(
        f"/api/epro/form-instances/{fi['id']}/data?token={token}",
        json={"values": {"pain": "50"}},
    )
    assert bad.status_code == 422


async def test_epro_entry_is_audited_with_source(client: AsyncClient) -> None:
    token, _dep, deployed = await _setup(client)
    await client.post(f"/api/epro/consent?token={token}")
    fi = (await client.post(f"/api/epro/forms?token={token}&deployed_form_id={deployed}")).json()
    await client.put(
        f"/api/epro/form-instances/{fi['id']}/data?token={token}", json={"values": {"pain": "3"}}
    )
    # Staff-side audit shows the participant-sourced entry.
    audit = (await client.get(f"/api/edc/form-instances/{fi['id']}/audit")).json()
    assert any(a["source"] == "epro" for a in audit)
