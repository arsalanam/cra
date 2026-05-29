"""ClinicalRepository protocol-deviation + CAPA lifecycle."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.models import StudyDeployment, Subject
from research_assistant.persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
)


async def _seed_subject(session: AsyncSession) -> tuple[StudyDeployment, Subject]:
    from research_assistant.persistence.clinical.models import Site

    dep = StudyDeployment(research_study_id="rs-1", name="Trial")
    session.add(dep)
    await session.flush()
    site = Site(deployment_id=dep.id, name="Site A")
    session.add(site)
    await session.flush()
    subj = Subject(deployment_id=dep.id, site_id=site.id, subject_code="S-001")
    session.add(subj)
    await session.flush()
    return dep, subj


async def test_record_deviation_opens_with_status_open(
    clinical_session: AsyncSession,
) -> None:
    dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    dev = await repo.record_deviation(
        deployment_id=dep.id,
        subject_id=subj.id,
        classification="minor",
        category="visit_window",
        description="Subject visit 14 days late",
        actor_sub="coord-1",
    )
    assert dev.status == "open"
    assert dev.discovered_by == "coord-1"


async def test_record_rejects_invalid_classification(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="major"):
        await repo.record_deviation(
            deployment_id=dep.id,
            subject_id=None,
            classification="catastrophic",
            category="other",
            description="x",
        )


async def test_add_capa_flips_status_to_under_capa(
    clinical_session: AsyncSession,
) -> None:
    dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    dev = await repo.record_deviation(
        deployment_id=dep.id,
        subject_id=subj.id,
        classification="major",
        category="eligibility",
        description="ineligible subject enrolled",
        actor_sub="coord-1",
    )
    await repo.add_capa(
        dev.id, action_text="Site retraining", actor_sub="dm-1"
    )
    refreshed = await repo.get_deviation(dev.id)
    assert refreshed is not None
    assert refreshed.status == "under_capa"


async def test_close_blocked_while_capa_open(
    clinical_session: AsyncSession,
) -> None:
    dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    dev = await repo.record_deviation(
        deployment_id=dep.id,
        subject_id=subj.id,
        classification="minor",
        category="procedure",
        description="x",
        actor_sub="coord",
    )
    capa = await repo.add_capa(dev.id, action_text="do thing", actor_sub="dm")
    with pytest.raises(ClinicalError, match="CAPA action.*open"):
        await repo.close_deviation(dev.id, actor_sub="pi")
    # Complete the CAPA, then close should succeed.
    await repo.complete_capa(capa.id, actor_sub="dm")
    closed = await repo.close_deviation(dev.id, actor_sub="pi")
    assert closed.status == "closed"
    assert closed.resolved_by == "pi"


async def test_close_no_capa_required_for_open_deviation_with_no_capa(
    clinical_session: AsyncSession,
) -> None:
    """A deviation with NO CAPAs at all can be closed immediately
    (e.g. minor data-capture deviation requiring no remediation)."""
    dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    dev = await repo.record_deviation(
        deployment_id=dep.id,
        subject_id=subj.id,
        classification="minor",
        category="data_capture",
        description="typo in date field",
        actor_sub="coord",
    )
    closed = await repo.close_deviation(dev.id, actor_sub="pi")
    assert closed.status == "closed"


async def test_add_capa_blocked_on_closed_deviation(
    clinical_session: AsyncSession,
) -> None:
    dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    dev = await repo.record_deviation(
        deployment_id=dep.id,
        subject_id=subj.id,
        classification="minor",
        category="other",
        description="x",
        actor_sub="coord",
    )
    await repo.close_deviation(dev.id, actor_sub="pi")
    with pytest.raises(ClinicalError, match="closed"):
        await repo.add_capa(dev.id, action_text="after-the-fact", actor_sub="dm")


async def test_complete_capa_idempotent(
    clinical_session: AsyncSession,
) -> None:
    dep, subj = await _seed_subject(clinical_session)
    repo = ClinicalRepository(clinical_session)
    dev = await repo.record_deviation(
        deployment_id=dep.id,
        subject_id=subj.id,
        classification="minor",
        category="other",
        description="x",
        actor_sub="coord",
    )
    capa = await repo.add_capa(dev.id, action_text="do thing", actor_sub="dm")
    first = await repo.complete_capa(capa.id, actor_sub="dm")
    second = await repo.complete_capa(capa.id, actor_sub="dm")
    assert first.completed_at == second.completed_at
