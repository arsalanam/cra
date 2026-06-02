"""Account-layer repository (Sprint A1) — CRUD + members + sites + trials.

The account layer is the top-level container for clinical-trial work.
Tests pin the state-invariants that the API endpoints rely on, plus the
backfill behaviour during init_db (which legacy databases will hit
exactly once on first upgrade).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.auth.rbac import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    ScopeType,
)
from research_assistant.persistence.models import (
    DEFAULT_USER_ID,
    Account,
    EcrfStudy,
    User,
)
from research_assistant.persistence.repository import (
    AccountError,
    AccountRepository,
)


async def _seed_user(db: AsyncSession, *, sub: str) -> User:
    user = User(cognito_sub=sub, email=f"{sub}@example.com")
    db.add(user)
    await db.flush()
    return user


# ── Account CRUD ────────────────────────────────────────────────────────


async def test_create_account_auto_adds_owner_member(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    repo = AccountRepository(db_session)
    account = await repo.create_account(name="Cardio program", owner_user_id=owner.id)
    members = await repo.list_members(account.id)
    assert len(members) == 1
    assert members[0].user_id == owner.id
    assert members[0].role == "owner"


async def test_create_account_rejects_duplicate_name(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    await repo.create_account(name="My account")
    with pytest.raises(AccountError, match="already exists"):
        await repo.create_account(name="My account")


async def test_create_account_rejects_blank_name(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    with pytest.raises(AccountError, match="non-empty"):
        await repo.create_account(name="   ")


async def test_list_accounts_for_user_includes_owned_and_member(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    other = await _seed_user(db_session, sub="sub-other")
    repo = AccountRepository(db_session)
    owned = await repo.create_account(name="Owned", owner_user_id=owner.id)
    invited_to = await repo.create_account(name="Invited", owner_user_id=other.id)
    await repo.add_member(account_id=invited_to.id, user_id=owner.id, role="member")
    listed = await repo.list_accounts_for_user(owner.id)
    ids = {a.id for a in listed}
    assert owned.id in ids
    assert invited_to.id in ids


async def test_update_account_rejects_invalid_status(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    with pytest.raises(AccountError, match="Invalid status"):
        await repo.update_account(a.id, status="frozen")


async def test_archive_account_flips_status(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    assert a.status == "active"
    assert await repo.archive_account(a.id) is True
    reloaded = await repo.get_account(a.id)
    assert reloaded is not None and reloaded.status == "archived"


# ── Members ─────────────────────────────────────────────────────────────


async def test_add_member_idempotent_upserts_role(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    invitee = await _seed_user(db_session, sub="sub-i")
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x", owner_user_id=owner.id)
    await repo.add_member(account_id=a.id, user_id=invitee.id, role="observer")
    upgraded = await repo.add_member(account_id=a.id, user_id=invitee.id, role="admin")
    assert upgraded.role == "admin"
    rows = [m for m in await repo.list_members(a.id) if m.user_id == invitee.id]
    assert len(rows) == 1


async def test_add_member_rejects_unknown_role(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    invitee = await _seed_user(db_session, sub="sub-i")
    with pytest.raises(AccountError, match="member role"):
        await repo.add_member(account_id=a.id, user_id=invitee.id, role="visitor")


async def test_remove_member_refuses_owner(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x", owner_user_id=owner.id)
    with pytest.raises(AccountError, match="owner"):
        await repo.remove_member(account_id=a.id, user_id=owner.id)


async def test_remove_member_returns_false_for_missing(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    assert await repo.remove_member(account_id=a.id, user_id="ghost") is False


# ── Account sites ──────────────────────────────────────────────────────


async def test_create_site_rejects_duplicate_code(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    await repo.create_site(a.id, name="Site A", code="A1")
    with pytest.raises(AccountError, match="already used"):
        await repo.create_site(a.id, name="Site B", code="A1")


async def test_create_site_rejects_unknown_account(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    with pytest.raises(AccountError, match="not found"):
        await repo.create_site("ghost", name="Site A")


async def test_update_site_rejects_invalid_status(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    site = await repo.create_site(a.id, name="Site A")
    with pytest.raises(AccountError, match="Invalid status"):
        await repo.update_site(site.id, status="paused")


# ── Trials ─────────────────────────────────────────────────────────────


async def test_create_trial_starts_in_design_status(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="Cardio RCT")
    assert t.status == "design"
    assert t.account_id == a.id


async def test_create_trial_rejects_invalid_phase(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    with pytest.raises(AccountError, match="phase"):
        await repo.create_trial(a.id, title="x", phase="phase_5")


async def test_update_trial_rejects_invalid_status(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="x")
    with pytest.raises(AccountError, match="Invalid status"):
        await repo.update_trial(t.id, status="paused")


async def test_update_trial_status_lifecycle(
    db_session: AsyncSession,
) -> None:
    """All 5 status values are accepted; transitions aren't restricted
    at the repo layer (the trial-status state machine is operator-
    driven in v1)."""
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="x")
    for s in ("draft", "deployed", "locked", "archived"):
        updated = await repo.update_trial(t.id, status=s)
        assert updated is not None and updated.status == s


async def test_list_trials_filters_by_status(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t1 = await repo.create_trial(a.id, title="t1")
    t2 = await repo.create_trial(a.id, title="t2")
    await repo.update_trial(t2.id, status="deployed")
    deployed = await repo.list_trials_for_account(a.id, status="deployed")
    assert [t.id for t in deployed] == [t2.id]
    design = await repo.list_trials_for_account(a.id, status="design")
    assert [t.id for t in design] == [t1.id]


# ── Trial ↔ Site assignment ────────────────────────────────────────────


async def test_assign_site_to_trial_idempotent_upsert(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="x")
    site = await repo.create_site(a.id, name="Site A")
    first = await repo.assign_site_to_trial(trial_id=t.id, account_site_id=site.id)
    second = await repo.assign_site_to_trial(
        trial_id=t.id, account_site_id=site.id, notes="updated"
    )
    assert first.id == second.id
    assert second.notes == "updated"


async def test_assign_site_rejects_cross_account(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a1 = await repo.create_account(name="acc1")
    a2 = await repo.create_account(name="acc2")
    t = await repo.create_trial(a1.id, title="x")
    site = await repo.create_site(a2.id, name="Site")
    with pytest.raises(AccountError, match="same account"):
        await repo.assign_site_to_trial(trial_id=t.id, account_site_id=site.id)


async def test_assign_inactive_site_rejected(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="x")
    site = await repo.create_site(a.id, name="Site")
    await repo.update_site(site.id, status="inactive")
    with pytest.raises(AccountError, match="inactive"):
        await repo.assign_site_to_trial(trial_id=t.id, account_site_id=site.id)


async def test_unassign_returns_false_for_missing(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="x")
    assert await repo.unassign_site_from_trial(trial_id=t.id, account_site_id="ghost") is False


# ── Cross-store resolution ────────────────────────────────────────────


async def test_resolve_trial_for_ecrf_study_returns_trial(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="x")
    t = await repo.create_trial(a.id, title="x")
    study = EcrfStudy(name="study", trial_id=t.id)
    db_session.add(study)
    await db_session.flush()
    resolved = await repo.resolve_trial_for_ecrf_study(study.id)
    assert resolved is not None and resolved.id == t.id


async def test_resolve_trial_for_ecrf_study_returns_none_legacy(
    db_session: AsyncSession,
) -> None:
    repo = AccountRepository(db_session)
    study = EcrfStudy(name="legacy", trial_id=None)
    db_session.add(study)
    await db_session.flush()
    assert await repo.resolve_trial_for_ecrf_study(study.id) is None


# ── Default-account backfill (init_db) ────────────────────────────────


async def test_backfill_creates_default_account_and_wraps_studies(
    db_session: AsyncSession,
) -> None:
    """The init_db backfill creates the Default Account on first run +
    wraps every legacy EcrfStudy in a ClinicalTrial under it. Replays
    the helper directly so we don't have to spin up a fresh DB."""
    from research_assistant.persistence.database import _backfill_account_layer

    # Seed legacy data: default user + 2 studies without trial_id.
    default_user = User(id=DEFAULT_USER_ID, name="Default User")
    db_session.add(default_user)
    await db_session.flush()
    s1 = EcrfStudy(name="legacy-1", status="draft")
    s2 = EcrfStudy(name="legacy-2", status="active")
    db_session.add_all([s1, s2])
    await db_session.flush()

    await _backfill_account_layer(db_session)
    repo = AccountRepository(db_session)

    # Default Account exists + owns default user as owner-member.
    from sqlalchemy import select

    account = (
        await db_session.scalars(select(Account).where(Account.name == "Default research program"))
    ).first()
    assert account is not None
    assert account.owner_user_id == DEFAULT_USER_ID
    members = await repo.list_members(account.id)
    assert any(m.user_id == DEFAULT_USER_ID and m.role == "owner" for m in members)

    # Each legacy study was wrapped in a ClinicalTrial under the Default.
    trials = await repo.list_trials_for_account(account.id)
    titles = {t.title for t in trials}
    assert titles == {"legacy-1", "legacy-2"}
    # Status mapping: draft → design, active → deployed.
    status_by_title = {t.title: t.status for t in trials}
    assert status_by_title["legacy-1"] == "design"
    assert status_by_title["legacy-2"] == "deployed"
    # And the EcrfStudy.trial_id is now populated.
    await db_session.refresh(s1)
    await db_session.refresh(s2)
    assert s1.trial_id is not None
    assert s2.trial_id is not None


