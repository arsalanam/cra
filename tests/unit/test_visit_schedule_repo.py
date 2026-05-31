"""Visit-scheduling repository (P1 #4) — schedule CRUD + planned-visit
auto-generation + reminder-queue computation."""

from __future__ import annotations

import json
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


async def _seed_deployment(session: AsyncSession) -> tuple[StudyDeployment, Site, Subject]:
    dep = StudyDeployment(research_study_id="rs-1", name="Trial")
    session.add(dep)
    await session.flush()
    site = Site(deployment_id=dep.id, name="Site A")
    session.add(site)
    await session.flush()
    baseline = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    subj = Subject(
        deployment_id=dep.id,
        site_id=site.id,
        subject_code="S-001",
        baseline_date=baseline,
    )
    session.add(subj)
    await session.flush()
    return dep, site, subj


# ── VisitSchedule + ScheduledVisit CRUD ─────────────────────────────────


async def test_create_visit_schedule_starts_inactive(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main", description="primary cohort")
    assert sched.is_active is False
    assert sched.name == "Main"


async def test_create_rejects_duplicate_name(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    await repo.create_visit_schedule(dep.id, name="Main")
    with pytest.raises(ClinicalError, match="already exists"):
        await repo.create_visit_schedule(dep.id, name="Main")


async def test_set_active_deactivates_siblings(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    a = await repo.create_visit_schedule(dep.id, name="A")
    b = await repo.create_visit_schedule(dep.id, name="B")
    await repo.set_active_visit_schedule(a.id)
    await repo.set_active_visit_schedule(b.id)
    fetched_a = await clinical_session.get(type(a), a.id)
    fetched_b = await clinical_session.get(type(b), b.id)
    assert fetched_a is not None and fetched_a.is_active is False
    assert fetched_b is not None and fetched_b.is_active is True


async def test_get_active_returns_none_when_no_active(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    await repo.create_visit_schedule(dep.id, name="Inactive")
    assert await repo.get_active_visit_schedule(dep.id) is None


async def test_add_scheduled_visit_rejects_positive_reminder_offset(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    with pytest.raises(ClinicalError, match="days BEFORE"):
        await repo.add_scheduled_visit(
            sched.id,
            visit_name="V1",
            day_offset=14,
            reminder_offsets=[-7, 1],  # 1 is in the past — illegal
        )


async def test_add_scheduled_visit_rejects_negative_window(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    with pytest.raises(ClinicalError, match="non-negative"):
        await repo.add_scheduled_visit(
            sched.id,
            visit_name="V1",
            day_offset=14,
            window_before_days=-3,
        )


async def test_add_scheduled_visit_persists_offsets_as_json(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    v = await repo.add_scheduled_visit(
        sched.id,
        visit_name="Week 4",
        day_offset=28,
        reminder_offsets=[-7, -1],
    )
    parsed = json.loads(v.reminder_offsets_json)
    assert sorted(parsed) == [-7, -1]


# ── Planned-visit generation ────────────────────────────────────────────


async def test_generate_requires_active_schedule(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    await repo.create_visit_schedule(dep.id, name="Inactive")
    with pytest.raises(ClinicalError, match="No active visit schedule"):
        await repo.generate_planned_visits(subj.id)


async def test_generate_creates_one_planned_per_scheduled(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="Baseline", day_offset=0)
    await repo.add_scheduled_visit(
        sched.id, visit_name="Week 4", day_offset=28, window_before_days=3, window_after_days=3
    )
    await repo.set_active_visit_schedule(sched.id)
    created = await repo.generate_planned_visits(subj.id)
    assert len(created) == 2
    by_offset = {(v.window_end - v.window_start).days: v for v in created}
    # baseline has 0-day window, Week 4 has 6-day window (3 before + 3 after).
    assert 0 in by_offset
    assert 6 in by_offset


async def test_generate_is_idempotent(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="V1", day_offset=7)
    await repo.set_active_visit_schedule(sched.id)
    first = await repo.generate_planned_visits(subj.id)
    second = await repo.generate_planned_visits(subj.id)
    assert len(first) == 1
    assert len(second) == 0  # no new rows on second call


async def test_generate_uses_baseline_override(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="V1", day_offset=14)
    await repo.set_active_visit_schedule(sched.id)
    new_baseline = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    created = await repo.generate_planned_visits(subj.id, baseline_date=new_baseline)
    assert created[0].planned_date == new_baseline + timedelta(days=14)
    refreshed = await clinical_session.get(Subject, subj.id)
    assert refreshed is not None and refreshed.baseline_date == new_baseline


# ── update_planned_visit ────────────────────────────────────────────────


async def test_reschedule_requires_override_reason(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="V1", day_offset=7)
    await repo.set_active_visit_schedule(sched.id)
    [pv] = await repo.generate_planned_visits(subj.id)
    new_date = pv.planned_date + timedelta(days=2)
    with pytest.raises(ClinicalError, match="override_reason required"):
        await repo.update_planned_visit(pv.id, planned_date=new_date)


async def test_reschedule_shifts_window(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(
        sched.id,
        visit_name="V1",
        day_offset=7,
        window_before_days=3,
        window_after_days=3,
    )
    await repo.set_active_visit_schedule(sched.id)
    [pv] = await repo.generate_planned_visits(subj.id)
    new_date = pv.planned_date + timedelta(days=10)
    updated = await repo.update_planned_visit(
        pv.id,
        planned_date=new_date,
        override_reason="subject travel",
    )
    assert updated.planned_date == new_date
    assert updated.window_start == new_date - timedelta(days=3)
    assert updated.window_end == new_date + timedelta(days=3)
    assert updated.override_reason == "subject travel"


async def test_complete_sets_completed_at(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="V1", day_offset=7)
    await repo.set_active_visit_schedule(sched.id)
    [pv] = await repo.generate_planned_visits(subj.id)
    completed = await repo.update_planned_visit(pv.id, status="completed")
    assert completed.status == "completed"
    assert completed.completed_at is not None


async def test_update_rejects_invalid_status(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="V1", day_offset=0)
    await repo.set_active_visit_schedule(sched.id)
    [pv] = await repo.generate_planned_visits(subj.id)
    with pytest.raises(ClinicalError, match="Invalid status"):
        await repo.update_planned_visit(pv.id, status="weird")


# ── Reminder queue ──────────────────────────────────────────────────────


async def test_queue_empty_when_no_subjects(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    queue = await repo.compute_due_reminders(dep.id)
    assert queue == []


async def test_queue_skips_subjects_without_contact(
    clinical_session: AsyncSession,
) -> None:
    """No ParticipantContact → reminder pipeline skips entirely."""
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="V1", day_offset=0, reminder_offsets=[0])
    await repo.set_active_visit_schedule(sched.id)
    await repo.generate_planned_visits(subj.id)
    queue = await repo.compute_due_reminders(dep.id)
    assert queue == []


async def test_queue_picks_up_opted_in_contact(
    clinical_session: AsyncSession,
) -> None:
    from research_assistant.persistence.clinical.models import ParticipantAccess

    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(
        sched.id,
        visit_name="Week 1",
        day_offset=0,
        reminder_offsets=[0],  # day-of reminder
    )
    await repo.set_active_visit_schedule(sched.id)
    await repo.generate_planned_visits(subj.id)
    # Create participant access + contact.
    access = ParticipantAccess(subject_id=subj.id, deployment_id=dep.id, token_hash="hash-1")
    clinical_session.add(access)
    await clinical_session.flush()
    await repo.upsert_participant_contact(
        access.id,
        email="participant@example.com",
        preferred_channel="email",
        opt_in_channels=["email"],
    )
    # Set the "now" to after the planned date so the day-0 reminder fires.
    now = subj.baseline_date + timedelta(days=1)
    queue = await repo.compute_due_reminders(dep.id, now=now)
    assert len(queue) == 1
    assert queue[0]["channel"] == "email"
    assert queue[0]["recipient"] == "participant@example.com"
    assert queue[0]["offset_days"] == 0


async def test_queue_respects_opt_out(
    clinical_session: AsyncSession,
) -> None:
    from research_assistant.persistence.clinical.models import ParticipantAccess

    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(
        sched.id, visit_name="Week 1", day_offset=0, reminder_offsets=[0]
    )
    await repo.set_active_visit_schedule(sched.id)
    await repo.generate_planned_visits(subj.id)
    access = ParticipantAccess(subject_id=subj.id, deployment_id=dep.id, token_hash="hash-2")
    clinical_session.add(access)
    await clinical_session.flush()
    await repo.upsert_participant_contact(
        access.id,
        email="x@example.com",
        opt_in_channels=["email"],
        opt_out=True,  # withdrew consent
    )
    now = subj.baseline_date + timedelta(days=1)
    queue = await repo.compute_due_reminders(dep.id, now=now)
    assert queue == []


async def test_queue_dedupes_against_sent_reminders(
    clinical_session: AsyncSession,
) -> None:
    """Already-sent reminders should not re-appear in the queue."""
    from research_assistant.persistence.clinical.models import ParticipantAccess

    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(
        sched.id, visit_name="Week 1", day_offset=0, reminder_offsets=[0]
    )
    await repo.set_active_visit_schedule(sched.id)
    [pv] = await repo.generate_planned_visits(subj.id)
    access = ParticipantAccess(subject_id=subj.id, deployment_id=dep.id, token_hash="hash-3")
    clinical_session.add(access)
    await clinical_session.flush()
    await repo.upsert_participant_contact(
        access.id,
        email="y@example.com",
        opt_in_channels=["email"],
    )
    await repo.record_sent_reminder(
        planned_visit_id=pv.id,
        subject_id=subj.id,
        channel="email",
        offset_days=0,
        provider="dry_run",
        status="sent",
    )
    now = subj.baseline_date + timedelta(days=1)
    queue = await repo.compute_due_reminders(dep.id, now=now)
    assert queue == []


async def test_record_sent_reminder_is_idempotent(
    clinical_session: AsyncSession,
) -> None:
    from research_assistant.persistence.clinical.models import ParticipantAccess

    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="V", day_offset=0)
    await repo.set_active_visit_schedule(sched.id)
    [pv] = await repo.generate_planned_visits(subj.id)
    access = ParticipantAccess(subject_id=subj.id, deployment_id=dep.id, token_hash="hash-4")
    clinical_session.add(access)
    await clinical_session.flush()
    a = await repo.record_sent_reminder(
        planned_visit_id=pv.id,
        subject_id=subj.id,
        channel="email",
        offset_days=-1,
        provider="dry_run",
        status="sent",
    )
    b = await repo.record_sent_reminder(
        planned_visit_id=pv.id,
        subject_id=subj.id,
        channel="email",
        offset_days=-1,
        provider="dry_run",
        status="sent",
    )
    assert a.id == b.id  # same row returned
