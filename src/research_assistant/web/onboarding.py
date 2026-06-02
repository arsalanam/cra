"""Sprint U3 — user-side self-service onboarding endpoints.

Sibling to `web/user_admin.py` (which is the ADMIN-side surface). These
endpoints let an authenticated user inspect + complete their OWN
onboarding flow without going through admin: edit their own profile,
upload training-record URLs, capture delegation entries for trials they
hold a role on, and finally click Complete to set the
`onboarding_completed_at` gate.

Gating posture:
  - All endpoints require an authenticated session (CurrentUser).
  - Admin-only fields (suspended_*, onboarding_completed_at when reverting)
    are not writable here.
  - POST /me/complete validates that EVERY required field for EVERY held
    role is filled before setting the gate. 422 otherwise so the wizard
    can re-render with the missing chips.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..persistence.database import get_db_session
from ..persistence.user_admin_repository import (
    UserAdminError,
    UserAdminRepository,
)
from ..persistence.user_repository import UserRepository
from ..services.user_admin import (
    compute_missing_by_role,
    is_onboarded,
)
from .auth import CurrentUser

logger = logging.getLogger(__name__)


# ── DTOs ────────────────────────────────────────────────────────────────


class ProfileIn(BaseModel):
    """Fields the user can edit on themselves. Admin-only lifecycle
    fields (suspended_*, onboarding_completed_at) deliberately omitted."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    credentials: str | None = None
    medical_license_number: str | None = None
    medical_license_country: str | None = None
    gcp_training_completed_date: datetime | None = None
    gcp_training_provider: str | None = None
    gcp_certificate_url: str | None = None
    cv_url: str | None = None
    financial_disclosure_signed_date: datetime | None = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: str
    title: str | None
    first_name: str | None
    last_name: str | None
    credentials: str | None
    medical_license_number: str | None
    medical_license_country: str | None
    gcp_training_completed_date: datetime | None
    gcp_training_provider: str | None
    gcp_certificate_url: str | None
    cv_url: str | None
    financial_disclosure_signed_date: datetime | None
    onboarding_completed_at: datetime | None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    role: str
    scope_type: str
    scope_id: str | None
    granted_at: datetime


class MeOut(BaseModel):
    user_id: str
    email: str | None
    profile: ProfileOut | None
    assignments: list[AssignmentOut]
    missing_fields_by_role: dict[str, list[str]]
    onboarding_required: bool


class TrainingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    training_type: str  # ich_gcp | protocol_specific | platform | other
    topic: str
    provider: str | None = None
    completed_date: datetime
    expires_date: datetime | None = None
    certificate_url: str | None = None


class TrainingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    training_type: str
    topic: str
    provider: str | None
    completed_date: datetime
    expires_date: datetime | None
    certificate_url: str | None
    verified_by_user_id: str | None
    verified_at: datetime | None


class DelegationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trial_id: str
    study_role: str
    delegated_tasks: list[str]
    start_date: datetime
    end_date: datetime | None = None


class DelegationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    trial_id: str
    user_id: str
    study_role: str
    delegated_tasks_json: str
    start_date: datetime
    end_date: datetime | None
    signed_by_pi_user_id: str | None
    signed_at: datetime | None


# ── Helpers ─────────────────────────────────────────────────────────────


async def _resolve_caller_user_id(user_sub: str) -> str:
    """Resolve the session sub to a local User.id, raising 404 if no
    matching row exists (Cognito sub never logged in via resolve_login)."""
    async with get_db_session() as session:
        local = await UserRepository(session).get_by_sub(user_sub)
        if local is None:
            raise HTTPException(404, "No local user record for this session.")
        return local.id


# ── Router ──────────────────────────────────────────────────────────────


