"""User identity + role provisioning (Phase C).

The login matcher (`resolve_login`) binds a validated Cognito identity to
a local `User` row. The app is admin-invitation-only: a first-time login
only succeeds if an admin previously recorded a `PendingInvitation` for
that email (or a pre-existing local user shares the email). Roles listed
on the invitation are granted on first login.

Cognito owns identity; these rows own authorization (Phase D). The
boolean `User.is_admin` has been dropped in favour of `user_roles`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PendingInvitation, User, UserRole

logger = logging.getLogger(__name__)


class NoInvitationError(Exception):
    """First login with no matching invitation — admin-invitation-only."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"No invitation found for {email!r}")


def _parse_roles(roles_json: str) -> list[str]:
    try:
        roles = json.loads(roles_json)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(r) for r in roles if isinstance(r, str)]


class UserRepository:
    """Data access for users, roles, and pending invitations."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_sub(self, cognito_sub: str) -> User | None:
        stmt = select(User).where(User.cognito_sub == cognito_sub)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def list_roles(self, user_id: str) -> list[str]:
        stmt = select(UserRole.role).where(UserRole.user_id == user_id)
        return list((await self._s.execute(stmt)).scalars().all())

    async def roles_for_sub(self, cognito_sub: str) -> list[str]:
        """Roles granted to the user with this Cognito sub (empty if unknown)."""
        stmt = (
            select(UserRole.role)
            .join(User, User.id == UserRole.user_id)
            .where(User.cognito_sub == cognito_sub)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def create_invitation(
        self, email: str, roles: list[str], *, invited_by: str | None = None
    ) -> PendingInvitation:
        """Upsert a pending invitation. Re-inviting refreshes roles and reopens it."""
        stmt = select(PendingInvitation).where(PendingInvitation.email == email)
        inv = (await self._s.execute(stmt)).scalar_one_or_none()
        if inv is None:
            inv = PendingInvitation(
                email=email, roles_json=json.dumps(roles), invited_by=invited_by
            )
            self._s.add(inv)
        else:
            inv.roles_json = json.dumps(roles)
            inv.invited_by = invited_by
            inv.consumed_at = None  # re-inviting reopens a consumed invitation
        await self._s.flush()
        return inv

    async def resolve_login(self, *, cognito_sub: str, email: str) -> User:
        """Bind a validated Cognito identity to a local `User`; grant invited roles.

        Order of resolution:
          1. Returning user (sub already bound) → returned as-is (email refreshed).
          2. First login with a pending invitation → create the user, grant the
             invitation's roles, mark it consumed.
          3. First login matching a pre-existing local user by email (no sub
             yet) → bind the sub to that user.
          4. Otherwise → raise `NoInvitationError` (admin-invitation-only).
        """
        user = await self.get_by_sub(cognito_sub)
        if user is not None:
            if email and user.email != email:
                user.email = email
            return user

        invitation = (
            await self._s.execute(
                select(PendingInvitation).where(
                    PendingInvitation.email == email,
                    PendingInvitation.consumed_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if invitation is not None:
            user = User(cognito_sub=cognito_sub, email=email)
            self._s.add(user)
            await self._s.flush()  # assign user.id before granting roles
            for role in _parse_roles(invitation.roles_json):
                self._s.add(UserRole(user_id=user.id, role=role))
            invitation.consumed_at = datetime.now(UTC)
            await self._s.flush()
            logger.info(
                "Provisioned user %s (%s) with roles %s", user.id, email, invitation.roles_json
            )
            return user

        existing = await self.get_by_email(email)
        if existing is not None and existing.cognito_sub is None:
            existing.cognito_sub = cognito_sub
            await self._s.flush()
            logger.info("Bound pre-existing user %s to cognito sub", existing.id)
            return existing

        raise NoInvitationError(email)
