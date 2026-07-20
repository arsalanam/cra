"""T1 spend quota — Q1: thread→account attribution + backfill.

Pins the budget-attribution invariants from docs/t1-spend-quota.md:
every thread lands on exactly one Account (trial's account → membership
→ default), the init_db backfill heals legacy NULLs idempotently, and
the new Account budget columns / SpendLedger table round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.database import _backfill_thread_accounts
from research_assistant.persistence.models import (
    DEFAULT_ACCOUNT_NAME,
    Account,
    AccountMember,
    ClinicalTrial,
    SpendLedger,
    Thread,
    User,
)
from research_assistant.persistence.repository import (
    AccountError,
    AccountRepository,
    ThreadRepository,
)


async def _seed_user(db: AsyncSession, *, sub: str) -> User:
    user = User(cognito_sub=sub, email=f"{sub}@example.com")
    db.add(user)
    await db.flush()
    return user


async def _seed_default_account(db: AsyncSession) -> Account:
    account = Account(name=DEFAULT_ACCOUNT_NAME, status="active")
    db.add(account)
    await db.flush()
    return account


def _member(account_id: str, user_id: str, *, role: str, joined: datetime) -> AccountMember:
    return AccountMember(account_id=account_id, user_id=user_id, role=role, joined_at=joined)


# ── resolve_account_for_user ────────────────────────────────────────────


async def test_resolve_prefers_owner_or_admin_membership(
    db_session: AsyncSession,
) -> None:
    user = await _seed_user(db_session, sub="sub-a")
    repo = AccountRepository(db_session)
    plain = await repo.create_account(name="Plain program")
    admin = await repo.create_account(name="Admin program")
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 2, 1, tzinfo=UTC)
    # Older plain membership must lose to a newer admin membership.
    db_session.add(_member(plain.id, user.id, role="member", joined=t0))
    db_session.add(_member(admin.id, user.id, role="admin", joined=t1))
    await db_session.flush()

    assert await repo.resolve_account_for_user(user.id) == admin.id


async def test_resolve_oldest_owner_membership_wins(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, sub="sub-b")
    repo = AccountRepository(db_session)
    first = await repo.create_account(name="First program")
    second = await repo.create_account(name="Second program")
    db_session.add(
        _member(first.id, user.id, role="owner", joined=datetime(2026, 1, 1, tzinfo=UTC))
    )
    db_session.add(
        _member(second.id, user.id, role="owner", joined=datetime(2026, 3, 1, tzinfo=UTC))
    )
    await db_session.flush()

    assert await repo.resolve_account_for_user(user.id) == first.id


async def test_resolve_falls_back_to_any_membership(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, sub="sub-c")
    repo = AccountRepository(db_session)
    account = await repo.create_account(name="Observer program")
    db_session.add(
        _member(account.id, user.id, role="observer", joined=datetime(2026, 1, 1, tzinfo=UTC))
    )
    await db_session.flush()

    assert await repo.resolve_account_for_user(user.id) == account.id


async def test_resolve_falls_back_to_default_account(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, sub="sub-d")
    default = await _seed_default_account(db_session)
    repo = AccountRepository(db_session)

    assert await repo.resolve_account_for_user(user.id) == default.id


async def test_resolve_without_default_account_raises(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, sub="sub-e")
    repo = AccountRepository(db_session)
    with pytest.raises(AccountError, match="Default account missing"):
        await repo.resolve_account_for_user(user.id)


# ── create_thread carries account_id ────────────────────────────────────


async def test_create_thread_stores_account_id(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, sub="sub-f")
    account = await AccountRepository(db_session).create_account(name="Thread program")
    thread = await ThreadRepository(db_session).create_thread(
        title="Budgeted", user_id=user.id, account_id=account.id
    )
    assert thread.account_id == account.id


async def test_create_thread_account_id_defaults_none(db_session: AsyncSession) -> None:
    thread = await ThreadRepository(db_session).create_thread(title="Legacy shape")
    assert thread.account_id is None


# ── init_db backfill ────────────────────────────────────────────────────


async def _seed_backfill_fixture(
    db: AsyncSession,
) -> tuple[Account, Account, Thread, Thread, Thread]:
    """Default account + a trial account; one trial-bound thread, one
    orphan thread, one already-attributed thread."""
    default = await _seed_default_account(db)
    trial_account = Account(name="Trial program", status="active")
    db.add(trial_account)
    await db.flush()
    trial = ClinicalTrial(account_id=trial_account.id, title="A trial")
    db.add(trial)
    await db.flush()

    trial_thread = Thread(title="trial-bound", trial_id=trial.id)
    orphan_thread = Thread(title="orphan")
    kept_thread = Thread(title="already-attributed", account_id=trial_account.id)
    db.add_all([trial_thread, orphan_thread, kept_thread])
    await db.flush()
    return default, trial_account, trial_thread, orphan_thread, kept_thread


async def test_backfill_assigns_trial_account_then_default(
    db_session: AsyncSession,
) -> None:
    default, trial_account, trial_thread, orphan_thread, kept = await _seed_backfill_fixture(
        db_session
    )
    await _backfill_thread_accounts(db_session)

    await db_session.refresh(trial_thread)
    await db_session.refresh(orphan_thread)
    await db_session.refresh(kept)
    assert trial_thread.account_id == trial_account.id
    assert orphan_thread.account_id == default.id
    # Pre-attributed rows are never overwritten.
    assert kept.account_id == trial_account.id


async def test_backfill_is_idempotent(db_session: AsyncSession) -> None:
    default, trial_account, trial_thread, orphan_thread, _ = await _seed_backfill_fixture(
        db_session
    )
    await _backfill_thread_accounts(db_session)
    await _backfill_thread_accounts(db_session)

    await db_session.refresh(trial_thread)
    await db_session.refresh(orphan_thread)
    assert trial_thread.account_id == trial_account.id
    assert orphan_thread.account_id == default.id


async def test_backfill_without_default_account_is_noop(
    db_session: AsyncSession,
) -> None:
    thread = Thread(title="orphan")
    db_session.add(thread)
    await db_session.flush()
    await _backfill_thread_accounts(db_session)
    await db_session.refresh(thread)
    assert thread.account_id is None


# ── budget columns + ledger table shape ─────────────────────────────────


async def test_account_budget_defaults(db_session: AsyncSession) -> None:
    account = await AccountRepository(db_session).create_account(name="Defaults")
    assert account.budget_usd == 0.0
    assert account.budget_start_at is None
    assert account.budget_warn_percent == 80


async def test_spend_ledger_roundtrip(db_session: AsyncSession) -> None:
    account = await AccountRepository(db_session).create_account(name="Ledger program")
    row = SpendLedger(
        account_id=account.id,
        category="turn",
        quantity=1234,
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        usd=0.0042,
    )
    db_session.add(row)
    await db_session.flush()

    fetched = (await db_session.scalars(select(SpendLedger))).one()
    assert fetched.account_id == account.id
    assert fetched.category == "turn"
    assert fetched.quantity == 1234
    assert fetched.usd == pytest.approx(0.0042)
    assert fetched.trial_id is None and fetched.thread_id is None