def create_onboarding_router() -> APIRouter:
    """Build the /api/onboarding/* router. Every endpoint requires an
    authenticated session — there's no admin gate because the user IS
    the one performing the action on themselves."""
    router = APIRouter(prefix="/onboarding", tags=["onboarding"])

    @router.get("/me", response_model=MeOut)
    async def get_me(user: CurrentUser) -> MeOut:
        uid = await _resolve_caller_user_id(user.sub)
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            profile, assignments = await admin_repo.onboarding_snapshot_for_user(uid)
            from ..persistence.models import User

            user_row = await session.get(User, uid)
        missing = compute_missing_by_role(
            assignment_roles=[a.role for a in assignments],
            profile=profile,
        )
        return MeOut(
            user_id=uid,
            email=user_row.email if user_row else None,
            profile=ProfileOut.model_validate(profile) if profile else None,
            assignments=[AssignmentOut.model_validate(a) for a in assignments],
            missing_fields_by_role=missing,
            onboarding_required=(not is_onboarded(profile) and len(assignments) > 0),
        )

    @router.patch("/me/profile", response_model=ProfileOut)
    async def patch_profile(body: ProfileIn, user: CurrentUser) -> ProfileOut:
        uid = await _resolve_caller_user_id(user.sub)
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            try:
                profile = await admin_repo.upsert_profile(
                    uid, **body.model_dump(exclude_unset=True)
                )
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
        logger.info("User %s self-edited profile", uid)
        return ProfileOut.model_validate(profile)

    @router.post("/me/training-records", response_model=TrainingOut, status_code=201)
    async def add_training(body: TrainingIn, user: CurrentUser) -> TrainingOut:
        uid = await _resolve_caller_user_id(user.sub)
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            try:
                record = await admin_repo.create_training_record(
                    user_id=uid,
                    training_type=body.training_type,
                    topic=body.topic,
                    provider=body.provider,
                    completed_date=body.completed_date,
                    expires_date=body.expires_date,
                    certificate_url=body.certificate_url,
                    # Self-uploaded training isn't admin-verified at write
                    # time; the U4 admin surface will let a verifier
                    # countersign later.
                    verified_by_user_id=None,
                )
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
        # If this is a GCP training upload, mirror the date + provider +
        # URL into the profile so the gate sees the field as filled
        # without the user needing to re-enter them in the Identity step.
        if body.training_type == "ich_gcp":
            async with get_db_session() as session:
                admin_repo = UserAdminRepository(session)
                await admin_repo.upsert_profile(
                    uid,
                    gcp_training_completed_date=body.completed_date,
                    gcp_training_provider=body.provider,
                    gcp_certificate_url=body.certificate_url,
                )
        logger.info("User %s uploaded training record %s", uid, record.id)
        return TrainingOut.model_validate(record)

    @router.post("/me/delegation-entries", response_model=DelegationOut, status_code=201)
    async def add_delegation(body: DelegationIn, user: CurrentUser) -> DelegationOut:
        uid = await _resolve_caller_user_id(user.sub)
        # Verify the caller actually holds a role on this trial (study or
        # site under the trial) — prevents random users from creating
        # delegation entries against trials they aren't part of.
        async with get_db_session() as session:
            from sqlalchemy import select

            from ..persistence.models import ClinicalTrial, EcrfStudy, RoleAssignment

            trial = await session.get(ClinicalTrial, body.trial_id)
            if trial is None:
                raise HTTPException(404, "Trial not found.")
            # Get the studies under this trial.
            study_ids = list(
                (
                    await session.scalars(
                        select(EcrfStudy.id).where(EcrfStudy.trial_id == body.trial_id)
                    )
                ).all()
            )
            assignments = (
                await session.scalars(select(RoleAssignment).where(RoleAssignment.user_id == uid))
            ).all()
            holds_role = any(
                a.scope_type == "global"
                or (a.scope_type == "study" and a.scope_id in study_ids)
                or a.scope_type == "trial"
                and a.scope_id == body.trial_id
                for a in assignments
            )
            if not holds_role:
                raise HTTPException(
                    403,
                    "You don't hold a role on this trial; can't create a delegation entry.",
                )
            admin_repo = UserAdminRepository(session)
            try:
                entry = await admin_repo.create_delegation_entry(
                    trial_id=body.trial_id,
                    user_id=uid,
                    study_role=body.study_role,
                    delegated_tasks=body.delegated_tasks,
                    start_date=body.start_date,
                    end_date=body.end_date,
                    # signed_by_pi remains null at create time; PI
                    # countersigns from the admin surface (U4).
                    signed_by_pi_user_id=None,
                )
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
        logger.info("User %s captured delegation entry %s", uid, entry.id)
        return DelegationOut.model_validate(entry)

    @router.post("/me/complete", response_model=ProfileOut)
    async def complete(request: Request, user: CurrentUser) -> ProfileOut:
        """Mark the user as onboarded. Validates that EVERY required field
        for EVERY held role is filled; 422 with the missing-fields map
        otherwise so the wizard can route the user back to the right
        step."""
        from ..services.user_admin_audit import (
            ACTION_ONBOARDING_COMPLETED,
            record_event,
        )

        uid = await _resolve_caller_user_id(user.sub)
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            profile, assignments = await admin_repo.onboarding_snapshot_for_user(uid)
            missing = compute_missing_by_role(
                assignment_roles=[a.role for a in assignments],
                profile=profile,
            )
            if missing:
                raise HTTPException(
                    422,
                    {
                        "message": (
                            "Cannot complete onboarding — required fields are still "
                            "missing for at least one of your roles."
                        ),
                        "missing_fields_by_role": missing,
                    },
                )
            updated = await admin_repo.mark_onboarded(uid)
            # The actor is the user themselves (self-service). Captured
            # in the audit log so an auditor sees every onboarding
            # completion event for a per-user trail.
            await record_event(
                session,
                actor_user_id=uid,
                action=ACTION_ONBOARDING_COMPLETED,
                target_user_id=uid,
                payload={"roles": sorted({a.role for a in assignments})},
                ip_address=(request.client.host if request.client else None),
            )
        logger.info("User %s completed onboarding", uid)
        return ProfileOut.model_validate(updated)

    return router
