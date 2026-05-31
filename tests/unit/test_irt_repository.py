"""IRT — repository methods: schedule + allocation + code-break."""

from __future__ import annotations

import json

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


async def _seed_deployment_with_two_subjects(
    repo: ClinicalRepository,
) -> tuple[str, str, str]:
    dep = await repo.deploy_study(
        research_study_id="rs1", name="Trial", forms=[_SNAP], actor_sub="u"
    )
    site = await repo.add_site(dep.id, name="Site A", code="A")
    s1 = await repo.add_subject(dep.id, site_id=site.id, subject_code="S-001")
    s2 = await repo.add_subject(dep.id, site_id=site.id, subject_code="S-002")
    return dep.id, s1.id, s2.id


# ── Schedule lifecycle ─────────────────────────────────────────────────


async def test_create_schedule_persists_seed_and_arms(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, _ = await _seed_deployment_with_two_subjects(repo)
    schedule = await repo.create_randomization_schedule(
        dep_id,
        algorithm="permuted_block",
        arms=["Drug A", "Drug B"],
        ratio=[1, 1],
        block_sizes=[4],
        seed=12345,
        sequence_json=json.dumps(["Drug A", "Drug B", "Drug B", "Drug A"] * 5),
        actor_sub="dm-1",
    )
    assert schedule.seed == 12345
    assert json.loads(schedule.arms_json) == ["Drug A", "Drug B"]
    assert schedule.status == "active"


async def test_double_schedule_creation_refused(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, _ = await _seed_deployment_with_two_subjects(repo)
    await repo.create_randomization_schedule(
        dep_id,
        algorithm="simple",
        arms=["A", "B"],
        seed=1,
        sequence_json=json.dumps(["A", "B"]),
        actor_sub="dm",
    )
    with pytest.raises(ClinicalError, match="already has an active"):
        await repo.create_randomization_schedule(
            dep_id,
            algorithm="simple",
            arms=["A", "B"],
            seed=2,
            sequence_json=json.dumps(["A", "B"]),
            actor_sub="dm",
        )


async def test_close_schedule_blocks_double_close(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, _, _ = await _seed_deployment_with_two_subjects(repo)
    schedule = await repo.create_randomization_schedule(
        dep_id,
        algorithm="simple",
        arms=["A", "B"],
        seed=1,
        sequence_json=json.dumps(["A", "B"]),
        actor_sub="dm",
    )
    closed = await repo.close_randomization_schedule(schedule.id, actor_sub="dm")
    assert closed.status == "closed"
    with pytest.raises(ClinicalError, match="already closed"):
        await repo.close_randomization_schedule(schedule.id, actor_sub="dm")


# ── Allocation ─────────────────────────────────────────────────────────


async def test_double_allocation_refused(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, subj_id, _ = await _seed_deployment_with_two_subjects(repo)
    schedule = await repo.create_randomization_schedule(
        dep_id,
        algorithm="simple",
        arms=["A", "B"],
        seed=1,
        sequence_json=json.dumps(["A", "B"]),
        actor_sub="dm",
    )
    from research_assistant.persistence.clinical.models import Subject

    subject = await clinical_session.get(Subject, subj_id)
    assert subject is not None
    await repo.persist_allocation(
        schedule=schedule,
        subject=subject,
        arm="A",
        stratum_label=None,
        factor_values=None,
        sequence_position=0,
        actor_sub="coord",
    )
    with pytest.raises(ClinicalError, match="already randomised"):
        await repo.persist_allocation(
            schedule=schedule,
            subject=subject,
            arm="B",
            stratum_label=None,
            factor_values=None,
            sequence_position=1,
            actor_sub="coord",
        )


async def test_open_label_allocation_is_unblinded_at_creation(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, subj_id, _ = await _seed_deployment_with_two_subjects(repo)
    schedule = await repo.create_randomization_schedule(
        dep_id,
        algorithm="simple",
        arms=["A", "B"],
        seed=1,
        blinding="open_label",
        sequence_json=json.dumps(["A"]),
        actor_sub="dm",
    )
    from research_assistant.persistence.clinical.models import Subject

    subject = await clinical_session.get(Subject, subj_id)
    assert subject is not None
    allocation = await repo.persist_allocation(
        schedule=schedule,
        subject=subject,
        arm="A",
        stratum_label=None,
        factor_values=None,
        sequence_position=0,
        actor_sub="coord",
    )
    assert allocation.unblinded is True
    assert allocation.unblinded_at is not None


async def test_double_blind_allocation_persists_unblinded_false(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, subj_id, _ = await _seed_deployment_with_two_subjects(repo)
    schedule = await repo.create_randomization_schedule(
        dep_id,
        algorithm="simple",
        arms=["A", "B"],
        seed=1,
        blinding="double_blind",
        sequence_json=json.dumps(["A"]),
        actor_sub="dm",
    )
    from research_assistant.persistence.clinical.models import Subject

    subject = await clinical_session.get(Subject, subj_id)
    assert subject is not None
    allocation = await repo.persist_allocation(
        schedule=schedule,
        subject=subject,
        arm="A",
        stratum_label=None,
        factor_values=None,
        sequence_position=0,
        actor_sub="coord",
    )
    assert allocation.unblinded is False
    assert allocation.unblinded_at is None


# ── Code-break ─────────────────────────────────────────────────────────


async def test_code_break_flips_unblinded_and_persists_event(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, subj_id, _ = await _seed_deployment_with_two_subjects(repo)
    schedule = await repo.create_randomization_schedule(
        dep_id,
        algorithm="simple",
        arms=["A", "B"],
        seed=1,
        blinding="double_blind",
        sequence_json=json.dumps(["A"]),
        actor_sub="dm",
    )
    from research_assistant.persistence.clinical.models import Subject

    subject = await clinical_session.get(Subject, subj_id)
    assert subject is not None
    await repo.persist_allocation(
        schedule=schedule,
        subject=subject,
        arm="A",
        stratum_label=None,
        factor_values=None,
        sequence_position=0,
        actor_sub="coord",
    )
    event = await repo.code_break(
        subj_id,
        reason="Suspected anaphylaxis — clinical urgency.",
        actor_sub="pi",
    )
    assert event.reason.startswith("Suspected anaphylaxis")
    refreshed = await repo.get_allocation_for_subject(subj_id)
    assert refreshed is not None
    assert refreshed.unblinded is True
    assert refreshed.unblinded_at is not None


async def test_code_break_refused_when_no_allocation(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    _dep_id, subj_id, _ = await _seed_deployment_with_two_subjects(repo)
    with pytest.raises(ClinicalError, match="no allocation"):
        await repo.code_break(subj_id, reason="reason placeholder text", actor_sub="pi")


async def test_double_code_break_refused(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    dep_id, subj_id, _ = await _seed_deployment_with_two_subjects(repo)
    schedule = await repo.create_randomization_schedule(
        dep_id,
        algorithm="simple",
        arms=["A", "B"],
        seed=1,
        blinding="double_blind",
        sequence_json=json.dumps(["A"]),
        actor_sub="dm",
    )
    from research_assistant.persistence.clinical.models import Subject

    subject = await clinical_session.get(Subject, subj_id)
    assert subject is not None
    await repo.persist_allocation(
        schedule=schedule,
        subject=subject,
        arm="A",
        stratum_label=None,
        factor_values=None,
        sequence_position=0,
        actor_sub="coord",
    )
    await repo.code_break(subj_id, reason="First emergency event description.", actor_sub="pi")
    with pytest.raises(ClinicalError, match="already unblinded"):
        await repo.code_break(subj_id, reason="Second attempt.", actor_sub="pi")