async def test_backfill_is_idempotent_on_second_run(
    db_session: AsyncSession,
) -> None:
    from research_assistant.persistence.database import _backfill_account_layer

    db_session.add(User(id=DEFAULT_USER_ID, name="Default User"))
    await db_session.flush()
    db_session.add(EcrfStudy(name="legacy", status="draft"))
    await db_session.flush()

    await _backfill_account_layer(db_session)
    repo = AccountRepository(db_session)
    from sqlalchemy import select

    account = (
        await db_session.scalars(select(Account).where(Account.name == "Default research program"))
    ).first()
    assert account is not None
    before = await repo.list_trials_for_account(account.id)

    # Second invocation — nothing new should land.
    await _backfill_account_layer(db_session)
    after = await repo.list_trials_for_account(account.id)
    assert {t.id for t in before} == {t.id for t in after}


# ── RBAC matrix ───────────────────────────────────────────────────────


def test_account_perms_added_to_scope_enum() -> None:
    """The two new scope types are part of the ScopeType enum."""
    assert ScopeType.ACCOUNT.value == "account"
    assert ScopeType.TRIAL.value == "trial"


def test_researcher_can_manage_accounts_and_trials() -> None:
    perms = ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.ACCOUNT_READ in perms
    assert Permission.ACCOUNT_MANAGE in perms
    assert Permission.TRIAL_READ in perms
    assert Permission.TRIAL_MANAGE in perms


