"""Sprint A2 wiring tests — auto-status hooks, ownership transfer,
artefact binding, cross-store resolver.

The auto-status hooks (publish/deploy/lock) themselves fire from the
endpoint layer; here we test the underlying helpers + the no-regression
invariant.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.models import (
    Thread,
    User,
)
from research_assistant.persistence.repository import (
    AccountError,
    AccountRepository,
)
from research_assistant.services.trial_status import (
    _can_advance_to,
    _set_trial_status,
)


async def _seed_user(db: AsyncSession, *, sub: str) -> User:
    user = User(cognito_sub=sub, email=f"{sub}@example.com")
    db.add(user)
    await db.flush()
    return user


# ── Auto-status invariants ────────────────────────────────────────────


def test_can_advance_through_lifecycle() -> None:
    assert _can_advance_to("design", "draft") is True
    assert _can_advance_to("design", "deployed") is True
    assert _can_advance_to("design", "locked") is True
    assert _can_advance_to("draft", "deployed") is True
    assert _can_advance_to("deployed", "locked") is True


def test_cannot_regress() -> None:
    assert _can_advance_to("draft", "design") is False
    assert _can_advance_to("deployed", "draft") is False
    assert _can_advance_to("locked", "deployed") is False
    assert _can_advance_to("locked", "design") is False


def test_archived_is_terminal() -> None:
    assert _can_advance_to("archived", "draft") is False
    assert _can_advance_to("archived", "locked") is False


def test_idempotent_when_already_at_target() -> None:
    """Same-status target is treated as no-op (not an advance)."""
    for s in ("design", "draft", "deployed", "locked"):
        assert _can_advance_to(s, s) is False


async def test_set_trial_status_advances(db_session: AsyncSession) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    # Sanity: created in 'design'.
    assert t.status == "design"


async def test_set_trial_status_no_regression(
    db_session: AsyncSession,
) -> None:
    """advancing 'deployed' → 'draft' is a no-op + returns False."""
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    await repo.update_trial(t.id, status="deployed")
    changed = await _set_trial_status(t.id, target="draft", session=db_session)
    assert changed is False
    refreshed = await repo.get_trial(t.id)
    assert refreshed is not None
    assert refreshed.status == "deployed"


async def test_set_trial_status_advances_when_eligible(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    # design → draft is a legitimate advance.
    changed = await _set_trial_status(t.id, target="draft", session=db_session)
    assert changed is True
    refreshed = await repo.get_trial(t.id)
    assert refreshed is not None
    assert refreshed.status == "draft"


async def test_set_trial_status_archived_untouched(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    await repo.update_trial(t.id, status="archived")
    for target in ("draft", "deployed", "locked"):
        changed = await _set_trial_status(t.id, target=target, session=db_session)
        assert changed is False
    refreshed = await repo.get_trial(t.id)
    assert refreshed is not None
    assert refreshed.status == "archived"


# ── Ownership transfer ───────────────────────────────────────────────


async def test_transfer_ownership_promotes_and_demotes(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    successor = await _seed_user(db_session, sub="sub-successor")
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x", owner_user_id=owner.id)
    await repo.add_member(account_id=a.id, user_id=successor.id, role="admin")

    updated = await repo.transfer_ownership(account_id=a.id, new_owner_user_id=successor.id)
    assert updated.owner_user_id == successor.id
    members = await repo.list_members(a.id)
    by_uid = {m.user_id: m.role for m in members}
    assert by_uid[owner.id] == "admin"  # demoted
    assert by_uid[successor.id] == "owner"  # promoted


async def test_transfer_ownership_rejects_non_member(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    stranger = await _seed_user(db_session, sub="sub-stranger")
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x", owner_user_id=owner.id)
    with pytest.raises(AccountError, match="member"):
        await repo.transfer_ownership(account_id=a.id, new_owner_user_id=stranger.id)


async def test_transfer_ownership_idempotent_same_owner(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x", owner_user_id=owner.id)
    # No-op when target is current owner.
    updated = await repo.transfer_ownership(account_id=a.id, new_owner_user_id=owner.id)
    assert updated.owner_user_id == owner.id


# ── Artefact binding ─────────────────────────────────────────────────


async def test_bind_artefact_writes_to_matching_column(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    thread = Thread(title="manuscript thread")
    db_session.add(thread)
    await db_session.flush()
    updated = await repo.bind_trial_artefact(trial_id=t.id, kind="manuscript", thread_id=thread.id)
    assert updated.manuscript_thread_id == thread.id
    assert updated.registration_thread_id is None


async def test_bind_artefact_can_unbind(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    thread = Thread(title="t")
    db_session.add(thread)
    await db_session.flush()
    await repo.bind_trial_artefact(trial_id=t.id, kind="irb", thread_id=thread.id)
    updated = await repo.bind_trial_artefact(trial_id=t.id, kind="irb", thread_id=None)
    assert updated.irb_thread_id is None


async def test_bind_artefact_rejects_unknown_kind(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    with pytest.raises(AccountError, match="kind"):
        await repo.bind_trial_artefact(trial_id=t.id, kind="lay_summary", thread_id=None)


async def test_bind_artefact_rejects_unknown_thread(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="t")
    with pytest.raises(AccountError, match="Thread"):
        await repo.bind_trial_artefact(trial_id=t.id, kind="csr", thread_id="ghost")


# ── Router smoke ─────────────────────────────────────────────────────


def test_router_pins_a2_paths() -> None:
    from research_assistant.web.accounts import create_accounts_router

    router = create_accounts_router()
    paths = {r.path for r in router.routes}
    assert "/accounts/{account_id}/transfer-ownership" in paths
    assert "/trials/{trial_id}/artefacts" in paths
    assert "/deployments/{deployment_id}/trial" in paths


def test_studyin_carries_trial_id() -> None:
    from datetime import UTC, datetime

    from research_assistant.web.ecrf import StudyIn, StudyOut

    body = StudyIn(name="x", trial_id="t-123")
    assert body.trial_id == "t-123"
    out = StudyOut(
        id="s1",
        name="x",
        protocol_id=None,
        description=None,
        status="draft",
        trial_id="t-123",
        created_at=datetime.now(UTC),
    )
    assert out.trial_id == "t-123"
