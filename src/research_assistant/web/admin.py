"""Admin endpoints — paper-source config + user invitations.

Gated by the `AdminUser` dependency (Phase D): the caller must hold the
'admin' role. When auth is disabled (tests / early dev) the dependency
short-circuits open. Cookies are same-origin, so a logged-in admin's
browser reaches these endpoints without extra frontend wiring.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..auth.cognito_admin import CognitoAdminError, create_cognito_user
from ..auth.rbac import Role, ScopeType, normalize_legacy_role
from ..config import get_settings
from ..persistence.database import get_db_session
from ..persistence.models import RoleAssignment, SourceConfig, User
from ..persistence.user_repository import UserRepository
from .auth import AdminUser

logger = logging.getLogger(__name__)


class SourceConfigOut(BaseModel):
    """Admin-facing representation of a SourceConfig row."""

    id: str
    display_name: str
    enabled: bool
    api_key: str | None
    contact_email: str | None
    max_retries: int
    backoff_base_sec: float
    backoff_cap_sec: float
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InviteUserIn(BaseModel):
    """Admin request to invite a user by email."""

    email: str
    roles: list[str] = ["researcher"]

    model_config = ConfigDict(extra="forbid")


class InviteUserOut(BaseModel):
    """Result of an invitation: the recorded invite + Cognito provisioning status."""

    email: str
    roles: list[str]
    cognito_status: str  # e.g. "FORCE_CHANGE_PASSWORD" or "EXISTS"


class RoleAssignmentOut(BaseModel):
    """A single RoleAssignment row for the admin UI."""

    id: str
    role: str
    scope_type: str
    scope_id: str | None
    granted_by: str | None
    granted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserWithRolesOut(BaseModel):
    id: str
    email: str | None
    name: str | None
    cognito_sub: str | None
    created_at: datetime
    role_assignments: list[RoleAssignmentOut]

    model_config = ConfigDict(from_attributes=True)


class GrantRoleIn(BaseModel):
    """Admin grant payload — scope_id required iff scope_type != 'global'."""

    role: str
    scope_type: str = "global"
    scope_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class SourceConfigUpdate(BaseModel):
    """Partial-update payload — fields omitted from the JSON body are left alone."""

    display_name: str | None = None
    enabled: bool | None = None
    api_key: str | None = None
    contact_email: str | None = None
    max_retries: int | None = None
    backoff_base_sec: float | None = None
    backoff_cap_sec: float | None = None

    model_config = ConfigDict(extra="forbid")


def create_admin_router() -> APIRouter:
    """Build the `/admin/*` router. Every endpoint requires the 'admin' role."""
    router = APIRouter(prefix="/admin", tags=["admin"])

    @router.post("/users", response_model=InviteUserOut, status_code=201)
    async def invite_user(body: InviteUserIn, admin: AdminUser) -> InviteUserOut:
        """Invite a user: provision in Cognito + record a pending invitation.

        Roles are granted on the invitee's first login by the matcher in
        `user_repository.resolve_login`. Each role name is validated
        against `auth.rbac.Role` (with legacy aliases like 'data_entry'
        accepted) so a typo can't slip through and leave the invitee with
        no permissions.
        """
        settings = get_settings()
        email = body.email.strip().lower()
        domain = email.rsplit("@", 1)[-1]
        if "@" not in email or "." not in domain:
            raise HTTPException(422, "A valid email address is required.")
        roles = body.roles or ["researcher"]

        unknown = [r for r in roles if normalize_legacy_role(r) is None]
        if unknown:
            raise HTTPException(
                422,
                f"Unknown role(s): {', '.join(unknown)}. "
                f"See rbac-design.md §4.3 for the role catalogue.",
            )

        if not settings.auth_enabled:
            raise HTTPException(503, "Authentication is not configured.")
        try:
            # boto3 is sync/blocking — keep it off the event loop.
            cognito_status = await run_in_threadpool(
                create_cognito_user,
                email,
                region=settings.cognito_region,
                user_pool_id=settings.cognito_user_pool_id,
            )
        except CognitoAdminError as e:
            raise HTTPException(502, f"Cognito provisioning failed: {e}") from e

        async with get_db_session() as session:
            repo = UserRepository(session)
            inviter = await repo.get_by_sub(admin.sub)
            await repo.create_invitation(email, roles, invited_by=inviter.id if inviter else None)

        logger.info("Admin invited %s with roles %s (cognito=%s)", email, roles, cognito_status)
        return InviteUserOut(email=email, roles=roles, cognito_status=cognito_status)

    # ── User + role management (RBAC-2) ──────────────────────────────────

    @router.get("/users", response_model=list[UserWithRolesOut])
    async def list_users(admin: AdminUser) -> list[UserWithRolesOut]:
        """List every local user with their current RoleAssignment rows.

        The admin settings UI uses this to render the grant/revoke surface.
        Heavy join; small population — acceptable for an admin-only page.
        """
        async with get_db_session() as session:
            users = list(
                (await session.execute(select(User).order_by(User.email.is_(None), User.email)))
                .scalars()
                .all()
            )
            repo = UserRepository(session)
            out: list[UserWithRolesOut] = []
            for u in users:
                assignments = await repo.assignments_for_user(u.id)
                out.append(
                    UserWithRolesOut(
                        id=u.id,
                        email=u.email,
                        name=u.name,
                        cognito_sub=u.cognito_sub,
                        created_at=u.created_at,
                        role_assignments=[RoleAssignmentOut.model_validate(a) for a in assignments],
                    )
                )
            return out

    @router.post(
        "/users/{user_id}/roles",
        response_model=RoleAssignmentOut,
        status_code=201,
    )
    async def grant_role(user_id: str, body: GrantRoleIn, admin: AdminUser) -> RoleAssignmentOut:
        """Grant `(role, scope)` to a user. Idempotent on the same triple.

        Validates the role name against `auth.rbac.Role` (with legacy
        aliases). 422s on typos and on shape mismatches (scope_id required
        for study/site, forbidden for global).
        """
        if normalize_legacy_role(body.role) is None:
            raise HTTPException(
                422,
                f"Unknown role {body.role!r}. Valid roles: {sorted(r.value for r in Role)}.",
            )
        if body.scope_type not in (s.value for s in ScopeType):
            raise HTTPException(
                422,
                f"Unknown scope_type {body.scope_type!r}. "
                f"Use one of: {sorted(s.value for s in ScopeType)}.",
            )
        async with get_db_session() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise HTTPException(404, f"User {user_id!r} not found")
            repo = UserRepository(session)
            inviter = await repo.get_by_sub(admin.sub)
            try:
                assignment = await repo.grant_role(
                    user_id,
                    body.role,
                    scope_type=body.scope_type,
                    scope_id=body.scope_id,
                    granted_by=inviter.id if inviter else None,
                )
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            logger.info(
                "Admin %s granted role %s (scope=%s/%s) to user %s",
                admin.sub,
                assignment.role,
                assignment.scope_type,
                assignment.scope_id,
                user_id,
            )
            return RoleAssignmentOut.model_validate(assignment)

    @router.delete(
        "/users/{user_id}/roles/{assignment_id}",
        status_code=204,
    )
    async def revoke_role(user_id: str, assignment_id: str, admin: AdminUser) -> None:
        """Revoke a specific RoleAssignment row by id.

        404 if the assignment doesn't exist OR belongs to a different user
        (prevents a malformed admin-side URL from accidentally revoking the
        wrong row).
        """
        async with get_db_session() as session:
            assignment = await session.get(RoleAssignment, assignment_id)
            if assignment is None or assignment.user_id != user_id:
                raise HTTPException(404, "Role assignment not found for this user.")
            await UserRepository(session).revoke_role(assignment_id)
            logger.info(
                "Admin %s revoked role %s (scope=%s/%s) from user %s",
                admin.sub,
                assignment.role,
                assignment.scope_type,
                assignment.scope_id,
                user_id,
            )
            return None

    @router.get("/sources", response_model=list[SourceConfigOut])
    async def list_sources(admin: AdminUser) -> list[SourceConfigOut]:
        async with get_db_session() as session:
            stmt = select(SourceConfig).order_by(SourceConfig.id)
            rows = list((await session.execute(stmt)).scalars().all())
            return [SourceConfigOut.model_validate(r) for r in rows]

    @router.put("/sources/{source_id}", response_model=SourceConfigOut)
    async def update_source(
        source_id: str, body: SourceConfigUpdate, admin: AdminUser
    ) -> SourceConfigOut:
        async with get_db_session() as session:
            row = await session.get(SourceConfig, source_id)
            if row is None:
                raise HTTPException(404, f"Source {source_id!r} not found")
            updates = body.model_dump(exclude_unset=True)
            for key, value in updates.items():
                setattr(row, key, value)
            await session.flush()
            await session.refresh(row)
            logger.info(
                "Admin updated source %r: %s",
                source_id,
                ", ".join(updates.keys()) or "(no fields)",
            )
            return SourceConfigOut.model_validate(row)

    return router
