"""ScreeningLog repository — recruitment / screening lifecycle (P1 #3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.models import (
    Site,
    StudyDeployment,
    Subject,
)
from research_assistant.persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
)


async def _seed_deployment(
    session: AsyncSession,
) -> tuple[StudyDeployment, Site]:
    dep = StudyDeployment(research_study_id="rs-1", name="Trial")
    session.add(dep)
    await session.flush()
    site = Site(deployment_id=dep.id, name="Site A")
    session.add(site)
    await session.flush()
    return dep, site


# ── record_screening ────────────────────────────────────────────────────


async def test_record_screening_creates_pending_log(
    clinical_session: AsyncSession,
) -> None:
    dep, site = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(
        deployment_id=dep.id,
        screening_code="SCR-0001",
        site_id=site.id,
        age_band="45-64",
        sex="F",
        race="white",
        ethnicity="not_hispanic_or_latino",
        dob_year=1968,
        actor_sub="coord-1",
    )
    assert log.eligibility_status == "pending"
    assert log.consent_status == "pending"
    assert log.enrolment_status == "pending"
    assert log.recorded_by_sub == "coord-1"
    assert log.screening_code == "SCR-0001"


async def test_record_rejects_unknown_deployment(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="not found"):
        await repo.record_screening(
            deployment_id="ghost",
            screening_code="X",
        )


async def test_record_rejects_cross_deployment_site(
    clinical_session: AsyncSession,
) -> None:
    dep1, _ = await _seed_deployment(clinical_session)
    dep2, site2 = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="not found in deployment"):
        await repo.record_screening(
            deployment_id=dep1.id,
            screening_code="X",
            site_id=site2.id,  # belongs to dep2
        )


async def test_record_rejects_duplicate_code(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    await repo.record_screening(deployment_id=dep.id, screening_code="SCR-1")
    with pytest.raises(ClinicalError, match="already exists"):
        await repo.record_screening(deployment_id=dep.id, screening_code="SCR-1")


# ── update_screening_eligibility ────────────────────────────────────────


async def test_update_eligibility_to_eligible(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    updated = await repo.update_screening_eligibility(
        log.id, eligibility_status="eligible", actor_sub="pi-1"
    )
    assert updated.eligibility_status == "eligible"
    assert updated.exclusion_reason_code is None
    assert updated.exclusion_reason_text == ""


async def test_update_screen_failure_requires_reason_code(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    with pytest.raises(ClinicalError, match="exclusion_reason_code required"):
        await repo.update_screening_eligibility(
            log.id, eligibility_status="screen_failure"
        )


async def test_update_screen_failure_rejects_unknown_code(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    with pytest.raises(ClinicalError, match="Unknown exclusion_reason_code"):
        await repo.update_screening_eligibility(
            log.id,
            eligibility_status="screen_failure",
            exclusion_reason_code="banana",
        )


async def test_update_screen_failure_with_consort_code(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    failed = await repo.update_screening_eligibility(
        log.id,
        eligibility_status="screen_failure",
        exclusion_reason_code="age_out_of_range",
        exclusion_reason_text="subject 17 y/o, protocol >=18.",
    )
    assert failed.eligibility_status == "screen_failure"
    assert failed.exclusion_reason_code == "age_out_of_range"
    assert "17" in failed.exclusion_reason_text


async def test_eligibility_invalid_status_rejected(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    with pytest.raises(ClinicalError, match="Invalid eligibility_status"):
        await repo.update_screening_eligibility(
            log.id, eligibility_status="wrong"
        )


# ── update_screening_consent ────────────────────────────────────────────


async def test_consent_blocked_before_eligible(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    with pytest.raises(ClinicalError, match="before eligibility"):
        await repo.update_screening_consent(
            log.id, consent_status="consented"
        )


async def test_consent_consented_sets_date(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    await repo.update_screening_eligibility(log.id, eligibility_status="eligible")
    consented = await repo.update_screening_consent(
        log.id, consent_status="consented"
    )
    assert consented.consent_status == "consented"
    assert consented.consent_date is not None


# ── update_screening_enrolment ──────────────────────────────────────────


async def test_enrolment_blocked_before_consent(
    clinical_session: AsyncSession,
) -> None:
    dep, site = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    await repo.update_screening_eligibility(log.id, eligibility_status="eligible")
    subj = Subject(
        deployment_id=dep.id, site_id=site.id, subject_code="S-001"
    )
    clinical_session.add(subj)
    await clinical_session.flush()
    with pytest.raises(ClinicalError, match="before consent"):
        await repo.update_screening_enrolment(
            log.id,
            enrolment_status="enrolled",
            enrolled_subject_id=subj.id,
        )


async def test_enrolment_requires_subject_id(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    await repo.update_screening_eligibility(log.id, eligibility_status="eligible")
    await repo.update_screening_consent(log.id, consent_status="consented")
    with pytest.raises(ClinicalError, match="enrolled_subject_id required"):
        await repo.update_screening_enrolment(
            log.id, enrolment_status="enrolled"
        )


async def test_enrolment_rejects_cross_deployment_subject(
    clinical_session: AsyncSession,
) -> None:
    dep1, _ = await _seed_deployment(clinical_session)
    dep2, site2 = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep1.id, screening_code="X")
    await repo.update_screening_eligibility(log.id, eligibility_status="eligible")
    await repo.update_screening_consent(log.id, consent_status="consented")
    other_subject = Subject(
        deployment_id=dep2.id, site_id=site2.id, subject_code="OTHER"
    )
    clinical_session.add(other_subject)
    await clinical_session.flush()
    with pytest.raises(ClinicalError, match="different deployments"):
        await repo.update_screening_enrolment(
            log.id,
            enrolment_status="enrolled",
            enrolled_subject_id=other_subject.id,
        )


async def test_enrolment_happy_path(
    clinical_session: AsyncSession,
) -> None:
    dep, site = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    log = await repo.record_screening(deployment_id=dep.id, screening_code="X")
    await repo.update_screening_eligibility(log.id, eligibility_status="eligible")
    await repo.update_screening_consent(log.id, consent_status="consented")
    subj = Subject(
        deployment_id=dep.id, site_id=site.id, subject_code="S-001"
    )
    clinical_session.add(subj)
    await clinical_session.flush()
    enrolled = await repo.update_screening_enrolment(
        log.id,
        enrolment_status="enrolled",
        enrolled_subject_id=subj.id,
    )
    assert enrolled.enrolment_status == "enrolled"
    assert enrolled.enrolled_subject_id == subj.id
    assert enrolled.enrolment_date is not None


# ── list_screening_logs filters ─────────────────────────────────────────


async def test_list_filters_by_eligibility(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    a = await repo.record_screening(deployment_id=dep.id, screening_code="A")
    await repo.record_screening(deployment_id=dep.id, screening_code="B")
    await repo.update_screening_eligibility(a.id, eligibility_status="eligible")
    eligible_only = await repo.list_screening_logs(
        deployment_id=dep.id, eligibility_status="eligible"
    )
    assert [r.screening_code for r in eligible_only] == ["A"]
    pending_only = await repo.list_screening_logs(
        deployment_id=dep.id, eligibility_status="pending"
    )
    assert [r.screening_code for r in pending_only] == ["B"]


# ── recruitment_funnel ─────────────────────────────────────────────────


async def test_funnel_counts_each_stage_independently(
    clinical_session: AsyncSession,
) -> None:
    """Funnel totals reflect each stage on its own — not cumulative
    drop-through. A subject who got to 'eligible' but didn't yet consent
    still counts as eligible."""
    dep, site = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    # 5 screenings: 1 pending, 1 screen_failure, 1 eligible-only,
    # 1 consented-but-not-enrolled, 1 fully enrolled.
    pend = await repo.record_screening(deployment_id=dep.id, screening_code="P")
    fail = await repo.record_screening(deployment_id=dep.id, screening_code="F")
    elig_only = await repo.record_screening(deployment_id=dep.id, screening_code="E")
    consented = await repo.record_screening(deployment_id=dep.id, screening_code="C")
    enrolled = await repo.record_screening(deployment_id=dep.id, screening_code="N")
    await repo.update_screening_eligibility(
        fail.id, eligibility_status="screen_failure",
        exclusion_reason_code="age_out_of_range",
    )
    for log in (elig_only, consented, enrolled):
        await repo.update_screening_eligibility(log.id, eligibility_status="eligible")
    await repo.update_screening_consent(consented.id, consent_status="consented")
    await repo.update_screening_consent(enrolled.id, consent_status="consented")
    subj = Subject(deployment_id=dep.id, site_id=site.id, subject_code="S")
    clinical_session.add(subj)
    await clinical_session.flush()
    await repo.update_screening_enrolment(
        enrolled.id,
        enrolment_status="enrolled",
        enrolled_subject_id=subj.id,
    )
    funnel = await repo.recruitment_funnel(dep.id)
    totals = funnel["totals"]
    assert totals == {
        "screened": 5,
        "eligible": 3,
        "consented": 2,
        "enrolled": 1,
    }
    assert pend.screening_code == "P"  # silence linter


async def test_funnel_screen_failures_by_reason(
    clinical_session: AsyncSession,
) -> None:
    dep, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    for i, reason in enumerate(
        ["age_out_of_range", "age_out_of_range", "pregnancy"]
    ):
        log = await repo.record_screening(
            deployment_id=dep.id, screening_code=f"SCR-{i}"
        )
        await repo.update_screening_eligibility(
            log.id,
            eligibility_status="screen_failure",
            exclusion_reason_code=reason,
        )
    funnel = await repo.recruitment_funnel(dep.id)
    counts = funnel["screen_failures_by_reason"]
    assert counts == {"age_out_of_range": 2, "pregnancy": 1}


async def test_funnel_per_week_per_site_buckets(
    clinical_session: AsyncSession,
) -> None:
    dep, site = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    base = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)  # Monday, ISO week 20
    await repo.record_screening(
        deployment_id=dep.id,
        screening_code="A",
        site_id=site.id,
        screening_date=base,
    )
    await repo.record_screening(
        deployment_id=dep.id,
        screening_code="B",
        site_id=site.id,
        screening_date=base + timedelta(days=2),  # same week
    )
    await repo.record_screening(
        deployment_id=dep.id,
        screening_code="C",
        site_id=site.id,
        screening_date=base + timedelta(days=8),  # next week
    )
    funnel = await repo.recruitment_funnel(dep.id)
    weeks = funnel["per_week_per_site"]
    assert len(weeks) == 2
    # The site count for week 20 should be 2.
    counts_per_site = list(weeks.values())
    site_counts = [next(iter(week.values())) for week in counts_per_site]
    assert {sc["screened"] for sc in site_counts} == {2, 1}


async def test_funnel_site_filter_isolates(
    clinical_session: AsyncSession,
) -> None:
    dep, site_a = await _seed_deployment(clinical_session)
    site_b = Site(deployment_id=dep.id, name="Site B")
    clinical_session.add(site_b)
    await clinical_session.flush()
    repo = ClinicalRepository(clinical_session)
    await repo.record_screening(
        deployment_id=dep.id, screening_code="A1", site_id=site_a.id
    )
    await repo.record_screening(
        deployment_id=dep.id, screening_code="A2", site_id=site_a.id
    )
    await repo.record_screening(
        deployment_id=dep.id, screening_code="B1", site_id=site_b.id
    )
    funnel_a = await repo.recruitment_funnel(dep.id, site_id=site_a.id)
    assert funnel_a["totals"]["screened"] == 2
    funnel_b = await repo.recruitment_funnel(dep.id, site_id=site_b.id)
    assert funnel_b["totals"]["screened"] == 1
