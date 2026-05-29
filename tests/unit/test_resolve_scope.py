"""Resource→scope resolvers (`web/authz.py`).

The resolvers fetch from the clinical store and project resources to
`(study_id, site_id)` tuples the rbac module can answer permission
questions against. Tests use the in-memory clinical session fixture from
`tests/conftest.py` and patch `get_clinical_session` to yield it.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.models import (
    DeployedForm,
    FormInstance,
    Site,
    StudyDeployment,
    Subject,
)


def _patch_clinical_session(session: AsyncSession) -> contextlib.AbstractContextManager[None]:
    """Re-yield the provided AsyncSession from `get_clinical_session()`.

    The resolvers use `async with get_clinical_session() as s:` — we replace
    that context manager so the resolvers see the same session the test is
    seeding rows into.
    """

    @contextlib.asynccontextmanager
    async def _fake() -> AsyncIterator[AsyncSession]:
        yield session

    return patch(
        "research_assistant.web.authz.get_clinical_session",
        lambda: _fake(),
    )


async def _seed(session: AsyncSession) -> dict[str, str]:
    """Seed a deployment + 1 site + 1 subject + 1 form-instance.

    Returns a dict of ids so tests can assert against them.
    """
    deployment = StudyDeployment(
        research_study_id="research-study-1",
        name="My Trial",
    )
    session.add(deployment)
    await session.flush()
    site = Site(deployment_id=deployment.id, name="Boston")
    session.add(site)
    await session.flush()
    subject = Subject(
        deployment_id=deployment.id,
        site_id=site.id,
        subject_code="S-001",
    )
    deployed_form = DeployedForm(
        deployment_id=deployment.id,
        form_def_id="def-1",
        form_name="demographics",
        version=1,
        title="Demographics",
        definition_json="{}",
    )
    session.add_all([subject, deployed_form])
    await session.flush()
    form_instance = FormInstance(
        subject_id=subject.id,
        deployed_form_id=deployed_form.id,
    )
    session.add(form_instance)
    await session.flush()
    return {
        "deployment_id": deployment.id,
        "site_id": site.id,
        "subject_id": subject.id,
        "form_instance_id": form_instance.id,
        "research_study_id": deployment.research_study_id,
    }


async def test_resolve_deployment_scope(clinical_session: AsyncSession) -> None:
    from research_assistant.web.authz import resolve_deployment_scope

    ids = await _seed(clinical_session)
    with _patch_clinical_session(clinical_session):
        study_id, site_id, sr_review_id = await resolve_deployment_scope(ids["deployment_id"])
    assert study_id == ids["research_study_id"]
    assert site_id is None  # deployment has no specific site context
    assert sr_review_id is None


async def test_resolve_subject_scope(clinical_session: AsyncSession) -> None:
    from research_assistant.web.authz import resolve_subject_scope

    ids = await _seed(clinical_session)
    with _patch_clinical_session(clinical_session):
        study_id, site_id, sr_review_id = await resolve_subject_scope(ids["subject_id"])
    assert study_id == ids["research_study_id"]
    assert site_id == ids["site_id"]
    assert sr_review_id is None


async def test_resolve_form_instance_scope(clinical_session: AsyncSession) -> None:
    from research_assistant.web.authz import resolve_form_instance_scope

    ids = await _seed(clinical_session)
    with _patch_clinical_session(clinical_session):
        study_id, site_id, sr_review_id = await resolve_form_instance_scope(
            ids["form_instance_id"]
        )
    assert study_id == ids["research_study_id"]
    assert site_id == ids["site_id"]
    assert sr_review_id is None


async def test_resolve_missing_resource_404(clinical_session: AsyncSession) -> None:
    """Missing resources raise an HTTP 404 — preserves the same 404 surface
    the resolver would have produced via the existing endpoint."""
    from fastapi import HTTPException

    from research_assistant.web.authz import (
        resolve_deployment_scope,
        resolve_form_instance_scope,
        resolve_subject_scope,
    )

    with _patch_clinical_session(clinical_session):
        with pytest.raises(HTTPException) as exc:
            await resolve_form_instance_scope("missing")
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException):
            await resolve_subject_scope("missing")
        with pytest.raises(HTTPException):
            await resolve_deployment_scope("missing")


async def test_resolve_ecrf_study_is_identity() -> None:
    """ecrf study path-params resolve straight through — the id IS the scope."""
    from research_assistant.web.authz import resolve_ecrf_study_scope

    study_id, site_id, sr_review_id = await resolve_ecrf_study_scope("study-42")
    assert study_id == "study-42"
    assert site_id is None
    assert sr_review_id is None


async def test_resolve_sr_project_is_identity() -> None:
    """SR project path-params resolve to the sr_review slot only."""
    from research_assistant.web.authz import resolve_sr_project_scope

    study_id, site_id, sr_review_id = await resolve_sr_project_scope("sr-42")
    assert study_id is None
    assert site_id is None
    assert sr_review_id == "sr-42"