def test_auditor_can_read_but_not_manage() -> None:
    perms = ROLE_PERMISSIONS[Role.AUDITOR]
    assert Permission.ACCOUNT_READ in perms
    assert Permission.TRIAL_READ in perms
    assert Permission.ACCOUNT_MANAGE not in perms
    assert Permission.TRIAL_MANAGE not in perms


def test_student_carries_no_account_perms_by_default() -> None:
    perms = ROLE_PERMISSIONS[Role.STUDENT]
    assert Permission.ACCOUNT_READ not in perms
    assert Permission.ACCOUNT_MANAGE not in perms
    assert Permission.TRIAL_READ not in perms
    assert Permission.TRIAL_MANAGE not in perms


def test_admin_carries_every_perm_including_account_layer() -> None:
    perms = ROLE_PERMISSIONS[Role.ADMIN]
    for p in (
        Permission.ACCOUNT_READ,
        Permission.ACCOUNT_MANAGE,
        Permission.TRIAL_READ,
        Permission.TRIAL_MANAGE,
    ):
        assert p in perms


# ── Router smoke ──────────────────────────────────────────────────────


def test_router_factory_pins_paths() -> None:
    from research_assistant.web.accounts import create_accounts_router

    router = create_accounts_router()
    paths = {r.path for r in router.routes}
    # CRUD on accounts
    assert "/accounts" in paths
    assert "/accounts/{account_id}" in paths
    # Members
    assert "/accounts/{account_id}/members" in paths
    assert "/accounts/{account_id}/members/{uid}" in paths
    # Sites
    assert "/accounts/{account_id}/sites" in paths
    assert "/account-sites/{site_id}" in paths
    # Trials
    assert "/accounts/{account_id}/trials" in paths
    assert "/trials/{trial_id}" in paths
    assert "/trials/{trial_id}/sites" in paths
    assert "/trials/{trial_id}/sites/{site_id}" in paths


def test_router_dtos_round_trip() -> None:
    from datetime import UTC, datetime

    from research_assistant.web.accounts import (
        AccountCreate,
        AccountSiteCreate,
        AccountSiteView,
        AccountView,
        MemberAdd,
        MemberView,
        TrialCreate,
        TrialPatch,
        TrialView,
    )

    now = datetime.now(UTC)
    create = AccountCreate(name="Cardio program", description="dept")
    assert create.name == "Cardio program"

    av = AccountView(
        id="a1",
        name="x",
        description="",
        status="active",
        owner_user_id="u1",
        n_members=3,
        n_sites=2,
        n_trials=4,
        n_active_trials=3,
        n_locked_trials=1,
        created_at=now,
        updated_at=now,
    )
    assert av.n_active_trials == 3

    site_in = AccountSiteCreate(name="University Hospital", code="UH")
    assert site_in.code == "UH"
    site_out = AccountSiteView(
        id="s1",
        account_id="a1",
        name="x",
        code=None,
        address="",
        contact_email=None,
        pi_name=None,
        status="active",
        created_at=now,
    )
    assert site_out.status == "active"

    member_add = MemberAdd(email="hi@example.com", role="member")
    assert member_add.role == "member"
    mv = MemberView(user_id="u2", role="member", joined_at=now)
    assert mv.email is None

    trial_in = TrialCreate(title="Cardio RCT", phase="phase_2")
    assert trial_in.phase == "phase_2"
    trial_out = TrialView(
        id="t1",
        account_id="a1",
        title="x",
        sponsor="",
        indication="",
        phase=None,
        protocol_id=None,
        status="design",
        registration_thread_id=None,
        irb_thread_id=None,
        sap_thread_id=None,
        csr_thread_id=None,
        manuscript_thread_id=None,
        created_at=now,
        updated_at=now,
    )
    assert trial_out.status == "design"

    patch = TrialPatch(status="deployed", registration_thread_id="thread-1")
    assert patch.status == "deployed"
