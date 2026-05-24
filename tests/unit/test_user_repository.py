"""Phase C: UserRepository login matcher + invitation upsert."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.models import PendingInvitation, User
from research_assistant.persistence.user_repository import (
    NoInvitationError,
    UserRepository,
)


async def test_first_login_with_invitation_provisions_user_and_roles(
    db_session: AsyncSession,
) -> None:
    repo = UserRepository(db_session)
    await repo.create_invitation("new@example.com", ["admin", "researcher"])

    user = await repo.resolve_login(cognito_sub="sub-1", email="new@example.com")

    assert user.cognito_sub == "sub-1"
    assert user.email == "new@example.com"
    assert set(await repo.list_roles(user.id)) == {"admin", "researcher"}

    inv = (
        await db_session.execute(
            select(PendingInvitation).where(PendingInvitation.email == "new@example.com")
        )
    ).scalar_one()
    assert inv.consumed_at is not None  # invitation consumed


async def test_returning_user_is_idempotent(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    await repo.create_invitation("x@example.com", ["researcher"])

    first = await repo.resolve_login(cognito_sub="sub-x", email="x@example.com")
    second = await repo.resolve_login(cognito_sub="sub-x", email="x@example.com")

    assert first.id == second.id
    assert await repo.list_roles(first.id) == ["researcher"]  # not duplicated


async def test_no_invitation_is_rejected(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    with pytest.raises(NoInvitationError) as exc:
        await repo.resolve_login(cognito_sub="sub-z", email="stranger@example.com")
    assert exc.value.email == "stranger@example.com"


async def test_binds_existing_user_by_email(db_session: AsyncSession) -> None:
    seeded = User(email="seed@example.com")  # no cognito_sub yet
    db_session.add(seeded)
    await db_session.flush()

    repo = UserRepository(db_session)
    user = await repo.resolve_login(cognito_sub="sub-seed", email="seed@example.com")

    assert user.id == seeded.id
    assert user.cognito_sub == "sub-seed"


async def test_create_invitation_upsert_refreshes_and_reopens(
    db_session: AsyncSession,
) -> None:
    repo = UserRepository(db_session)
    inv1 = await repo.create_invitation("up@example.com", ["researcher"])
    await repo.resolve_login(cognito_sub="sub-up", email="up@example.com")  # consumes it

    inv2 = await repo.create_invitation("up@example.com", ["admin"])

    assert inv1.id == inv2.id  # upsert, not a duplicate row
    assert json.loads(inv2.roles_json) == ["admin"]
    assert inv2.consumed_at is None  # reopened
