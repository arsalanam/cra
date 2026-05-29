"""RBAC repository methods — RoleAssignment grant/revoke + effective perms.

Complements `test_user_repository.py` (which covered the pre-RBAC-1 login
matcher) with the scope-aware surface added in this slice.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.auth.rbac import Permission, ScopeType
from research_assistant.persistence.models import (
    PendingInvitation,
    RoleAssignment,
    User,
    UserRole,
)
from research_assistant.persistence.user_repository import UserRepository


async def _seed_user(db: AsyncSession, *, sub: str = "sub-A") -> User:
    user = User(cognito_sub=sub, email=f"{sub}@example.com")
    db.add(user)
    await db.flush()
    return user


async def test_grant_role_normalises_legacy_data_entry_alias(
    db_session: AsyncSession,
) -> None:
    """`data_entry` is the legacy role string used by eCRF E1 invites; it
    must land in role_assignments as `coordinator`."""
    user = await _seed_user(db_session)
    repo = UserRepository(db_session)
    assignment = await repo.grant_role(user.id, "data_entry")
    assert assignment.role == "coordinator"
    assert assignment.scope_type == ScopeType.GLOBAL.value
    assert assignment.scope_id is None


async def test_grant_role_rejects_unknown_role(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    repo = UserRepository(db_session)
    with pytest.raises(ValueError, match="Unknown role"):
        await repo.grant_role(user.id, "nonexistent")


async def test_grant_role_is_idempotent_on_same_scope(
    db_session: AsyncSession,
) -> None:
    """Re-granting the same (user, role, scope) returns the existing row,
    never duplicates — the unique constraint would block it anyway, but we
    want a clean no-op rather than an IntegrityError surfacing to callers."""
    user = await _seed_user(db_session)
    repo = UserRepository(db_session)
    a1 = await repo.grant_role(user.id, "researcher")
    a2 = await repo.grant_role(user.id, "researcher")
    assert a1.id == a2.id
    rows = (
        (await db_session.execute(select(RoleAssignment).where(RoleAssignment.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_grant_role_requires_scope_id_for_study_scope(
    db_session: AsyncSession,
) -> None:
    user = await _seed_user(db_session)
    repo = UserRepository(db_session)
    with pytest.raises(ValueError, match="scope_id"):
        await repo.grant_role(user.id, "data_manager", scope_type="study")


async def test_grant_role_rejects_scope_id_at_global(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    repo = UserRepository(db_session)
    with pytest.raises(ValueError, match="scope_id=None"):
        await repo.grant_role(user.id, "researcher", scope_type="global", scope_id="X")


async def test_revoke_returns_false_for_missing_row(db_session: AsyncSession) -> None:
    assert await UserRepository(db_session).revoke_role("nope") is False


async def test_revoke_deletes_the_assignment(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    repo = UserRepository(db_session)
    grant = await repo.grant_role(user.id, "researcher")
    assert await repo.revoke_role(grant.id) is True
    assert await db_session.get(RoleAssignment, grant.id) is None


async def test_effective_permissions_unknown_sub_is_empty(
    db_session: AsyncSession,
) -> None:
    perms = await UserRepository(db_session).effective_permissions_for_sub("ghost")
    assert perms == frozenset()


async def test_effective_permissions_for_student_is_meta_plus_general(
    db_session: AsyncSession,
) -> None:
    user = await _seed_user(db_session, sub="sub-student")
    repo = UserRepository(db_session)
    await repo.grant_role(user.id, "student")

    perms = await repo.effective_permissions_for_sub("sub-student")
    assert perms == frozenset({Permission.SKILL_META_ANALYSIS, Permission.SKILL_GENERAL_QA})


async def test_effective_permissions_merges_legacy_user_roles(
    db_session: AsyncSession,
) -> None:
    """A pre-RBAC-1 row in user_roles must continue granting perms during
    the migration window — even without a backfilled RoleAssignment."""
    user = await _seed_user(db_session, sub="sub-legacy")
    db_session.add(UserRole(user_id=user.id, role="admin"))
    await db_session.flush()

    perms = await UserRepository(db_session).effective_permissions_for_sub("sub-legacy")
    assert Permission.USER_MANAGE in perms
    assert Permission.SKILL_ECRF_DESIGN in perms


async def test_list_roles_dedupes_across_user_roles_and_assignments(
    db_session: AsyncSession,
) -> None:
    """The reads union both tables; the same role on both sides must show
    once, not twice — `/auth/me` and admin UIs would otherwise show
    duplicates after a migration backfill that runs alongside legacy
    writes."""
    user = await _seed_user(db_session, sub="sub-dup")
    db_session.add(UserRole(user_id=user.id, role="researcher"))
    await db_session.flush()
    await UserRepository(db_session).grant_role(user.id, "researcher")

    roles = await UserRepository(db_session).list_roles(user.id)
    assert roles == ["researcher"]


async def test_resolve_login_grants_via_role_assignment(
    db_session: AsyncSession,
) -> None:
    """The invitation matcher should write to role_assignments, not the
    legacy user_roles table."""
    repo = UserRepository(db_session)
    await repo.create_invitation("new@example.com", ["admin", "researcher"])
    user = await repo.resolve_login(cognito_sub="sub-new", email="new@example.com")

    rows = (
        (await db_session.execute(select(RoleAssignment).where(RoleAssignment.user_id == user.id)))
        .scalars()
        .all()
    )
    assert {r.role for r in rows} == {"admin", "researcher"}
    # All grants land at global scope by default
    assert all(r.scope_type == ScopeType.GLOBAL.value for r in rows)
    assert all(r.scope_id is None for r in rows)

    # Invitation is consumed
    inv = (
        await db_session.execute(
            select(PendingInvitation).where(PendingInvitation.email == "new@example.com")
        )
    ).scalar_one()
    assert inv.consumed_at is not None


async def test_resolve_login_skips_unknown_roles_on_invitation(
    db_session: AsyncSession,
) -> None:
    """An admin typo on the invitation must not lock the invitee out — log
    + skip, grant the other valid roles."""
    repo = UserRepository(db_session)
    await repo.create_invitation("partial@example.com", ["researcher", "garbage"])
    user = await repo.resolve_login(cognito_sub="sub-partial", email="partial@example.com")

    rows = (
        (await db_session.execute(select(RoleAssignment).where(RoleAssignment.user_id == user.id)))
        .scalars()
        .all()
    )
    assert {r.role for r in rows} == {"researcher"}
