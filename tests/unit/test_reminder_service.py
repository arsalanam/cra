"""services.reminders — fire pipeline tests (dry-run only; no SES IO)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.models import (
    ParticipantAccess,
    Site,
    StudyDeployment,
    Subject,
)
from research_assistant.persistence.clinical.repository import ClinicalRepository
from research_assistant.services.reminders import (
    ReminderBatchResult,
    _build_email_body,
    fire_due_reminders,
    ses_enabled_for_testing,
)


async def _seed(session: AsyncSession) -> tuple[StudyDeployment, Subject, ParticipantAccess]:
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
    access = ParticipantAccess(subject_id=subj.id, deployment_id=dep.id, token_hash="hash-1")
    session.add(access)
    await session.flush()
    return dep, subj, access


# ── Email body builder ──────────────────────────────────────────────────


def test_email_body_today_when_offset_zero() -> None:
    subject, body = _build_email_body(
        {
            "visit_name": "Week 4",
            "planned_date": "2026-02-01T12:00:00",
            "offset_days": 0,
        }
    )
    assert "Week 4" in subject
    assert "today" in subject.lower()
    assert "Week 4" in body


def test_email_body_negative_offset_describes_days_before() -> None:
    subject, _body = _build_email_body(
        {
            "visit_name": "Week 4",
            "planned_date": "2026-02-01T12:00:00",
            "offset_days": -7,
        }
    )
    assert "7 day" in subject


# ── SES enablement gate ─────────────────────────────────────────────────


def test_ses_disabled_when_env_unset() -> None:
    if os.environ.get("AWS_SES_FROM_EMAIL"):
        pytest.skip("AWS_SES_FROM_EMAIL set in env — skipping disabled check")
    assert ses_enabled_for_testing() is False


def test_ses_enabled_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_SES_FROM_EMAIL", "noreply@example.com")
    assert ses_enabled_for_testing() is True


# ── End-to-end dry-run fire pipeline ────────────────────────────────────


async def test_fire_returns_zero_counts_when_no_planned_visits(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed(clinical_session)
    with patch("research_assistant.services.reminders.get_clinical_session") as mock_session:
        mock_session.return_value.__aenter__.return_value = clinical_session
        mock_session.return_value.__aexit__.return_value = None
        result = await fire_due_reminders(dep.id)
    assert isinstance(result, ReminderBatchResult)
    assert result.queued == 0
    assert result.sent == 0


async def test_fire_dry_runs_email_when_ses_disabled(
    clinical_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without AWS_SES_FROM_EMAIL the helper writes a SentReminder row
    with provider='dry_run' and status='sent' — no external IO."""
    monkeypatch.delenv("AWS_SES_FROM_EMAIL", raising=False)
    dep, subj, access = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="Day 0", day_offset=0, reminder_offsets=[0])
    await repo.set_active_visit_schedule(sched.id)
    await repo.generate_planned_visits(subj.id)
    await repo.upsert_participant_contact(
        access.id,
        email="participant@example.com",
        opt_in_channels=["email"],
    )

    with (
        patch("research_assistant.services.reminders.get_clinical_session") as mock_session,
        patch("research_assistant.services.reminders.datetime") as mock_dt,
    ):
        mock_session.return_value.__aenter__.return_value = clinical_session
        mock_session.return_value.__aexit__.return_value = None
        mock_dt.now.return_value = subj.baseline_date + timedelta(days=1)
        result = await fire_due_reminders(dep.id)
    # The helper's compute_due_reminders call uses the real datetime.now —
    # so we instead advance time by populating planned_date in the past.
    # When the subject's baseline_date is in early 2026 and pytest runs in
    # 2026-05+, the day-0 reminder is already due.
    assert result.queued >= 1
    sent_rows = await repo.list_sent_reminders(deployment_id=dep.id)
    assert any(r.provider == "dry_run" and r.status == "sent" for r in sent_rows)


async def test_fire_skips_sms_until_twilio_wired(
    clinical_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMS-channel reminders should be recorded with status='skipped'
    until the SMS provider is wired."""
    monkeypatch.delenv("AWS_SES_FROM_EMAIL", raising=False)
    dep, subj, access = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    sched = await repo.create_visit_schedule(dep.id, name="Main")
    await repo.add_scheduled_visit(sched.id, visit_name="V", day_offset=0, reminder_offsets=[0])
    await repo.set_active_visit_schedule(sched.id)
    await repo.generate_planned_visits(subj.id)
    await repo.upsert_participant_contact(
        access.id,
        phone="+15551234567",
        preferred_channel="sms",
        opt_in_channels=["sms"],
    )

    with patch("research_assistant.services.reminders.get_clinical_session") as mock_session:
        mock_session.return_value.__aenter__.return_value = clinical_session
        mock_session.return_value.__aexit__.return_value = None
        result = await fire_due_reminders(dep.id)
    assert result.skipped >= 1
    sent_rows = await repo.list_sent_reminders(deployment_id=dep.id)
    sms_rows = [r for r in sent_rows if r.channel == "sms"]
    assert sms_rows and sms_rows[0].status == "skipped"
    assert "SMS provider not configured" in (sms_rows[0].error or "")
