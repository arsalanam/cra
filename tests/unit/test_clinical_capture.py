"""Unit tests for the clinical-data capture repository + audit-writer seam (E1)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.models import AuditEntry, ItemData
from research_assistant.persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
    FormSnapshot,
)

_SNAPSHOT = FormSnapshot(
    form_def_id="f1",
    form_name="demographics",
    version=1,
    title="Demographics",
    definition_json='{"name": "demographics", "title": "Demographics", "sections": []}',
)


async def _deploy_with_subject(repo: ClinicalRepository) -> tuple[str, str]:
    """Deploy a study, add a site + subject; return (deployed_form_id, subject_id)."""
    dep = await repo.deploy_study(
        research_study_id="rs1", name="Trial", forms=[_SNAPSHOT], actor_sub="u1"
    )
    forms = await repo.list_deployed_forms(dep.id)
    site = await repo.add_site(dep.id, name="Site A", code="A", actor_sub="u1")
    subject = await repo.add_subject(dep.id, site_id=site.id, subject_code="S-001", actor_sub="u1")
    return forms[0].id, subject.id


async def test_deploy_snapshots_forms_and_audits(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    dep = await repo.deploy_study(
        research_study_id="rs1", name="Trial", forms=[_SNAPSHOT], actor_sub="u1"
    )
    forms = await repo.list_deployed_forms(dep.id)
    assert len(forms) == 1
    assert forms[0].form_name == "demographics" and forms[0].version == 1
    assert '"name": "demographics"' in forms[0].definition_json  # snapshot stored

    audit = (
        await clinical_session.scalars(select(AuditEntry).where(AuditEntry.entity_id == dep.id))
    ).all()
    assert [a.action for a in audit] == ["deploy"]


async def test_cross_deployment_site_rejected(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    dep = await repo.deploy_study(research_study_id="rs", name="T", forms=[_SNAPSHOT])
    other = await repo.deploy_study(research_study_id="rs2", name="T2", forms=[_SNAPSHOT])
    site = await repo.add_site(other.id, name="X")
    with pytest.raises(ClinicalError, match="Site not found"):
        await repo.add_subject(dep.id, site_id=site.id, subject_code="S1")


async def test_submit_audits_each_item_and_change(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    form_id, subject_id = await _deploy_with_subject(repo)
    fi = await repo.open_form_instance(subject_id, deployed_form_id=form_id, actor_sub="u1")

    # First capture: two items created.
    await repo.submit_item_data(fi.id, {"age": "45", "sex": "M"}, actor_sub="u1")
    found = await repo.get_form_instance(fi.id)
    assert found is not None
    _, items = found
    assert {i.item_id: i.value for i in items} == {"age": "45", "sex": "M"}
    assert (await repo.get_form_instance(fi.id))[0].status == "in_progress"

    # Change one value with a reason; unchanged value is not re-audited.
    await repo.submit_item_data(
        fi.id, {"age": "46", "sex": "M"}, actor_sub="u2", reason="data entry correction"
    )
    item_audit = (
        await clinical_session.scalars(
            select(AuditEntry).where(
                AuditEntry.form_instance_id == fi.id, AuditEntry.item_id.is_not(None)
            )
        )
    ).all()
    actions = sorted((a.item_id, a.action) for a in item_audit)
    # age: create + update ; sex: create only (second submit was a no-op).
    assert actions == [("age", "create"), ("age", "update"), ("sex", "create")]

    update = next(a for a in item_audit if a.item_id == "age" and a.action == "update")
    assert (update.old_value, update.new_value, update.reason) == (
        "45",
        "46",
        "data entry correction",
    )

    # No ItemData row was duplicated.
    n_items = await clinical_session.scalar(
        select(func.count()).select_from(ItemData).where(ItemData.form_instance_id == fi.id)
    )
    assert n_items == 2


async def test_mark_complete_sets_status_and_audits(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    form_id, subject_id = await _deploy_with_subject(repo)
    fi = await repo.open_form_instance(subject_id, deployed_form_id=form_id, actor_sub="u1")
    await repo.submit_item_data(fi.id, {"age": "50"}, actor_sub="u1", mark_complete=True)

    refreshed = (await repo.get_form_instance(fi.id))[0]
    assert refreshed.status == "complete"

    audit = await repo.get_form_instance_audit(fi.id)
    actions = [a.action for a in audit]
    assert "create" in actions  # form instance + item creates
    assert any(a.action == "status" and a.new_value == "complete" for a in audit)
