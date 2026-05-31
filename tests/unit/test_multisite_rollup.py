"""Multi-site rollup service (P2 #5) — per-deployment + org-level
aggregations over the existing ClinicalBase tables.

Each test seeds a deliberately small + asymmetric layout (2 sites,
1 subject per site) so the per-site separation is visible in the
output. Quorum-level rollups (counts) are easy to overcount or
undercount across joins, so each test pins the expected number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.models import (
    AdverseEvent,
    CapaAction,
    DeployedForm,
    FormInstance,
    PlannedVisit,
    ProtocolDeviation,
    Query,
    Site,
    StudyDeployment,
    Subject,
    Verification,
)
from research_assistant.persistence.clinical.repository import ClinicalRepository
from research_assistant.services.multisite import (
    org_rollup,
    site_rollup,
)

# ── Seed helpers ──────────────────────────────────────────────────────────


async def _seed_two_sites(
    session: AsyncSession,
    *,
    deployment_name: str = "Trial A",
) -> tuple[StudyDeployment, Site, Site, Subject, Subject]:
    """Return (deployment, site_a, site_b, subj_a, subj_b).

    `subj_a` lives at site A and `subj_b` at site B. Baseline date is
    now-30days so the visit-schedule arithmetic lands in the past for
    "overdue" tests.
    """
    dep = StudyDeployment(research_study_id="rs", name=deployment_name)
    session.add(dep)
    await session.flush()

    site_a = Site(deployment_id=dep.id, name="Site A", code="A")
    site_b = Site(deployment_id=dep.id, name="Site B", code="B")
    session.add_all([site_a, site_b])
    await session.flush()

    baseline = datetime.now(UTC) - timedelta(days=30)
    subj_a = Subject(
        deployment_id=dep.id,
        site_id=site_a.id,
        subject_code="S-A-001",
        baseline_date=baseline,
    )
    subj_b = Subject(
        deployment_id=dep.id,
        site_id=site_b.id,
        subject_code="S-B-001",
        baseline_date=baseline,
    )
    session.add_all([subj_a, subj_b])
    await session.flush()
    return dep, site_a, site_b, subj_a, subj_b


async def _seed_form_instance(
    session: AsyncSession,
    *,
    deployment_id: str,
    subject_id: str,
    form_name: str | None = None,
) -> FormInstance:
    name = form_name or f"vitals-{subject_id[:8]}"
    form = DeployedForm(
        deployment_id=deployment_id,
        form_def_id=f"def-{name}",
        form_name=name,
        version=1,
        title="Vitals",
        definition_json="{}",
    )
    session.add(form)
    await session.flush()
    fi = FormInstance(
        subject_id=subject_id,
        deployed_form_id=form.id,
        event_instance_id=None,
        status="in_progress",
    )
    session.add(fi)
    await session.flush()
    return fi


# ── Empty / not-found edges ──────────────────────────────────────────────


async def test_site_rollup_raises_when_deployment_missing(
    clinical_session: AsyncSession,
) -> None:
    with pytest.raises(ValueError, match="not found"):
        await site_rollup(clinical_session, deployment_id="ghost")


async def test_site_rollup_returns_zero_when_no_sites(
    clinical_session: AsyncSession,
) -> None:
    dep = StudyDeployment(research_study_id="rs", name="Empty")
    clinical_session.add(dep)
    await clinical_session.flush()
    card = await site_rollup(clinical_session, deployment_id=dep.id)
    assert card.n_sites == 0
    assert card.n_subjects == 0
    assert card.site_totals.enrolment.screened == 0


async def test_site_rollup_basic_per_site_separation(
    clinical_session: AsyncSession,
) -> None:
    dep, site_a, site_b, _, _ = await _seed_two_sites(clinical_session)
    card = await site_rollup(clinical_session, deployment_id=dep.id)
    assert card.deployment_name == "Trial A"
    assert card.n_sites == 2
    assert card.n_subjects == 2
    site_a_card = next(s for s in card.sites if s.site_id == site_a.id)
    site_b_card = next(s for s in card.sites if s.site_id == site_b.id)
    assert site_a_card.n_subjects == 1
    assert site_b_card.n_subjects == 1


# ── Enrolment funnel ─────────────────────────────────────────────────────


async def test_enrolment_funnel_counts_per_site(
    clinical_session: AsyncSession,
) -> None:
    dep, site_a, site_b, _, _ = await _seed_two_sites(clinical_session)
    repo = ClinicalRepository(clinical_session)
    # Site A: 3 screened, 2 eligible, 1 consented.
    await repo.record_screening(dep.id, site_id=site_a.id, screening_code="ENC-A1")
    log_a2 = await repo.record_screening(dep.id, site_id=site_a.id, screening_code="ENC-A2")
    log_a3 = await repo.record_screening(dep.id, site_id=site_a.id, screening_code="ENC-A3")
    await repo.update_screening_eligibility(log_a2.id, eligibility_status="eligible")
    await repo.update_screening_eligibility(log_a3.id, eligibility_status="eligible")
    await repo.update_screening_consent(log_a3.id, consent_status="consented")
    # Site B: 1 screened, 0 eligible.
    await repo.record_screening(dep.id, site_id=site_b.id, screening_code="ENC-B1")
    card = await site_rollup(clinical_session, deployment_id=dep.id)
    a = next(s for s in card.sites if s.site_id == site_a.id)
    b = next(s for s in card.sites if s.site_id == site_b.id)
    assert a.enrolment.screened == 3
    assert a.enrolment.eligible == 2
    assert a.enrolment.consented == 1
    assert b.enrolment.screened == 1
    assert b.enrolment.eligible == 0
    # Totals row adds up.
    assert card.site_totals.enrolment.screened == 4
    assert card.site_totals.enrolment.eligible == 2


# ── Query backlog ────────────────────────────────────────────────────────


async def test_query_backlog_counts_per_site(
    clinical_session: AsyncSession,
) -> None:
    dep, site_a, site_b, subj_a, subj_b = await _seed_two_sites(clinical_session)
    fi_a = await _seed_form_instance(clinical_session, deployment_id=dep.id, subject_id=subj_a.id)
    fi_b = await _seed_form_instance(clinical_session, deployment_id=dep.id, subject_id=subj_b.id)
    # Site A: 2 open + 1 answered + 1 closed.
    for status in ("open", "open", "answered", "closed"):
        clinical_session.add(
            Query(
                form_instance_id=fi_a.id,
                subject_id=subj_a.id,
                item_id="itm",
                status=status,
                text="q",
            )
        )
    # Site B: 1 open.
    clinical_session.add(
        Query(
            form_instance_id=fi_b.id,
            subject_id=subj_b.id,
            item_id="itm",
            status="open",
            text="q",
        )
    )
    await clinical_session.flush()
    card = await site_rollup(clinical_session, deployment_id=dep.id)
    a = next(s for s in card.sites if s.site_id == site_a.id)
    b = next(s for s in card.sites if s.site_id == site_b.id)
    assert a.queries.open == 2
    assert a.queries.answered == 1
    assert a.queries.closed == 1
    assert b.queries.open == 1
    assert card.site_totals.queries.open == 3


# ── Safety ──────────────────────────────────────────────────────────────


async def test_safety_open_aes_count_per_site(
    clinical_session: AsyncSession,
) -> None:
    dep, site_a, site_b, subj_a, subj_b = await _seed_two_sites(clinical_session)
    # Site A: 2 open AEs (one severity 4 = serious), Site B: 1 closed AE.
    clinical_session.add_all(
        [
            AdverseEvent(
                subject_id=subj_a.id,
                deployment_id=dep.id,
                term_text="headache",
                start_date=datetime.now(UTC),
                severity_grade=2,
                outcome="ongoing",
                is_serious=False,
            ),
            AdverseEvent(
                subject_id=subj_a.id,
                deployment_id=dep.id,
                term_text="sepsis",
                start_date=datetime.now(UTC),
                severity_grade=4,
                outcome=None,
                is_serious=True,
            ),
            AdverseEvent(
                subject_id=subj_b.id,
                deployment_id=dep.id,
                term_text="nausea",
                start_date=datetime.now(UTC),
                severity_grade=1,
                outcome="recovered",
                is_serious=False,
            ),
        ]
    )
    await clinical_session.flush()
    card = await site_rollup(clinical_session, deployment_id=dep.id)
    a = next(s for s in card.sites if s.site_id == site_a.id)
    b = next(s for s in card.sites if s.site_id == site_b.id)
    assert a.safety.open_aes == 2
    assert a.safety.open_serious_aes == 1
    assert b.safety.open_aes == 0
    assert card.site_totals.safety.open_aes == 2


async def test_safety_open_deviations_and_capas_per_site(
    clinical_session: AsyncSession,
) -> None:
    dep, site_a, site_b, subj_a, subj_b = await _seed_two_sites(clinical_session)
    # Site A: 1 open deviation with 1 open CAPA + 1 closed deviation.
    dev_open = ProtocolDeviation(
        deployment_id=dep.id,
        subject_id=subj_a.id,
        category="visit_window",
        description="late visit",
        status="open",
    )
    dev_closed = ProtocolDeviation(
        deployment_id=dep.id,
        subject_id=subj_a.id,
        category="other",
        description="resolved",
        status="closed",
    )
    clinical_session.add_all([dev_open, dev_closed])
    await clinical_session.flush()
    clinical_session.add(CapaAction(deviation_id=dev_open.id, action_text="retrain", status="open"))
    await clinical_session.flush()
    card = await site_rollup(clinical_session, deployment_id=dep.id)
    a = next(s for s in card.sites if s.site_id == site_a.id)
    b = next(s for s in card.sites if s.site_id == site_b.id)
    assert a.safety.open_deviations == 1
    assert a.safety.open_capas == 1
    assert b.safety.open_deviations == 0
    assert b.safety.open_capas == 0


# ── Operational — overdue visits, low IP, last SDV ─────────────────────


async def test_operational_overdue_visits_per_site(
    clinical_session: AsyncSession,
) -> None:
    dep, site_a, site_b, subj_a, subj_b = await _seed_two_sites(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    sv_day7 = await repo.add_scheduled_visit(sched.id, visit_name="V1", day_offset=7)
    await repo.set_active_visit_schedule(sched.id)
    # Hand-roll a planned visit with window_end in the past for subj_a;
    # status='pending' is the default. For subj_b, window_end in the
    # future.
    now = datetime.now(UTC)
    past_window_end = now - timedelta(days=2)
    future_window_end = now + timedelta(days=7)
    clinical_session.add_all(
        [
            PlannedVisit(
                subject_id=subj_a.id,
                scheduled_visit_id=sv_day7.id,
                planned_date=now - timedelta(days=5),
                window_start=now - timedelta(days=7),
                window_end=past_window_end,
                status="pending",
            ),
            PlannedVisit(
                subject_id=subj_b.id,
                scheduled_visit_id=sv_day7.id,
                planned_date=now + timedelta(days=5),
                window_start=now + timedelta(days=3),
                window_end=future_window_end,
                status="pending",
            ),
        ]
    )
    await clinical_session.flush()
    card = await site_rollup(clinical_session, deployment_id=dep.id, now=now)
    a = next(s for s in card.sites if s.site_id == site_a.id)
    b = next(s for s in card.sites if s.site_id == site_b.id)
    assert a.operational.overdue_visits == 1
    assert b.operational.overdue_visits == 0
    assert card.site_totals.operational.overdue_visits == 1


async def test_operational_last_sdv_per_site(
    clinical_session: AsyncSession,
) -> None:
    dep, site_a, site_b, subj_a, _ = await _seed_two_sites(clinical_session)
    fi_a = await _seed_form_instance(clinical_session, deployment_id=dep.id, subject_id=subj_a.id)
    sdv_at = datetime(2026, 5, 15, 10, 0, tzinfo=UTC)
    clinical_session.add(Verification(form_instance_id=fi_a.id, item_id="itm", verified_at=sdv_at))
    await clinical_session.flush()
    card = await site_rollup(clinical_session, deployment_id=dep.id)
    a = next(s for s in card.sites if s.site_id == site_a.id)
    b = next(s for s in card.sites if s.site_id == site_b.id)
    # SQLite drops tzinfo on read, so compare on the naive components.
    assert a.operational.last_sdv_at is not None
    assert a.operational.last_sdv_at.replace(tzinfo=None) == sdv_at.replace(tzinfo=None)
    assert b.operational.last_sdv_at is None


async def test_operational_low_ip_lots_surface_on_totals(
    clinical_session: AsyncSession,
) -> None:
    """IP is registered per-deployment (not per-site), so low-lot
    surfaces on the totals row, not on individual site cards."""
    dep, site_a, _, _, _ = await _seed_two_sites(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(
        dep.id,
        ip_id=ip.id,
        lot_number="LOT-LOW",
        quantity_received=5,  # under default threshold of 10
        site_id=site_a.id,
    )
    card = await site_rollup(clinical_session, deployment_id=dep.id, low_ip_threshold=10)
    assert any("LOT-LOW" in k for k in card.site_totals.operational.low_ip_lots)


# ── Org-level rollup ────────────────────────────────────────────────────


async def test_org_rollup_aggregates_across_deployments(
    clinical_session: AsyncSession,
) -> None:
    dep1, _, _, _, _ = await _seed_two_sites(clinical_session, deployment_name="Trial A")
    dep2, _, _, _, _ = await _seed_two_sites(clinical_session, deployment_name="Trial B")
    rollup = await org_rollup(clinical_session)
    assert rollup["n_deployments"] == 2
    # 2 deployments × 2 subjects each.
    assert rollup["org_totals"]["n_subjects"] == 4
    names = {d["deployment_name"] for d in rollup["deployments"]}
    assert names == {"Trial A", "Trial B"}


async def test_org_rollup_empty_when_no_deployments(
    clinical_session: AsyncSession,
) -> None:
    rollup = await org_rollup(clinical_session)
    assert rollup["n_deployments"] == 0
    assert rollup["org_totals"]["n_subjects"] == 0
    assert rollup["deployments"] == []
