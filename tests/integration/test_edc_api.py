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
    assert len(forms) == 1
    assert forms[0]["form_name"] == "demographics"
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


# ── E2: edit checks + queries via the API ─────────────────────────────────────


def _form_with_checks() -> dict:
    return {
        "name": "vitals",
        "title": "Vitals",
        "sections": [
            {
                "id": "s",
                "title": "S",
                "items": [
                    {
                        "id": "age",
                        "label": "Age",
                        "data_type": "integer",
                        "required": True,
                        "edit_checks": [
                            {
                                "id": "age_range",
                                "severity": "hard",
                                "expression": "is_blank(age) or (age >= 0 and age < 120)",
                                "message": "Age must be 0-119",
                            }
                        ],
                    },
                    {
                        "id": "sbp",
                        "label": "Systolic BP",
                        "data_type": "integer",
                        "edit_checks": [
                            {
                                "id": "sbp_high",
                                "severity": "soft",
                                "expression": "is_blank(sbp) or sbp <= 200",
                                "message": "Systolic BP unusually high",
                            }
                        ],
                    },
                ],
            }
        ],
    }


async def _open_instance_with_checks(client: AsyncClient) -> str:
    sid = (await client.post("/api/ecrf/studies", json={"name": "Vitals study"})).json()["id"]
    fid = (await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_with_checks())).json()[
        "id"
    ]
    await client.post(f"/api/ecrf/forms/{fid}/publish")
    dep = (await client.post("/api/edc/deployments", json={"research_study_id": sid})).json()["id"]
    form_id = (await client.get(f"/api/edc/deployments/{dep}/forms")).json()[0]["id"]
    site = (await client.post(f"/api/edc/deployments/{dep}/sites", json={"name": "A"})).json()["id"]
    subj = (
        await client.post(
            f"/api/edc/deployments/{dep}/subjects", json={"site_id": site, "subject_code": "S1"}
        )
    ).json()["id"]
    return (
        await client.post(f"/api/edc/subjects/{subj}/forms", json={"deployed_form_id": form_id})
    ).json()["id"]


async def test_hard_check_returns_422_and_saves_nothing(client: AsyncClient) -> None:
    fi_id = await _open_instance_with_checks(client)
    r = await client.put(f"/api/edc/form-instances/{fi_id}/data", json={"values": {"age": "200"}})
    assert r.status_code == 422
    assert r.json()["detail"]["failures"][0]["check_id"] == "age_range"
    # Nothing persisted.
    detail = (await client.get(f"/api/edc/form-instances/{fi_id}")).json()
    assert detail["items"] == []


async def test_soft_check_opens_auto_query_via_api(client: AsyncClient) -> None:
    fi_id = await _open_instance_with_checks(client)
    ok = await client.put(f"/api/edc/form-instances/{fi_id}/data", json={"values": {"sbp": "250"}})
    assert ok.status_code == 200
    queries = (await client.get(f"/api/edc/form-instances/{fi_id}/queries")).json()
    assert len(queries) == 1
    assert queries[0]["query_type"] == "auto"
    assert queries[0]["status"] == "open"


async def test_deployed_form_definition_and_subject_forms(client: AsyncClient) -> None:
    rs = await _published_study(client)
    dep = (await client.post("/api/edc/deployments", json={"research_study_id": rs})).json()["id"]
    form = (await client.get(f"/api/edc/deployments/{dep}/forms")).json()[0]

    # The collector fetches the full definition to render the form.
    detail = (await client.get(f"/api/edc/deployed-forms/{form['id']}")).json()
    assert detail["definition"]["sections"][0]["items"][0]["id"] == "age"

    site = (await client.post(f"/api/edc/deployments/{dep}/sites", json={"name": "A"})).json()["id"]
    subj = (
        await client.post(
            f"/api/edc/deployments/{dep}/subjects", json={"site_id": site, "subject_code": "S1"}
        )
    ).json()["id"]

    # No instances yet, then one after opening.
    assert (await client.get(f"/api/edc/subjects/{subj}/forms")).json() == []
    await client.post(f"/api/edc/subjects/{subj}/forms", json={"deployed_form_id": form["id"]})
    instances = (await client.get(f"/api/edc/subjects/{subj}/forms")).json()
    assert len(instances) == 1
    assert instances[0]["deployed_form_id"] == form["id"]


async def test_sign_lock_and_unlock(client: AsyncClient) -> None:
    fi_id = await _open_instance_with_checks(client)
    # Complete the form (required age present).
    await client.put(
        f"/api/edc/form-instances/{fi_id}/data",
        json={"values": {"age": "45"}, "mark_complete": True},
    )

    # Sign it -> locked.
    sig = await client.post(
        f"/api/edc/form-instances/{fi_id}/sign", json={"meaning": "PI sign-off"}
    )
    assert sig.status_code == 200
    assert sig.json()["voided"] is False

    # Editing a signed form is blocked.
    blocked = await client.put(
        f"/api/edc/form-instances/{fi_id}/data", json={"values": {"age": "46"}}
    )
    assert blocked.status_code == 409

    # Unlock (elevated) voids the signature and reopens the form.
    un = await client.post(
        f"/api/edc/form-instances/{fi_id}/unlock", json={"reason": "correction needed"}
    )
    assert un.status_code == 200
    assert un.json()["status"] == "in_progress"
    sigs = (await client.get(f"/api/edc/form-instances/{fi_id}/signatures")).json()
    assert sigs[0]["voided"] is True

    # Editing works again after unlock.
    again = await client.put(
        f"/api/edc/form-instances/{fi_id}/data", json={"values": {"age": "46"}, "reason": "fix"}
    )
    assert again.status_code == 200


