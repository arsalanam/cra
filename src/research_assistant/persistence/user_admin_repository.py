"""Sprint U1 — repository for the user-administration tables.

Separate from `user_repository.py` (which handles login + role-grant +
legacy invitation paths) so the new surface stays self-contained. Where
overlap exists (the Cognito-invite write itself), this module reuses
`UserRepository.create_invitation` underneath.

Tables owned here:
  - UserProfile
  - DelegationLogEntry
  - TrainingRecord

`PendingInvitation` reads + writes for the SCOPED path go through
`UserRepository.create_scoped_invitation` (added in U1) — exposed via
this repo's `create_scoped_invitation` shim for caller ergonomics.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    DelegationLogEntry,
    PendingInvitation,
    RoleAssignment,
    TrainingRecord,
    User,
    UserProfile,
)


class UserAdminError(Exception):
    """Raised for validation errors from the user-admin layer (suspended
    self-mutation, unknown user, etc.). Endpoints translate to 422."""


class UserAdminRepository:
    """CRUD on the user-administration surface."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── UserProfile ─────────────────────────────────────────────────

    async def get_profile(self, user_id: str) -> UserProfile | None:
        return await self._s.get(UserProfile, user_id)

    async def upsert_profile(
        self,
        user_id: str,
        **fields: Any,
    ) -> UserProfile:
        """Insert-or-update the profile row keyed on user_id. Only the
        keys supplied in `fields` are written; others are left untouched
        on an existing row. Datetime fields can be passed as ISO strings
        or datetime objects — coerced here for caller convenience."""
        user = await self._s.get(User, user_id)
        if user is None:
            raise UserAdminError(f"User {user_id!r} not found.")
        profile = await self._s.get(UserProfile, user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            self._s.add(profile)
        for key, value in fields.items():
            if not hasattr(profile, key):
                continue
            if value is not None and "date" in key and isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            setattr(profile, key, value)
        await self._s.flush()
        return profile

    async def suspend(
        self,
        user_id: str,
        *,
        reason: str,
        suspended_by_user_id: str | None,
    ) -> UserProfile:
        if not reason or not reason.strip():
            raise UserAdminError("Suspension requires a non-empty reason.")
        profile = await self.upsert_profile(user_id)
        profile.suspended_at = datetime.now(UTC)
        profile.suspended_by_user_id = suspended_by_user_id
        profile.suspended_reason = reason.strip()
        await self._s.flush()
        return profile

    async def reactivate(self, user_id: str) -> UserProfile:
        profile = await self._s.get(UserProfile, user_id)
        if profile is None or profile.suspended_at is None:
            raise UserAdminError("User is not suspended.")
        profile.suspended_at = None
        profile.suspended_by_user_id = None
        profile.suspended_reason = None
        await self._s.flush()
        return profile

    async def mark_onboarded(self, user_id: str) -> UserProfile:
        profile = await self.upsert_profile(user_id)
        profile.onboarding_completed_at = datetime.now(UTC)
        await self._s.flush()
        return profile

    # ── Scoped invitations (extends PendingInvitation) ──────────────

    async def create_scoped_invitation(
        self,
        *,
        email: str,
        assignments: list[dict[str, Any]],
        invited_by: str | None,
    ) -> PendingInvitation:
        """Sprint U1 — write a PendingInvitation row with
        `assignments_json` populated. `roles_json` is set to the flat
        projection of the assignments' role names so the legacy login
        path keeps working as a fallback.
        """
        if not assignments:
            raise UserAdminError("At least one role assignment is required.")
        flat_roles = [a["role"] for a in assignments]
        stmt = select(PendingInvitation).where(PendingInvitation.email == email)
        inv = (await self._s.execute(stmt)).scalar_one_or_none()
        if inv is None:
            inv = PendingInvitation(
                email=email,
                roles_json=json.dumps(flat_roles),
                assignments_json=json.dumps(assignments),
                invited_by=invited_by,
            )
            self._s.add(inv)
        else:
            inv.roles_json = json.dumps(flat_roles)
            inv.assignments_json = json.dumps(assignments)
            inv.invited_by = invited_by
            inv.consumed_at = None  # re-inviting reopens
        await self._s.flush()
        return inv

    async def get_invitation_by_email(self, email: str) -> PendingInvitation | None:
        stmt = select(PendingInvitation).where(PendingInvitation.email == email)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def revoke_invitation(self, invitation_id: str) -> bool:
        """Pre-consumption revoke. Sets consumed_at to now so the matcher
        won't grant the roles on a future login. Returns False if the
        invitation was already consumed (idempotent no-op) or missing."""
        inv = await self._s.get(PendingInvitation, invitation_id)
        if inv is None:
            return False
        if inv.consumed_at is not None:
            return False
        inv.consumed_at = datetime.now(UTC)
        await self._s.flush()
        return True

    # ── DelegationLogEntry ─────────────────────────────────────────

    async def create_delegation_entry(
        self,
        *,
        trial_id: str,
        user_id: str,
        study_role: str,
        delegated_tasks: list[str],
        start_date: datetime,
        end_date: datetime | None = None,
        signed_by_pi_user_id: str | None = None,
    ) -> DelegationLogEntry:
        if not study_role.strip():
            raise UserAdminError("study_role is required.")
        if not delegated_tasks:
            raise UserAdminError("At least one delegated task is required.")
        entry = DelegationLogEntry(
            trial_id=trial_id,
            user_id=user_id,
            study_role=study_role.strip(),
            delegated_tasks_json=json.dumps(delegated_tasks),
            start_date=start_date,
            end_date=end_date,
            signed_by_pi_user_id=signed_by_pi_user_id,
            signed_at=datetime.now(UTC) if signed_by_pi_user_id else None,
        )
        self._s.add(entry)
        await self._s.flush()
        return entry

    async def list_delegation_entries_for_trial(self, trial_id: str) -> list[DelegationLogEntry]:
        stmt = (
            select(DelegationLogEntry)
            .where(DelegationLogEntry.trial_id == trial_id)
            .order_by(DelegationLogEntry.start_date)
        )
        return list((await self._s.scalars(stmt)).all())

    async def list_delegation_entries_for_user(self, user_id: str) -> list[DelegationLogEntry]:
        stmt = (
            select(DelegationLogEntry)
            .where(DelegationLogEntry.user_id == user_id)
            .order_by(DelegationLogEntry.start_date)
        )
        return list((await self._s.scalars(stmt)).all())

    # ── TrainingRecord ─────────────────────────────────────────────

    async def create_training_record(
        self,
        *,
        user_id: str,
        training_type: str,
        topic: str,
        provider: str | None,
        completed_date: datetime,
        expires_date: datetime | None = None,
        certificate_url: str | None = None,
        verified_by_user_id: str | None = None,
    ) -> TrainingRecord:
        if training_type not in {"ich_gcp", "protocol_specific", "platform", "other"}:
            raise UserAdminError(
                f"Unknown training_type {training_type!r}. "
                "Choose ich_gcp | protocol_specific | platform | other."
            )
        if not topic.strip():
            raise UserAdminError("topic is required.")
        record = TrainingRecord(
            user_id=user_id,
            training_type=training_type,
            topic=topic.strip(),
            provider=provider.strip() if provider else None,
            completed_date=completed_date,
            expires_date=expires_date,
            certificate_url=certificate_url,
            verified_by_user_id=verified_by_user_id,
            verified_at=datetime.now(UTC) if verified_by_user_id else None,
        )
        self._s.add(record)
        await self._s.flush()
        return record

    async def list_training_records_for_user(self, user_id: str) -> list[TrainingRecord]:
        stmt = (
            select(TrainingRecord)
            .where(TrainingRecord.user_id == user_id)
            .order_by(TrainingRecord.completed_date.desc())
        )
        return list((await self._s.scalars(stmt)).all())

    # ── User listing for the admin UI ──────────────────────────────

    async def list_users_with_role_in_scope(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        role: str | None = None,
    ) -> list[User]:
        """Return users with at least one RoleAssignment matching the
        filter. Filter shape:
          - scope_type only → match any assignment with that scope
          - scope_type + scope_id → match that specific scope
          - role only → match across any scope
          - no filter → return every user with at least one assignment

        Distinct on user_id; ordered by created_at desc.
        """
        stmt = (
            select(User)
            .join(RoleAssignment, RoleAssignment.user_id == User.id)
            .distinct()
            .order_by(User.created_at.desc())
        )
        if role is not None:
            stmt = stmt.where(RoleAssignment.role == role)
        if scope_type is not None:
            stmt = stmt.where(RoleAssignment.scope_type == scope_type)
        if scope_id is not None:
            stmt = stmt.where(RoleAssignment.scope_id == scope_id)
        return list((await self._s.scalars(stmt)).all())

    async def list_assignments_for_user(self, user_id: str) -> list[RoleAssignment]:
        stmt = (
            select(RoleAssignment)
            .where(RoleAssignment.user_id == user_id)
            .order_by(RoleAssignment.granted_at)
        )
        return list((await self._s.scalars(stmt)).all())

    async def onboarding_snapshot_for_user(
        self, user_id: str
    ) -> tuple[UserProfile | None, list[RoleAssignment]]:
        """Sprint U3 — one-shot fetch of (profile, assignments) for the
        onboarding gate check. Used by /auth/me + the require_onboarded
        dependency to avoid two round-trips on every request."""
        profile = await self.get_profile(user_id)
        assignments = await self.list_assignments_for_user(user_id)
        return profile, assignments

    async def list_assignments_for_users(
        self, user_ids: Iterable[str]
    ) -> dict[str, list[RoleAssignment]]:
        """Batch fetch — avoid N+1 when rendering the admin list."""
        ids = list(user_ids)
        if not ids:
            return {}
        stmt = (
            select(RoleAssignment)
            .where(RoleAssignment.user_id.in_(ids))
            .order_by(RoleAssignment.granted_at)
        )
        rows = list((await self._s.scalars(stmt)).all())
        bucket: dict[str, list[RoleAssignment]] = {uid: [] for uid in ids}
        for r in rows:
            bucket.setdefault(r.user_id, []).append(r)
        return bucket
