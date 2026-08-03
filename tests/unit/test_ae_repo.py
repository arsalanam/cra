"""ClinicalRepository AE methods — auto-classification + override + overdue."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.models import (
    StudyDeployment,
    Subject,
)
from research_assistant.persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
)


async def _seed_subject(session: AsyncSession) -> tuple[StudyDeployment, Subject]:
    dep = StudyDeployment(research_study_id="rs-1", name="Trial")
    session.add(dep)
    await session.flush()
    from research_assistant.persistence.clinical.models import Site

    site = Site(deployment_id=dep.id, name="Site A")
    session.add(site)
    await session.flush()
    subj = Subject(
        deployment_id=dep.id,
        site_id=site.id,
        subject_code="S-001",
    )
    session.add(subj)
    await session.flush()
    return dep, subj


async def test_record_grade_3_auto_serious_and_sets_deadline(
    clinical_session: AsyncSession,
) -> None:
    _dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ae = await repo.record_adverse_event(
        subj.id,
        term_text="severe headache",
        severity_grade=3,
        outcome="recovering",
        relationship_to_intervention="possible",
        start_date=datetime.now(UTC),
        actor_sub="coord-1",
    )
    assert ae.is_serious is True
    assert ae.reportable_deadline is not None
    # Deadline is 24h after reported_at
    assert ae.reportable_deadline == ae.reported_at + timedelta(hours=24)
    assert json.loads(ae.serious_reasons_json) == ["other_medically_significant"]


async def test_record_grade_1_not_serious_no_deadline(
    clinical_session: AsyncSession,
) -> None:
    _dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ae = await repo.record_adverse_event(
        subj.id,
        term_text="mild rash",
        severity_grade=1,
        outcome="recovered",
        relationship_to_intervention="unrelated",
        start_date=datetime.now(UTC),
        actor_sub="coord-1",
    )
    assert ae.is_serious is False
    assert ae.reportable_deadline is None
    assert json.loads(ae.serious_reasons_json) == []


async def test_hospitalisation_flag_promotes_to_serious(
    clinical_session: AsyncSession,
) -> None:
    _dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ae = await repo.record_adverse_event(
        subj.id,
        term_text="chest pain",
        severity_grade=2,
        outcome="recovering",
        relationship_to_intervention="possible",
        start_date=datetime.now(UTC),
        hospitalisation_flag=True,
        actor_sub="coord-1",
    )
    assert ae.is_serious is True
    assert json.loads(ae.serious_reasons_json) == ["hospitalisation"]


async def test_pi_override_to_not_serious_clears_deadline(
    clinical_session: AsyncSession,
) -> None:
    """PI can downgrade an auto-classified SAE to not-serious — clearing
    the 24h deadline so the overdue endpoint stops surfacing it."""
    _dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ae = await repo.record_adverse_event(
        subj.id,
        term_text="grade 3 fatigue",
        severity_grade=3,
        outcome="recovering",
        relationship_to_intervention="possible",
        start_date=datetime.now(UTC),
        actor_sub="coord-1",
    )
    assert ae.is_serious is True
    overridden = await repo.reclassify_adverse_event(ae.id, is_serious=False, actor_sub="pi-1")
    assert overridden.is_serious is False
    assert overridden.reportable_deadline is None
    assert overridden.classified_by == "pi-1"


async def test_list_overdue_serious_aes_filters_correctly(
    clinical_session: AsyncSession,
) -> None:
    """The overdue endpoint surfaces serious AEs past deadline that
    haven't been reported. Past, future, and reported-already are all
    excluded."""
    dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    # Past-deadline serious AE → should surface
    past = await repo.record_adverse_event(
        subj.id,
        term_text="overdue SAE",
        severity_grade=3,
        outcome="recovering",
        relationship_to_intervention="possible",
        start_date=datetime.now(UTC),
        actor_sub="coord",
    )
    # Backdate the deadline so it's in the past
    past.reportable_deadline = datetime.now(UTC) - timedelta(hours=1)
    await clinical_session.flush()

    # Recent serious AE → deadline still in the future
    await repo.record_adverse_event(
        subj.id,
        term_text="fresh SAE",
        severity_grade=3,
        outcome="recovering",
        relationship_to_intervention="possible",
        start_date=datetime.now(UTC),
        actor_sub="coord",
    )

    # Past-deadline serious AE but already reported → excluded
    reported = await repo.record_adverse_event(
        subj.id,
        term_text="reported SAE",
        severity_grade=3,
        outcome="recovering",
        relationship_to_intervention="possible",
        start_date=datetime.now(UTC),
        actor_sub="coord",
    )
    reported.reportable_deadline = datetime.now(UTC) - timedelta(hours=2)
    await repo.mark_ae_reported_to_authority(reported.id, actor_sub="dm")

    overdue = await repo.list_overdue_serious_aes(dep.id)
    assert len(overdue) == 1
    assert overdue[0].id == past.id


async def test_invalid_severity_grade_rejected(
    clinical_session: AsyncSession,
) -> None:
    _dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="1–5"):
        await repo.record_adverse_event(
            subj.id,
            term_text="bad grade",
            severity_grade=6,
            outcome="unknown",
            relationship_to_intervention="unknown",
            start_date=datetime.now(UTC),
        )


async def test_mark_reported_clears_deadline_and_is_idempotent(
    clinical_session: AsyncSession,
) -> None:
    _dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ae = await repo.record_adverse_event(
        subj.id,
        term_text="SAE",
        severity_grade=3,
        outcome="recovering",
        relationship_to_intervention="possible",
        start_date=datetime.now(UTC),
        actor_sub="coord",
    )
    assert ae.reportable_deadline is not None
    first = await repo.mark_ae_reported_to_authority(ae.id, actor_sub="dm")
    assert first.reported_to_authority_at is not None
    assert first.reportable_deadline is None
    # Idempotent
    second = await repo.mark_ae_reported_to_authority(ae.id, actor_sub="dm")
    assert second.reported_to_authority_at == first.reported_to_authority_at
