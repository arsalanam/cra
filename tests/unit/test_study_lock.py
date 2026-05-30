"""Study-level database lock (eCRF E7 — validation pack)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
    FormSnapshot,
)

_SNAP = FormSnapshot(
    form_def_id="f1",
    form_name="demographics",
    version=1,
    title="Demographics",
    definition_json='{"name": "demographics", "title": "Demographics", "sections": []}',
)


async def _deploy_with_form_instance(repo: ClinicalRepository) -> tuple[str, str, str]:
    dep = await repo.deploy_study(
        research_study_id="rs1", name="Trial", forms=[_SNAP], actor_sub="u"
    )
    forms = await repo.list_deployed_forms(dep.id)
    site = await repo.add_site(dep.id, name="Site A")
    subj = await repo.add_subject(dep.id, site_id=site.id, subject_code="S-001")
    fi = await repo.open_form_instance(subj.id, deployed_form_id=forms[0].id, actor_sub="u")
    return dep.id, subj.id, fi.id


async def test_lock_study_records_active_lock(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, _ = await _deploy_with_form_instance(repo)
    lock = await repo.lock_study(dep_id, reason="freeze for interim analysis", actor_sub="dm1")
    assert lock.unlocked_at is None
    assert await repo.is_study_locked(dep_id) is True


async def test_unlock_releases_active_lock(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, _ = await _deploy_with_form_instance(repo)
    await repo.lock_study(dep_id, reason="r", actor_sub="dm1")
    released = await repo.unlock_study(dep_id, reason="resume entries", actor_sub="dm2")
    assert released.unlocked_at is not None
    assert released.unlocked_by_sub == "dm2"
    assert await repo.is_study_locked(dep_id) is False


async def test_double_lock_is_rejected(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, _ = await _deploy_with_form_instance(repo)
    await repo.lock_study(dep_id, reason="r1", actor_sub="dm")
    with pytest.raises(ClinicalError, match="already locked"):
        await repo.lock_study(dep_id, reason="r2", actor_sub="dm")


async def test_lock_refused_with_open_queries(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, fi_id = await _deploy_with_form_instance(repo)
    # Capture one item so we can raise a query against it.
    await repo.submit_item_data(fi_id, {"age": "30"}, actor_sub="u")
    await repo.create_manual_query(
        fi_id, item_id="age", text="please confirm", actor_sub="dm"
    )
    with pytest.raises(ClinicalError, match="open query"):
        await repo.lock_study(dep_id, reason="freeze", actor_sub="dm")


async def test_lock_force_open_queries_overrides(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, fi_id = await _deploy_with_form_instance(repo)
    await repo.submit_item_data(fi_id, {"age": "30"}, actor_sub="u")
    await repo.create_manual_query(fi_id, item_id="age", text="?", actor_sub="dm")
    lock = await repo.lock_study(
        dep_id, reason="forced", actor_sub="dm", require_no_open_queries=False
    )
    assert lock.lock_reason == "forced"


async def test_lock_unknown_deployment_raises(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="not found"):
        await repo.lock_study("does-not-exist", reason="r", actor_sub="dm")


async def test_lock_history_preserved_across_unlock_relock(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, _ = await _deploy_with_form_instance(repo)
    await repo.lock_study(dep_id, reason="r1", actor_sub="dm")
    await repo.unlock_study(dep_id, reason="resume", actor_sub="dm")
    await repo.lock_study(dep_id, reason="r2", actor_sub="dm")
    history = await repo.list_study_locks(dep_id)
    # Two lock rows recorded; the first carries unlocked_at, the second does not.
    assert len(history) == 2
    assert history[0].unlocked_at is not None
    assert history[1].unlocked_at is None


async def test_deployment_id_for_form_instance_resolves(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, fi_id = await _deploy_with_form_instance(repo)
    assert await repo.deployment_id_for_form_instance(fi_id) == dep_id
    assert await repo.deployment_id_for_form_instance("no-such-fi") is None
