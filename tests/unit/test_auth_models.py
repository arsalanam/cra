
"""Phase B schema tests: cognito_sub, user_roles, pending_invitations.

Covers both the ORM-level behaviour (uniqueness, role constraint, cascade)
and the additive-migration path an existing pre-auth database takes when
the `users.cognito_sub` column and its unique index are added at startup.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from research_assistant.persistence.database import _apply_additive_migrations
from research_assistant.persistence.models import (
    PendingInvitation,
    User,
    UserRole,
)

# ── cognito_sub ────────────────────────────────────────────────────────────


async def test_cognito_sub_round_trips(db_session: AsyncSession) -> None:
    user = User(email="a@example.com", cognito_sub="sub-123")
    db_session.add(user)
    await db_session.commit()

    fetched = await db_session.get(User, user.id)
    assert fetched is not None
    assert fetched.cognito_sub == "sub-123"


async def test_cognito_sub_is_unique(db_session: AsyncSession) -> None:
    db_session.add(User(email="a@example.com", cognito_sub="dupe"))
    db_session.add(User(email="b@example.com", cognito_sub="dupe"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_multiple_null_cognito_sub_allowed(db_session: AsyncSession) -> None:
    # NULLs are distinct — legacy/pre-auth rows can coexist.
    db_session.add(User(email="a@example.com", cognito_sub=None))
    db_session.add(User(email="b@example.com", cognito_sub=None))
    await db_session.commit()  # must not raise


# ── user_roles ───────────────────────────────────────────────────────────


async def test_user_roles_relationship(db_session: AsyncSession) -> None:
    user = User(email="r@example.com", cognito_sub="sub-r")
    user.roles.append(UserRole(role="admin"))
    user.roles.append(UserRole(role="researcher"))
    db_session.add(user)
    await db_session.commit()

    fetched = await db_session.get(User, user.id)
    assert fetched is not None
    assert {r.role for r in fetched.roles} == {"admin", "researcher"}


async def test_duplicate_role_rejected(db_session: AsyncSession) -> None:
    user = User(email="d@example.com", cognito_sub="sub-d")
    user.roles.append(UserRole(role="admin"))
    user.roles.append(UserRole(role="admin"))  # same (user_id, role)
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_deleting_user_cascades_roles(db_session: AsyncSession) -> None:
    user = User(email="c@example.com", cognito_sub="sub-c")
    user.roles.append(UserRole(role="admin"))
    db_session.add(user)
    await db_session.commit()

    await db_session.delete(user)  # ORM-level cascade (all, delete-orphan)
    await db_session.commit()

    remaining = (await db_session.execute(text("SELECT COUNT(*) FROM user_roles"))).scalar()
    assert remaining == 0


# ── pending_invitations ──────────────────────────────────────────────────


async def test_pending_invitation_round_trips(db_session: AsyncSession) -> None:
    inv = PendingInvitation(email="invitee@example.com", roles_json=json.dumps(["admin"]))
    db_session.add(inv)
    await db_session.commit()

    fetched = await db_session.get(PendingInvitation, inv.id)
    assert fetched is not None
    assert json.loads(fetched.roles_json) == ["admin"]
    assert fetched.consumed_at is None


async def test_pending_invitation_default_role(db_session: AsyncSession) -> None:
    inv = PendingInvitation(email="default@example.com")
    db_session.add(inv)
    await db_session.commit()
    assert json.loads(inv.roles_json) == ["researcher"]


async def test_pending_invitation_email_unique(db_session: AsyncSession) -> None:
    db_session.add(PendingInvitation(email="same@example.com"))
    db_session.add(PendingInvitation(email="same@example.com"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


# ── additive migration path (the running-Postgres scenario) ────────────────


async def test_migration_adds_cognito_sub_to_legacy_users_table() -> None:
    """A pre-auth `users` table (no cognito_sub) gains the column + unique index."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        # Simulate the old schema: users without cognito_sub.
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE users ("
                " id TEXT PRIMARY KEY, email TEXT, name TEXT, is_admin BOOLEAN,"
                " created_at TIMESTAMP)"
            )

        await _apply_additive_migrations(engine)

        def _check(sync_conn: object) -> None:
            insp = inspect(sync_conn)
            cols = {c["name"] for c in insp.get_columns("users")}
            assert "cognito_sub" in cols
            assert "is_admin" not in cols  # Phase D drops it
            index_names = {ix["name"] for ix in insp.get_indexes("users")}
            assert "ix_users_cognito_sub" in index_names

        async with engine.begin() as conn:
            await conn.run_sync(_check)

        # Idempotent: a second run must not raise (column + index already there).
        await _apply_additive_migrations(engine)
    finally:
        await engine.dispose()
