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
from ..config import get_settings
from ..persistence.database import get_db_session
from ..persistence.models import SourceConfig
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
        `user_repository.resolve_login`.
        """
        settings = get_settings()
        email = body.email.strip().lower()
        domain = email.rsplit("@", 1)[-1]
        if "@" not in email or "." not in domain:
            raise HTTPException(422, "A valid email address is required.")
        roles = body.roles or ["researcher"]

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
            await repo.create_invitation(
                email, roles, invited_by=inviter.id if inviter else None
            )

        logger.info("Admin invited %s with roles %s (cognito=%s)", email, roles, cognito_status)
        return InviteUserOut(email=email, roles=roles, cognito_status=cognito_status)

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
