"""Sprint A2.5 tests — chat handoff seeds carry trial_id, dispatch
auto-bind on terminal artefacts.

Coverage:
  - Thread.trial_id persists round-trip (repo)
  - _KIND_TO_ARTEFACT_SLOT mapping
  - _maybe_autobind_artefact: happy path per kind, no-op when slot taken,
    no-op when trial_id is None, no-op when kind is unmapped, no-op when
    Trial deleted
  - Endpoint integration via the router factory (smoke pin on shape)
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.models import Thread, User
from research_assistant.persistence.repository import (
    AccountRepository,
    ThreadRepository,
)
from research_assistant.web.dispatch import (
    _KIND_TO_ARTEFACT_SLOT,
    _maybe_autobind_artefact,
)


async def _seed_user(db: AsyncSession, *, sub: str) -> User:
    user = User(cognito_sub=sub, email=f"{sub}@example.com")
    db.add(user)
    await db.flush()
    return user


# ── ThreadRepository.create_thread accepts trial_id ──────────────────


async def test_create_thread_persists_trial_id(db_session: AsyncSession) -> None:
    acct_repo = AccountRepository(db_session)
    a = await acct_repo.create_account(name="acme")
    t = await acct_repo.create_trial(a.id, title="trial-x")
    thread_repo = ThreadRepository(db_session)
    thread = await thread_repo.create_thread(title="manuscript", trial_id=t.id)
    refreshed = await thread_repo.get_thread(thread.id)
    assert refreshed is not None
    assert refreshed.trial_id == t.id


async def test_create_thread_trial_id_optional(db_session: AsyncSession) -> None:
    """Default behaviour unchanged when trial_id omitted."""
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread(title="general qa")
    refreshed = await repo.get_thread(thread.id)
    assert refreshed is not None
    assert refreshed.trial_id is None


# ── _KIND_TO_ARTEFACT_SLOT mapping ───────────────────────────────────


def test_kind_to_artefact_slot_covers_5_slots() -> None:
    assert _KIND_TO_ARTEFACT_SLOT == {
        "registration_document": "registration",
        "irb_document": "irb",
        "sap_document": "sap",
        "csr_document": "csr",
        "manuscript_draft": "manuscript",
    }


# ── _maybe_autobind_artefact ─────────────────────────────────────────


@pytest.mark.parametrize(
    "kind,column",
    [
        ("registration_document", "registration_thread_id"),
        ("irb_document", "irb_thread_id"),
        ("sap_document", "sap_thread_id"),
        ("csr_document", "csr_thread_id"),
        ("manuscript_draft", "manuscript_thread_id"),
    ],
)
async def test_autobind_writes_to_correct_slot(
    db_session: AsyncSession, kind: str, column: str
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    thread = Thread(title="terminal-card thread", trial_id=t.id)
    db_session.add(thread)
    await db_session.flush()

    await _maybe_autobind_artefact(db_session, thread_id=thread.id, trial_id=t.id, output_kind=kind)

    refreshed = await repo.get_trial(t.id)
    assert refreshed is not None
    assert getattr(refreshed, column) == thread.id
    # Other slots untouched.
    other_slots = {
        "registration_thread_id",
        "irb_thread_id",
        "sap_thread_id",
        "csr_thread_id",
        "manuscript_thread_id",
    } - {column}
    for s in other_slots:
        assert getattr(refreshed, s) is None


async def test_autobind_skips_when_trial_id_none(db_session: AsyncSession) -> None:
    """No trial on the Thread → no-op even with a terminal kind."""
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    thread = Thread(title="orphan")
    db_session.add(thread)
    await db_session.flush()

    await _maybe_autobind_artefact(
        db_session,
        thread_id=thread.id,
        trial_id=None,
        output_kind="manuscript_draft",
    )

    refreshed = await repo.get_trial(t.id)
    assert refreshed is not None
    assert refreshed.manuscript_thread_id is None


async def test_autobind_skips_unmapped_kind(db_session: AsyncSession) -> None:
    """Intermediate / non-terminal kinds shouldn't bind anything."""
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    thread = Thread(title="intake", trial_id=t.id)
    db_session.add(thread)
    await db_session.flush()

    for kind in ("manuscript_intake", "csr_synopsis", "pico", None):
        await _maybe_autobind_artefact(
            db_session,
            thread_id=thread.id,
            trial_id=t.id,
            output_kind=kind,
        )

    refreshed = await repo.get_trial(t.id)
    assert refreshed is not None
    assert refreshed.manuscript_thread_id is None
    assert refreshed.csr_thread_id is None


async def test_autobind_idempotent_when_slot_populated(
    db_session: AsyncSession,
) -> None:
    """When the slot is already bound to another thread, leave it alone."""
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    first = Thread(title="first manuscript", trial_id=t.id)
    second = Thread(title="second manuscript", trial_id=t.id)
    db_session.add_all([first, second])
    await db_session.flush()

    # Pre-bind first thread.
    await repo.bind_trial_artefact(trial_id=t.id, kind="manuscript", thread_id=first.id)
    # Second thread reaches the same terminal kind.
    await _maybe_autobind_artefact(
        db_session,
        thread_id=second.id,
        trial_id=t.id,
        output_kind="manuscript_draft",
    )

    refreshed = await repo.get_trial(t.id)
    assert refreshed is not None
    # First binding preserved, not overwritten.
    assert refreshed.manuscript_thread_id == first.id


async def test_autobind_skips_when_trial_missing(db_session: AsyncSession) -> None:
    """Trial deleted between turn start and dispatch → no crash, no bind."""
    thread = Thread(title="x", trial_id="ghost-trial-id")
    db_session.add(thread)
    await db_session.flush()
    # No exception even though the trial doesn't exist.
    await _maybe_autobind_artefact(
        db_session,
        thread_id=thread.id,
        trial_id="ghost-trial-id",
        output_kind="manuscript_draft",
    )


# ── Router-level smoke ───────────────────────────────────────────────


def test_thread_create_dto_accepts_trial_id() -> None:
    from research_assistant.web.threads import ThreadCreate

    body = ThreadCreate(title="x", trial_id="t-1")
    assert body.trial_id == "t-1"
    # Defaults to None.
    assert ThreadCreate(title="x").trial_id is None


def test_thread_out_carries_trial_id() -> None:
    from datetime import UTC, datetime

    from research_assistant.web.threads import ThreadOut

    out = ThreadOut(
        id="th-1",
        title="x",
        summary=None,
        workflow=None,
        trial_id="t-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        message_count=0,
    )
    assert out.trial_id == "t-1"
