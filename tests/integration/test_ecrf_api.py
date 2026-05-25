"""Integration tests for the eCRF authoring API (E0)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from research_assistant.persistence.database import reset_engine


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    # No Cognito env -> auth disabled -> admin endpoints reachable as the
    # default-user placeholder (require_admin short-circuits open).
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    reset_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from research_assistant.persistence.database import init_db
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    reset_engine()


def _form_body(name: str = "demographics") -> dict:
    return {
        "name": name,
        "title": "Demographics",
        "code_lists": [
            {
                "id": "cl_sex",
                "name": "Sex",
                "items": [{"code": "M", "label": "Male"}, {"code": "F", "label": "Female"}],
            }
        ],
        "sections": [
            {
                "id": "main",
                "title": "Main",
                "items": [
                    {"id": "age", "label": "Age", "data_type": "integer", "required": True},
                    {
                        "id": "sex",
                        "label": "Sex",
                        "data_type": "single_select",
                        "code_list_ref": "cl_sex",
                    },
                ],
            }
        ],
    }


async def test_full_authoring_lifecycle(client: AsyncClient) -> None:
    # Create a study.
    study = (await client.post("/api/ecrf/studies", json={"name": "SGLT2 trial"})).json()
    sid = study["id"]
    assert study["status"] == "draft"

    # Create a form (v1 draft).
    r = await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_body())
    assert r.status_code == 201, r.text
    form = r.json()
    fid = form["id"]
    assert form["version"] == 1 and form["status"] == "draft"

    # Duplicate name -> 409.
    assert (
        await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_body())
    ).status_code == 409

    # Edit the draft.
    assert (await client.put(f"/api/ecrf/forms/{fid}", json=_form_body())).status_code == 200

    # Publish, then it becomes immutable.
    pub = (await client.post(f"/api/ecrf/forms/{fid}/publish")).json()
    assert pub["status"] == "published"
    assert (await client.put(f"/api/ecrf/forms/{fid}", json=_form_body())).status_code == 409

    # Detail returns the parsed definition.
    detail = (await client.get(f"/api/ecrf/forms/{fid}")).json()
    assert detail["definition"]["sections"][0]["items"][0]["id"] == "age"

    # ODM export.
    exp = await client.get(f"/api/ecrf/forms/{fid}/export.odm.xml")
    assert exp.status_code == 200
    assert exp.headers["content-type"].startswith("application/xml")
    assert "http://www.cdisc.org/ns/odm/v1.3" in exp.text
    assert "IT.demographics.age" in exp.text

    # New version -> v2 draft.
    v2 = (await client.post(f"/api/ecrf/forms/{fid}/new-version")).json()
    assert v2["version"] == 2 and v2["status"] == "draft"

    # Two versions listed.
    forms = (await client.get(f"/api/ecrf/studies/{sid}/forms")).json()
    assert {f["version"] for f in forms} == {1, 2}


async def test_invalid_definition_rejected(client: AsyncClient) -> None:
    study = (await client.post("/api/ecrf/studies", json={"name": "S"})).json()
    bad = _form_body()
    bad["sections"][0]["items"][1]["code_list_ref"] = "missing"  # unknown code list
    r = await client.post(f"/api/ecrf/studies/{study['id']}/forms", json=bad)
    assert r.status_code == 422