async def test_cannot_sign_incomplete_form(client: AsyncClient) -> None:
    fi_id = await _open_instance_with_checks(client)
    # Not completed -> signing is rejected.
    r = await client.post(f"/api/edc/form-instances/{fi_id}/sign", json={"meaning": "x"})
    assert r.status_code == 409


# ── E6: SDV + subject-casebook sign-off ───────────────────────────────────────


async def _deployed_subject(client: AsyncClient) -> tuple[str, str]:
    """Returns (subject_id, deployed_form_id) for a deployed checked form."""
    sid = (await client.post("/api/ecrf/studies", json={"name": "S6"})).json()["id"]
    fid = (await client.post(f"/api/ecrf/studies/{sid}/forms", json=_form_with_checks())).json()[
        "id"
    ]
    await client.post(f"/api/ecrf/forms/{fid}/publish")
    dep = (await client.post("/api/edc/deployments", json={"research_study_id": sid})).json()["id"]
    form_id = (await client.get(f"/api/edc/deployments/{dep}/forms")).json()[0]["id"]
    site = (await client.post(f"/api/edc/deployments/{dep}/sites", json={"name": "A"})).json()["id"]
    subj = (
        await client.post(
            f"/api/edc/deployments/{dep}/subjects", json={"site_id": site, "subject_code": "S1"}
        )
    ).json()["id"]
    return subj, form_id


async def test_sdv_verification(client: AsyncClient) -> None:
    subj, form_id = await _deployed_subject(client)
    fi = (
        await client.post(f"/api/edc/subjects/{subj}/forms", json={"deployed_form_id": form_id})
    ).json()["id"]
    await client.put(f"/api/edc/form-instances/{fi}/data", json={"values": {"age": "45"}})

    assert (
        await client.post(f"/api/edc/form-instances/{fi}/verify", json={"item_ids": ["age"]})
    ).json()["verified"] == 1
    # Idempotent — re-verifying the same item adds nothing.
    assert (
        await client.post(f"/api/edc/form-instances/{fi}/verify", json={"item_ids": ["age"]})
    ).json()["verified"] == 0
    vs = (await client.get(f"/api/edc/form-instances/{fi}/verifications")).json()
    assert any(v["item_id"] == "age" for v in vs)


async def test_subject_signoff_locks_then_unlock(client: AsyncClient) -> None:
    subj, form_id = await _deployed_subject(client)
    fi = (
        await client.post(f"/api/edc/subjects/{subj}/forms", json={"deployed_form_id": form_id})
    ).json()["id"]

    # Can't sign off with an incomplete form instance.
    assert (
        await client.post(f"/api/edc/subjects/{subj}/sign", json={"meaning": "PI"})
    ).status_code == 409

    # Complete -> sign off the casebook -> instance locked -> edits blocked.
    await client.put(
        f"/api/edc/form-instances/{fi}/data", json={"values": {"age": "45"}, "mark_complete": True}
    )
    assert (
        await client.post(f"/api/edc/subjects/{subj}/sign", json={"meaning": "PI casebook"})
    ).status_code == 200
    assert (
        await client.put(f"/api/edc/form-instances/{fi}/data", json={"values": {"age": "46"}})
    ).status_code == 409

    # Admin unlock reopens the casebook and voids the subject signature.
    assert (
        await client.post(f"/api/edc/subjects/{subj}/unlock", json={"reason": "fix"})
    ).status_code == 200
    assert (
        await client.put(
            f"/api/edc/form-instances/{fi}/data",
            json={"values": {"age": "46"}, "reason": "correction"},
        )
    ).status_code == 200
    ssigs = (await client.get(f"/api/edc/subjects/{subj}/signatures")).json()
    assert ssigs[0]["voided"] is True


async def test_manual_query_workflow_via_api(client: AsyncClient) -> None:
    fi_id = await _open_instance_with_checks(client)
    await client.put(f"/api/edc/form-instances/{fi_id}/data", json={"values": {"age": "45"}})

    q = (
        await client.post(
            f"/api/edc/form-instances/{fi_id}/queries",
            json={"item_id": "age", "text": "Verify against source"},
        )
    ).json()
    assert q["status"] == "open"

    assert (await client.post(f"/api/edc/queries/{q['id']}/respond", json={"text": "ok"})).json()[
        "status"
    ] == "answered"
    assert (await client.post(f"/api/edc/queries/{q['id']}/close")).json()["status"] == "closed"
