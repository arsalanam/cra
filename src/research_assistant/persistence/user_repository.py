"""User identity + role provisioning (Phase C, extended for RBAC-1).

The login matcher (`resolve_login`) binds a validated Cognito identity to
a local `User` row. The app is admin-invitation-only: a first-time login
only succeeds if an admin previously recorded a `PendingInvitation` for
that email (or a pre-existing local user shares the email). Roles listed
on the invitation are granted on first login.

Cognito owns identity; these rows own authorization. With RBAC-1, grants
are stored in `role_assignments` (scoped) rather than the flat `user_roles`
table. Reads merge both during the migration window so pre-RBAC-1 rows
keep working; writes go to `role_assignments` only.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.rbac import (
    Permission,
    ScopeType,
    effective_permissions,
    normalize_legacy_role,
)
from .models import PendingInvitation, RoleAssignment, User, UserRole

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
        """List the user's role names (global scope only).

        Reads from `role_assignments` (RBAC-1) and unions in any legacy
        `user_roles` rows that haven't been backfilled yet, so callers see
        a consistent view during the migration window. Names are
        deduplicated and returned in deterministic insertion order.
        """
        new_stmt = select(RoleAssignment.role).where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.scope_type == ScopeType.GLOBAL,
        )
        legacy_stmt = select(UserRole.role).where(UserRole.user_id == user_id)
        rows: list[str] = list((await self._s.execute(new_stmt)).scalars().all())
        rows.extend((await self._s.execute(legacy_stmt)).scalars().all())
        seen: set[str] = set()
        out: list[str] = []
        for r in rows:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out

    async def roles_for_sub(self, cognito_sub: str) -> list[str]:
        """Global-scope role names for the user with this Cognito sub.

        Same merging logic as `list_roles` — used by the auth middleware to
        gate admin and data_entry endpoints. Empty when the sub is unknown.
        """
        user = await self.get_by_sub(cognito_sub)
        if user is None:
            return []
        return await self.list_roles(user.id)

    async def assignments_for_user(self, user_id: str) -> list[RoleAssignment]:
        """All scoped role grants for a user, newest first by `granted_at`."""
        stmt = (
            select(RoleAssignment)
            .where(RoleAssignment.user_id == user_id)
            .order_by(RoleAssignment.granted_at.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def effective_permissions_for_sub(
        self,
        cognito_sub: str,
        *,
        study_id: str | None = None,
        site_id: str | None = None,
    ) -> frozenset[Permission]:
        """Aggregate permissions the user holds against a `(study_id, site_id)`
        resource. Pass both None for a global check (skill gating, admin).

        Includes legacy `user_roles` rows projected to global scope so
        pre-RBAC-1 grants keep working during the migration window.
        """
        user = await self.get_by_sub(cognito_sub)
        if user is None:
            return frozenset()
        rows = await self.assignments_for_user(user.id)
        triples = [(r.role, r.scope_type, r.scope_id) for r in rows]
        legacy = (
            (await self._s.execute(select(UserRole.role).where(UserRole.user_id == user.id)))
            .scalars()
            .all()
        )
        for role_str in legacy:
            triples.append((role_str, ScopeType.GLOBAL.value, None))
        return effective_permissions(triples, study_id=study_id, site_id=site_id)

    async def grant_role(
        self,
        user_id: str,
        role: str,
        *,
        scope_type: str = "global",
        scope_id: str | None = None,
        granted_by: str | None = None,
    ) -> RoleAssignment:
        """Insert a new role grant. Idempotent on (user_id, role, scope).

        Validates `role` against the canonical `auth.rbac.Role` enum (with
        legacy aliases). Raises `ValueError` for an unknown role.
        """
        resolved = normalize_legacy_role(role)
        if resolved is None:
            raise ValueError(f"Unknown role {role!r}")
        if scope_type not in (s.value for s in ScopeType):
            raise ValueError(f"Unknown scope_type {scope_type!r}")
        if scope_type == ScopeType.GLOBAL.value and scope_id is not None:
            raise ValueError("global-scope assignments must have scope_id=None")
        if scope_type != ScopeType.GLOBAL.value and not scope_id:
            raise ValueError(f"{scope_type}-scope assignments require scope_id")

        existing = (
            await self._s.execute(
                select(RoleAssignment).where(
                    RoleAssignment.user_id == user_id,
                    RoleAssignment.role == resolved.value,
                    RoleAssignment.scope_type == scope_type,
                    RoleAssignment.scope_id.is_(scope_id)
                    if scope_id is None
                    else RoleAssignment.scope_id == scope_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        assignment = RoleAssignment(
            user_id=user_id,
            role=resolved.value,
            scope_type=scope_type,
            scope_id=scope_id,
            granted_by=granted_by,
        )
        self._s.add(assignment)
        await self._s.flush()
        return assignment

    async def revoke_role(self, assignment_id: str) -> bool:
        """Delete a single grant. Returns True if a row was removed."""
        assignment = await self._s.get(RoleAssignment, assignment_id)
        if assignment is None:
            return False
        await self._s.delete(assignment)
        await self._s.flush()
        return True

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
                try:
                    await self.grant_role(
                        user.id,
                        role,
                        granted_by=invitation.invited_by,
                    )
                except ValueError:
                    # Unknown role on the invitation — skip rather than fail
                    # the login. Logged so an operator can clean up the
                    # invitation row; the user still gets bound to their
                    # other valid roles. Better UX than refusing to log them
                    # in over a typo on the invite side.
                    logger.warning(
                        "Skipping unknown role %r on invitation for %s",
                        role,
                        email,
                    )
            invitation.consumed_at = datetime.now(UTC)
            await self._s.flush()
            logger.info(
                "Provisioned user %s (%s) with roles %s",
                user.id,
                email,
                invitation.roles_json,
            )
            return user

        existing = await self.get_by_email(email)
        if existing is not None and existing.cognito_sub is None:
            existing.cognito_sub = cognito_sub
            await self._s.flush()
            logger.info("Bound pre-existing user %s to cognito sub", existing.id)
            return existing

        raise NoInvitationError(email)
