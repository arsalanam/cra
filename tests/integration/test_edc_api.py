"""Integration tests for the EDC capture API (eCRF E1).

Exercises the full path across both datastores: author + publish a form
(research DB) -> deploy + capture (clinical DB) -> read back + audit trail.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from research_assistant.persistence.clinical.database import reset_clinical_engine
from research_assistant.persistence.database import reset_engine


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    # No Cognito env -> auth disabled -> role gates short-circuit open.
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


def _form_body() -> dict:
    return {
        "name": "demographics",
        "title": "Demographics",
        "sections": [
            {
                "id": "main",
                "title": "Main",
                "items": [
                    {"id": "age", "label": "Age", "data_type": "integer", "required": True},
                    {"id": "sex", "label": "Sex", "data_type": "text"},
                ],
            }
        ],
    }


async def _published_study(client: AsyncClient) -> str:
    """Create a study with one published form; return the research study id."""
    sid = (await client.post("/api/ecrf/studies", json={"name": "SGLT2 trial"})).json()["id"]
    fid = (await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_body())).json()["id"]
    assert (await client.post(f"/api/ecrf/forms/{fid}/publish")).status_code == 200
    return sid


async def test_deploy_capture_and_audit(client: AsyncClient) -> None:
    research_study_id = await _published_study(client)

    # Deploy for collection -> snapshots the published form into the clinical store.
    dep = await client.post("/api/edc/deployments", json={"research_study_id": research_study_id})
    assert dep.status_code == 201, dep.text
    dep_id = dep.json()["id"]

    forms = (await client.get(f"/api/edc/deployments/{dep_id}/forms")).json()
    assert len(forms) == 1 and forms[0]["form_name"] == "demographics"
    deployed_form_id = forms[0]["id"]

    # Site + subject.
    site_id = (
        await client.post(f"/api/edc/deployments/{dep_id}/sites", json={"name": "Site A"})
    ).json()["id"]
    subject_id = (
        await client.post(
            f"/api/edc/deployments/{dep_id}/subjects",
            json={"site_id": site_id, "subject_code": "S-001"},
        )
    ).json()["id"]

    # Open a form instance and submit data.
    fi_id = (
        await client.post(
            f"/api/edc/subjects/{subject_id}/forms",
            json={"deployed_form_id": deployed_form_id},
        )
    ).json()["id"]

    submit = await client.put(
        f"/api/edc/form-instances/{fi_id}/data",
        json={"values": {"age": "45", "sex": "M"}, "mark_complete": True},
    )
    assert submit.status_code == 200
    assert submit.json()["status"] == "complete"

    # Read back the captured values.
    detail = (await client.get(f"/api/edc/form-instances/{fi_id}")).json()
    assert {i["item_id"]: i["value"] for i in detail["items"]} == {"age": "45", "sex": "M"}

    # Audit trail has the form-instance create + per-item creates + status.
    audit = (await client.get(f"/api/edc/form-instances/{fi_id}/audit")).json()
    actions = {(a["entity_type"], a["action"]) for a in audit}
    assert ("form_instance", "create") in actions
    assert ("item_data", "create") in actions
    assert ("form_instance", "status") in actions


async def test_deploy_without_published_forms_409(client: AsyncClient) -> None:
    # Study with a draft (unpublished) form -> nothing to deploy.
    sid = (await client.post("/api/ecrf/studies", json={"name": "Draft only"})).json()["id"]
    await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_body())
    r = await client.post("/api/edc/deployments", json={"research_study_id": sid})
    assert r.status_code == 409


async def test_cross_deployment_site_rejected_via_api(client: AsyncClient) -> None:
    rs = await _published_study(client)
    dep1 = (await client.post("/api/edc/deployments", json={"research_study_id": rs})).json()["id"]
    dep2 = (await client.post("/api/edc/deployments", json={"research_study_id": rs})).json()["id"]
    site2 = (await client.post(f"/api/edc/deployments/{dep2}/sites", json={"name": "B"})).json()[
        "id"
    ]
    # Subject in dep1 referencing dep2's site -> 400.
    r = await client.post(
        f"/api/edc/deployments/{dep1}/subjects",
        json={"site_id": site2, "subject_code": "X1"},
    )
    assert r.status_code == 400
