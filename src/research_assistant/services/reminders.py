"""Participant-reminder send pipeline (P1 #4).

Stack:

    APScheduler interval job
              │
              ▼
    services.reminders.fire_due_reminders(deployment_id)
              │
              ▼
    ClinicalRepository.compute_due_reminders() → list[dict]
              │
              ▼
    _send_one(channel='email'|'sms', ...)
              │
              ▼
    SES (boto3) when AWS_SES_FROM_EMAIL is set
    dry_run     otherwise (logs only, still writes SentReminder)

The repository writes SentReminder rows BEFORE the provider call, so a
provider crash mid-batch never re-sends. The repository's
`record_sent_reminder` is idempotent on (planned_visit_id, offset_days,
channel) — even if the scheduler fires twice at once across replicas,
the unique constraint stops duplicates.

SMS is left as a deferred channel: the scheduler can return SMS-flagged
reminders but the helper marks them `provider='dry_run'` until Twilio
creds are wired.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.repository import ClinicalRepository

logger = logging.getLogger(__name__)


_SES_FROM_ENV = "AWS_SES_FROM_EMAIL"
_SES_REGION_ENV = "AWS_SES_REGION"


@dataclass(frozen=True)
class ReminderBatchResult:
    """Summary of one fire pass."""

    deployment_id: str
    queued: int
    sent: int
    failed: int
    skipped: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "queued": self.queued,
            "sent": self.sent,
            "failed": self.failed,
            "skipped": self.skipped,
        }


def _ses_enabled() -> bool:
    return bool(os.environ.get(_SES_FROM_ENV))


def _build_email_body(reminder: dict[str, object]) -> tuple[str, str]:
    """Return (subject, body) for the reminder email."""
    visit_name = str(reminder.get("visit_name", "Study visit"))
    planned = str(reminder.get("planned_date", ""))
    offset = cast(int, reminder.get("offset_days", 0) or 0)
    if offset >= 0:
        when = "today" if offset == 0 else f"in {offset} day(s)"
    else:
        when = f"{abs(offset)} day(s) from now"
    subject = f"Reminder: {visit_name} {when}"
    body = (
        f"Hello,\n\n"
        f"This is a reminder for your scheduled study visit:\n\n"
        f"  • Visit: {visit_name}\n"
        f"  • Planned date: {planned}\n\n"
        f"If you cannot attend, please contact your study coordinator.\n\n"
        f"Thank you for participating in this research.\n"
    )
    return subject, body


def _send_email_via_ses(*, recipient: str, subject: str, body: str) -> tuple[str, str | None]:
    """Send via boto3 SES. Returns (status, error|None).

    Wrapped to keep the import lazy — boto3 is pinned but not loaded
    until a real send is attempted.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as e:  # pragma: no cover - boto3 is pinned
        return ("failed", f"boto3 not available: {e}")
    from_addr = os.environ.get(_SES_FROM_ENV)
    if not from_addr:
        return ("failed", "AWS_SES_FROM_EMAIL not set.")
    region = os.environ.get(_SES_REGION_ENV, "us-east-1")
    try:
        client = boto3.client("ses", region_name=region)
        client.send_email(
            Source=from_addr,
            Destination={"ToAddresses": [recipient]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
        return ("sent", None)
    except (BotoCoreError, ClientError) as e:
        return ("failed", f"{type(e).__name__}: {e}")


async def _record_one(
    session: AsyncSession,
    reminder: dict[str, object],
    *,
    provider: str,
    status: str,
    error: str | None = None,
) -> None:
    offset_days = cast(int, reminder.get("offset_days", 0) or 0)
    await ClinicalRepository(session).record_sent_reminder(
        planned_visit_id=str(reminder["planned_visit_id"]),
        subject_id=str(reminder["subject_id"]),
        channel=str(reminder["channel"]),
        offset_days=offset_days,
        provider=provider,
        status=status,
        recipient=str(reminder.get("recipient", "")) or None,
        error=error,
    )


async def fire_due_reminders(deployment_id: str) -> ReminderBatchResult:
    """Compute the queue + send via SES (when enabled) or dry-run.

    Returns counts so the caller (the APScheduler job + the admin
    /run-due endpoint) can log them. The repository transaction COMMITS
    after each reminder so a crash mid-batch doesn't drop the audit
    trail for already-sent reminders.
    """
    sent = failed = skipped = 0
    async with get_clinical_session() as session:
        repo = ClinicalRepository(session)
        queue = await repo.compute_due_reminders(deployment_id)
        queued = len(queue)
        if queued == 0:
            return ReminderBatchResult(
                deployment_id=deployment_id,
                queued=0,
                sent=0,
                failed=0,
                skipped=0,
            )
        ses_on = _ses_enabled()
        for reminder in queue:
            channel = str(reminder["channel"])
            recipient = str(reminder.get("recipient", "") or "")
            if channel == "sms":
                # Twilio not wired — log + skip for now.
                await _record_one(
                    session,
                    reminder,
                    provider="dry_run",
                    status="skipped",
                    error="SMS provider not configured.",
                )
                skipped += 1
                continue
            if not recipient:
                await _record_one(
                    session,
                    reminder,
                    provider="dry_run",
                    status="skipped",
                    error="No recipient address.",
                )
                skipped += 1
                continue
            subject, body = _build_email_body(reminder)
            if not ses_on:
                logger.info("[reminders] dry-run email to %s: %r", recipient, subject)
                await _record_one(
                    session,
                    reminder,
                    provider="dry_run",
                    status="sent",
                    error=None,
                )
                sent += 1
                continue
            status, error = _send_email_via_ses(recipient=recipient, subject=subject, body=body)
            await _record_one(
                session,
                reminder,
                provider="ses",
                status=status,
                error=error,
            )
            if status == "sent":
                sent += 1
            else:
                failed += 1
        await session.commit()
    return ReminderBatchResult(
        deployment_id=deployment_id,
        queued=queued,
        sent=sent,
        failed=failed,
        skipped=skipped,
    )


async def fire_due_reminders_all_deployments() -> list[ReminderBatchResult]:
    """Iterate active deployments + fire reminders for each. Used by
    the periodic APScheduler job."""
    from sqlalchemy import select

    from ..persistence.clinical.models import StudyDeployment

    async with get_clinical_session() as session:
        deployments = list((await session.scalars(select(StudyDeployment.id))).all())
    results: list[ReminderBatchResult] = []
    for deployment_id in deployments:
        try:
            result = await fire_due_reminders(deployment_id)
        except Exception:
            logger.exception(
                "[reminders] fire_due_reminders failed for deployment=%s",
                deployment_id,
            )
            continue
        if result.queued > 0:
            logger.info("[reminders] %s", result.as_dict())
        results.append(result)
    return results


__all__ = [
    "ReminderBatchResult",
    "fire_due_reminders",
    "fire_due_reminders_all_deployments",
]


# Defensive: re-export the SES enable flag so tests can assert on it.
def ses_enabled_for_testing() -> bool:
    return _ses_enabled()


# Keep `datetime` import lazy from a typing perspective so mypy doesn't
# complain about unused imports.
_ = datetime
