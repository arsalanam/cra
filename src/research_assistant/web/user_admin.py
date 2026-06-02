"""Sprint U1 — user administration module router.

Sits alongside `web/admin.py` (which keeps the source-config + legacy
invite + validation-pack endpoints). The new surface here is
regulatory-aware: scoped invitations, SoD enforcement, required-fields
guidance, delegation log + training records.

All endpoints require `Permission.USER_MANAGE` (admin role today).
The matrices live in `services/user_admin.py`; this router is a thin
DTO + repo wrapper.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from ..auth.cognito_admin import (
    CognitoAdminError,
    create_cognito_user,
    resend_cognito_invitation,
)
from ..auth.rbac import ScopeType, normalize_legacy_role
from ..config import get_settings
from ..persistence.database import get_db_session
from ..persistence.user_admin_repository import (
    UserAdminError,
    UserAdminRepository,
)
from ..persistence.user_repository import UserRepository
from ..services.user_admin import (
    ROLE_CATALOGUE,
    SAME_STUDY_CONFLICTS,
    GrantConflict,
    detect_grant_conflicts,
    missing_required_fields,
)
from .auth import AdminUser

logger = logging.getLogger(__name__)


# ── DTOs ─────────────────────────────────────────────────────────────────


class AssignmentIn(BaseModel):
    """One row of a scoped grant. Used by InviteIn + GrantIn."""

    model_config = ConfigDict(extra="forbid")
    role: str
    scope_type: str = "global"
    scope_id: str | None = None
    override_rationale: str | None = Field(
        default=None,
        description=(
            "If a separation-of-duties conflict is detected, the admin "
            "must pass a non-empty rationale to override. Stored on the "
            "RoleAssignment for auditor inspection."
        ),
    )


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    scope_type: str
    scope_id: str | None
    granted_by: str | None
    granted_at: datetime


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    assignments: list[AssignmentIn]
    # Optional pre-fill of the profile so the onboarding screen shows
    # the admin's intent. All fields nullable.
    title: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class InviteOut(BaseModel):
    email: str
    cognito_status: str
    assignments: list[dict[str, Any]]


class ProfileIn(BaseModel):
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
    suspended_at: datetime | None
    suspended_by_user_id: str | None
    suspended_reason: str | None


class SuspendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str | None
    name: str | None
    cognito_sub: str | None
    created_at: datetime


class UserDetailView(BaseModel):
    user: UserView
    profile: ProfileOut | None
    assignments: list[AssignmentOut]
    missing_required_fields_by_role: dict[str, list[str]]


class DelegationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trial_id: str
    study_role: str
    delegated_tasks: list[str]
    start_date: datetime
    end_date: datetime | None = None
    sign_as_pi: bool = False


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


class TrainingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    training_type: str
    topic: str
    provider: str | None = None
    completed_date: datetime
    expires_date: datetime | None = None
    certificate_url: str | None = None
    verify: bool = False


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


class CheckGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    role: str
    scope_type: str = "global"
    scope_id: str | None = None


class ConflictOut(BaseModel):
    existing_role: str
    proposed_role: str
    scope_type: str
    scope_id: str | None
    rationale: str


class CheckGrantOut(BaseModel):
    conflicts: list[ConflictOut]
    missing_required_fields: list[str]


class RoleCatalogueEntry(BaseModel):
    role: str
    label: str
    description: str
    regulatory_basis: str
    typical_scope_types: list[str]
    required_fields: list[str]


class SoDPairOut(BaseModel):
    roles: list[str]
    rationale: str


# ── Validators ───────────────────────────────────────────────────────────


def _validate_assignment(a: AssignmentIn) -> None:
    if normalize_legacy_role(a.role) is None:
        raise HTTPException(
            422, f"Unknown role {a.role!r}. See rbac-design.md §4.3 for the catalogue."
        )
    if a.scope_type not in (s.value for s in ScopeType):
        raise HTTPException(422, f"Unknown scope_type {a.scope_type!r}.")
    if a.scope_type == ScopeType.GLOBAL.value and a.scope_id is not None:
        raise HTTPException(422, "global-scope assignments must have scope_id=null.")
    if a.scope_type != ScopeType.GLOBAL.value and not a.scope_id:
        raise HTTPException(422, f"{a.scope_type}-scope assignments require scope_id.")


def _conflict_to_out(c: GrantConflict) -> ConflictOut:
    return ConflictOut(
        existing_role=c.existing_role.value,
        proposed_role=c.proposed_role.value,
        scope_type=c.scope_type,
        scope_id=c.scope_id,
        rationale=c.rationale,
    )


def _enforce_sod(
    conflicts: list[GrantConflict],
    override_rationale: str | None,
) -> None:
    if not conflicts:
        return
    if not override_rationale or not override_rationale.strip():
        # 422 with the full conflict list + the regulatory rationale so
        # the admin can either pick a different person or supply an
        # explicit override.
        raise HTTPException(
            422,
            {
                "message": "Separation-of-duties conflict — supply override_rationale to proceed.",
                "conflicts": [
                    {
                        "existing_role": c.existing_role.value,
                        "proposed_role": c.proposed_role.value,
                        "scope_type": c.scope_type,
                        "scope_id": c.scope_id,
                        "rationale": c.rationale,
                    }
                    for c in conflicts
                ],
            },
        )


# ── Router ───────────────────────────────────────────────────────────────


def create_user_admin_router() -> APIRouter:
    """Build the /api/user-admin/* router. Every endpoint requires the
    'admin' role via AdminUser dependency (USER_MANAGE permission)."""
    router = APIRouter(prefix="/user-admin", tags=["user-admin"])

    # ── Roles catalogue + SoD matrix (drives the UI) ────────────────

    @router.get("/roles-catalogue", response_model=list[RoleCatalogueEntry])
    async def get_roles_catalogue(_: AdminUser) -> list[RoleCatalogueEntry]:
        return [
            RoleCatalogueEntry(
                role=r.role.value,
                label=r.label,
                description=r.description,
                regulatory_basis=r.regulatory_basis,
                typical_scope_types=list(r.typical_scope_types),
                required_fields=[f.value for f in sorted(r.required_fields, key=lambda x: x.value)],
            )
            for r in ROLE_CATALOGUE
        ]

    @router.get("/separation-of-duties", response_model=list[SoDPairOut])
    async def get_sod_matrix(_: AdminUser) -> list[SoDPairOut]:
        out: list[SoDPairOut] = []
        for pair, rationale in SAME_STUDY_CONFLICTS.items():
            out.append(
                SoDPairOut(
                    roles=sorted(r.value for r in pair),
                    rationale=rationale,
                )
            )
        return out

    @router.post("/check-grant", response_model=CheckGrantOut)
    async def check_grant(body: CheckGrantIn, _: AdminUser) -> CheckGrantOut:
        """Pre-flight check the U2 UI calls before submitting a grant —
        returns conflicts + missing required fields so the admin can
        adjust before clicking Submit. Read-only."""
        if normalize_legacy_role(body.role) is None:
            raise HTTPException(422, f"Unknown role {body.role!r}.")
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            existing_rows = await admin_repo.list_assignments_for_user(body.user_id)
            profile = await admin_repo.get_profile(body.user_id)
        conflicts = detect_grant_conflicts(
            existing_assignments=[(r.role, r.scope_type, r.scope_id) for r in existing_rows],
            proposed_role=body.role,
            proposed_scope_type=body.scope_type,
            proposed_scope_id=body.scope_id,
        )
        missing = missing_required_fields(role=body.role, profile=profile)
        return CheckGrantOut(
            conflicts=[_conflict_to_out(c) for c in conflicts],
            missing_required_fields=[f.value for f in missing],
        )

    # ── Invitations ─────────────────────────────────────────────────

    @router.post("/invitations", response_model=InviteOut, status_code=201)
    async def invite(body: InviteIn, admin: AdminUser) -> InviteOut:
        settings = get_settings()
        email = body.email.strip().lower()
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if "@" not in email or "." not in domain:
            raise HTTPException(422, "A valid email address is required.")
        if not body.assignments:
            raise HTTPException(422, "At least one role assignment is required.")
        for a in body.assignments:
            _validate_assignment(a)
        # Cross-assignment SoD check WITHIN the invitation itself: if
        # the admin tries to invite someone as both DM and PI on the
        # same study, refuse unless ALL conflicting pairs have an
        # override_rationale.
        for i, a in enumerate(body.assignments):
            others = [
                (b.role, b.scope_type, b.scope_id) for j, b in enumerate(body.assignments) if j != i
            ]
            conflicts = detect_grant_conflicts(
                existing_assignments=others,
                proposed_role=a.role,
                proposed_scope_type=a.scope_type,
                proposed_scope_id=a.scope_id,
            )
            _enforce_sod(conflicts, a.override_rationale)

        if not settings.auth_enabled:
            raise HTTPException(503, "Authentication is not configured.")
        try:
            cognito_status = await run_in_threadpool(
                create_cognito_user,
                email,
                region=settings.cognito_region,
                user_pool_id=settings.cognito_user_pool_id,
            )
        except CognitoAdminError as e:
            raise HTTPException(502, f"Cognito provisioning failed: {e}") from e

        assignments_payload = [a.model_dump(exclude_none=False) for a in body.assignments]
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            user_repo = UserRepository(session)
            inviter = await user_repo.get_by_sub(admin.sub)
            try:
                await admin_repo.create_scoped_invitation(
                    email=email,
                    assignments=assignments_payload,
                    invited_by=inviter.id if inviter else None,
                )
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e

        logger.info(
            "Admin %s invited %s with %d scoped assignments (cognito=%s)",
            admin.sub,
            email,
            len(body.assignments),
            cognito_status,
        )
        return InviteOut(
            email=email,
            cognito_status=cognito_status,
            assignments=assignments_payload,
        )

    @router.post("/invitations/{invitation_id}/resend")
    async def resend_invitation(invitation_id: str, admin: AdminUser) -> dict[str, str]:
        from ..persistence.models import PendingInvitation

        settings = get_settings()
        if not settings.auth_enabled:
            raise HTTPException(503, "Authentication is not configured.")
        async with get_db_session() as session:
            inv = await session.get(PendingInvitation, invitation_id)
            if inv is None:
                raise HTTPException(404, "Invitation not found.")
            if inv.consumed_at is not None:
                raise HTTPException(422, "Invitation already consumed.")
            email = inv.email
        try:
            status = await run_in_threadpool(
                resend_cognito_invitation,
                email,
                region=settings.cognito_region,
                user_pool_id=settings.cognito_user_pool_id,
            )
        except CognitoAdminError as e:
            raise HTTPException(502, str(e)) from e
        logger.info("Admin %s resent invite for %s (cognito=%s)", admin.sub, email, status)
        return {"email": email, "cognito_status": status}

    @router.post("/invitations/by-email/resend")
    async def resend_invitation_by_email(
        body: dict[str, str], admin: AdminUser
    ) -> dict[str, str]:
        """Sprint U2 — convenience for the admin UI which doesn't carry
        the invitation_id in the user list. Looks up the pending
        invitation by email then delegates to the Cognito resend.
        """
        email = (body.get("email") or "").strip().lower()
        if not email or "@" not in email:
            raise HTTPException(422, "Valid email required.")
        settings = get_settings()
        if not settings.auth_enabled:
            raise HTTPException(503, "Authentication is not configured.")
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            inv = await admin_repo.get_invitation_by_email(email)
            if inv is None:
                raise HTTPException(404, "No invitation on file for that email.")
            if inv.consumed_at is not None:
                raise HTTPException(422, "Invitation already consumed.")
        try:
            status = await run_in_threadpool(
                resend_cognito_invitation,
                email,
                region=settings.cognito_region,
                user_pool_id=settings.cognito_user_pool_id,
            )
        except CognitoAdminError as e:
            raise HTTPException(502, str(e)) from e
        logger.info("Admin %s resent invite by email %s (cognito=%s)", admin.sub, email, status)
        return {"email": email, "cognito_status": status}

    @router.delete("/invitations/{invitation_id}", status_code=204)
    async def revoke_invitation(invitation_id: str, admin: AdminUser) -> None:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            ok = await admin_repo.revoke_invitation(invitation_id)
            if not ok:
                raise HTTPException(404, "Invitation not found or already consumed.")
        logger.info("Admin %s revoked invitation %s", admin.sub, invitation_id)

    # ── Users ───────────────────────────────────────────────────────

    @router.get("/users", response_model=list[UserView])
    async def list_users(
        _: AdminUser,
        scope_type: str | None = Query(default=None),
        scope_id: str | None = Query(default=None),
        role: str | None = Query(default=None),
    ) -> list[UserView]:
        if scope_type is not None and scope_type not in (s.value for s in ScopeType):
            raise HTTPException(422, f"Unknown scope_type {scope_type!r}.")
        if role is not None and normalize_legacy_role(role) is None:
            raise HTTPException(422, f"Unknown role {role!r}.")
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            users = await admin_repo.list_users_with_role_in_scope(
                scope_type=scope_type,
                scope_id=scope_id,
                role=role,
            )
        return [UserView.model_validate(u) for u in users]

    @router.get("/users/{user_id}", response_model=UserDetailView)
    async def get_user(user_id: str, _: AdminUser) -> UserDetailView:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            from ..persistence.models import User

            user = await session.get(User, user_id)
            if user is None:
                raise HTTPException(404, "User not found.")
            profile = await admin_repo.get_profile(user_id)
            assignments = await admin_repo.list_assignments_for_user(user_id)
        missing_by_role: dict[str, list[str]] = {}
        for a in assignments:
            fields = missing_required_fields(role=a.role, profile=profile)
            if fields:
                missing_by_role[a.role] = [f.value for f in fields]
        return UserDetailView(
            user=UserView.model_validate(user),
            profile=ProfileOut.model_validate(profile) if profile else None,
            assignments=[AssignmentOut.model_validate(a) for a in assignments],
            missing_required_fields_by_role=missing_by_role,
        )

    @router.patch("/users/{user_id}/profile", response_model=ProfileOut)
    async def patch_profile(user_id: str, body: ProfileIn, _: AdminUser) -> ProfileOut:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            try:
                profile = await admin_repo.upsert_profile(
                    user_id, **body.model_dump(exclude_unset=True)
                )
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
            return ProfileOut.model_validate(profile)

    @router.post("/users/{user_id}/suspend", response_model=ProfileOut)
    async def suspend(user_id: str, body: SuspendIn, admin: AdminUser) -> ProfileOut:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            try:
                profile = await admin_repo.suspend(
                    user_id,
                    reason=body.reason,
                    suspended_by_user_id=actor.id if actor else None,
                )
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
            return ProfileOut.model_validate(profile)

    @router.post("/users/{user_id}/reactivate", response_model=ProfileOut)
    async def reactivate(user_id: str, _: AdminUser) -> ProfileOut:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            try:
                profile = await admin_repo.reactivate(user_id)
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
            return ProfileOut.model_validate(profile)

    # ── Role assignments ───────────────────────────────────────────

    @router.post("/users/{user_id}/role-assignments", response_model=AssignmentOut, status_code=201)
    async def grant(user_id: str, body: AssignmentIn, admin: AdminUser) -> AssignmentOut:
        _validate_assignment(body)
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            user_repo = UserRepository(session)
            existing = await admin_repo.list_assignments_for_user(user_id)
        conflicts = detect_grant_conflicts(
            existing_assignments=[(r.role, r.scope_type, r.scope_id) for r in existing],
            proposed_role=body.role,
            proposed_scope_type=body.scope_type,
            proposed_scope_id=body.scope_id,
        )
        _enforce_sod(conflicts, body.override_rationale)

        async with get_db_session() as session:
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            try:
                assignment = await user_repo.grant_role(
                    user_id,
                    body.role,
                    scope_type=body.scope_type,
                    scope_id=body.scope_id,
                    granted_by=actor.id if actor else None,
                )
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
        return AssignmentOut.model_validate(assignment)

    @router.delete("/users/{user_id}/role-assignments/{assignment_id}", status_code=204)
    async def revoke(user_id: str, assignment_id: str, _: AdminUser) -> None:
        async with get_db_session() as session:
            user_repo = UserRepository(session)
            ok = await user_repo.revoke_role(assignment_id)
            if not ok:
                raise HTTPException(404, "Assignment not found.")

    # ── Training records ───────────────────────────────────────────

    @router.post("/users/{user_id}/training-records", response_model=TrainingOut, status_code=201)
    async def add_training(user_id: str, body: TrainingIn, admin: AdminUser) -> TrainingOut:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            try:
                record = await admin_repo.create_training_record(
                    user_id=user_id,
                    training_type=body.training_type,
                    topic=body.topic,
                    provider=body.provider,
                    completed_date=body.completed_date,
                    expires_date=body.expires_date,
                    certificate_url=body.certificate_url,
                    verified_by_user_id=(actor.id if actor and body.verify else None),
                )
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
        return TrainingOut.model_validate(record)

    @router.get("/users/{user_id}/training-records", response_model=list[TrainingOut])
    async def list_training(user_id: str, _: AdminUser) -> list[TrainingOut]:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            records = await admin_repo.list_training_records_for_user(user_id)
        return [TrainingOut.model_validate(r) for r in records]

    # ── Delegation log ─────────────────────────────────────────────

    @router.post(
        "/users/{user_id}/delegation-entries", response_model=DelegationOut, status_code=201
    )
    async def add_delegation(user_id: str, body: DelegationIn, admin: AdminUser) -> DelegationOut:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            try:
                entry = await admin_repo.create_delegation_entry(
                    trial_id=body.trial_id,
                    user_id=user_id,
                    study_role=body.study_role,
                    delegated_tasks=body.delegated_tasks,
                    start_date=body.start_date,
                    end_date=body.end_date,
                    signed_by_pi_user_id=(actor.id if actor and body.sign_as_pi else None),
                )
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
        return DelegationOut.model_validate(entry)

    @router.get(
        "/trials/{trial_id}/delegation-entries",
        response_model=list[DelegationOut],
    )
    async def list_delegation_for_trial(trial_id: str, _: AdminUser) -> list[DelegationOut]:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            entries = await admin_repo.list_delegation_entries_for_trial(trial_id)
        return [DelegationOut.model_validate(e) for e in entries]

    return router
