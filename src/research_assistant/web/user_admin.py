"""Sprint U1 — user administration module router.

Sits alongside `web/admin.py` (which keeps the source-config + legacy
invite + validation-pack endpoints). The new surface here is
regulatory-aware: scoped invitations, SoD enforcement, required-fields
guidance, delegation log + training records.

Sprint U4 extends this router with audit-log queries, PI countersigning,
training-expiring + delegation-queue endpoints, and report download
surfaces. Every state-changing endpoint also writes an audit row via
`services/user_admin_audit.record_event`.

All endpoints require `Permission.USER_MANAGE` (admin role today).
The matrices live in `services/user_admin.py`; this router is a thin
DTO + repo wrapper.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
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
from ..services.user_admin_audit import (
    ACTION_DELEGATION_SIGNED,
    ACTION_INVITE_CREATED,
    ACTION_INVITE_RESENT,
    ACTION_INVITE_REVOKED,
    ACTION_PROFILE_PATCHED,
    ACTION_ROLE_GRANT_OVERRIDDEN,
    ACTION_ROLE_GRANTED,
    ACTION_ROLE_REVOKED,
    ACTION_TRAINING_RECORDED,
    ACTION_USER_REACTIVATED,
    ACTION_USER_SUSPENDED,
    KNOWN_ACTIONS,
    record_event,
)
from .auth import AdminUser


def _client_ip(request: Request) -> str | None:
    """Best-effort caller IP. FastAPI sets request.client to None in
    some test transports; fall back to None."""
    if request.client is None:
        return None
    return request.client.host


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
    async def invite(body: InviteIn, request: Request, admin: AdminUser) -> InviteOut:
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
            await record_event(
                session,
                actor_user_id=inviter.id if inviter else None,
                action=ACTION_INVITE_CREATED,
                payload={
                    "email": email,
                    "assignments": assignments_payload,
                    "cognito_status": cognito_status,
                },
                ip_address=_client_ip(request),
            )

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
    async def resend_invitation(
        invitation_id: str, request: Request, admin: AdminUser
    ) -> dict[str, str]:
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
        async with get_db_session() as session:
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            await record_event(
                session,
                actor_user_id=actor.id if actor else None,
                action=ACTION_INVITE_RESENT,
                payload={"email": email, "cognito_status": status},
                ip_address=_client_ip(request),
            )
        logger.info("Admin %s resent invite for %s (cognito=%s)", admin.sub, email, status)
        return {"email": email, "cognito_status": status}

    @router.post("/invitations/by-email/resend")
    async def resend_invitation_by_email(body: dict[str, str], admin: AdminUser) -> dict[str, str]:
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
    async def revoke_invitation(invitation_id: str, request: Request, admin: AdminUser) -> None:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            ok = await admin_repo.revoke_invitation(invitation_id)
            if not ok:
                raise HTTPException(404, "Invitation not found or already consumed.")
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            await record_event(
                session,
                actor_user_id=actor.id if actor else None,
                action=ACTION_INVITE_REVOKED,
                payload={"invitation_id": invitation_id},
                ip_address=_client_ip(request),
            )
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
    async def patch_profile(
        user_id: str, body: ProfileIn, request: Request, admin: AdminUser
    ) -> ProfileOut:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            try:
                changed = body.model_dump(exclude_unset=True)
                profile = await admin_repo.upsert_profile(user_id, **changed)
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
            await record_event(
                session,
                actor_user_id=actor.id if actor else None,
                action=ACTION_PROFILE_PATCHED,
                target_user_id=user_id,
                payload={"fields_changed": sorted(changed.keys())},
                ip_address=_client_ip(request),
            )
            return ProfileOut.model_validate(profile)

    @router.post("/users/{user_id}/suspend", response_model=ProfileOut)
    async def suspend(
        user_id: str, body: SuspendIn, request: Request, admin: AdminUser
    ) -> ProfileOut:
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
            await record_event(
                session,
                actor_user_id=actor.id if actor else None,
                action=ACTION_USER_SUSPENDED,
                target_user_id=user_id,
                payload={"reason": body.reason},
                ip_address=_client_ip(request),
            )
            return ProfileOut.model_validate(profile)

    @router.post("/users/{user_id}/reactivate", response_model=ProfileOut)
    async def reactivate(user_id: str, request: Request, admin: AdminUser) -> ProfileOut:
        async with get_db_session() as session:
            admin_repo = UserAdminRepository(session)
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            try:
                profile = await admin_repo.reactivate(user_id)
            except UserAdminError as e:
                raise HTTPException(422, str(e)) from e
            await record_event(
                session,
                actor_user_id=actor.id if actor else None,
                action=ACTION_USER_REACTIVATED,
                target_user_id=user_id,
                ip_address=_client_ip(request),
            )
            return ProfileOut.model_validate(profile)

    # ── Role assignments ───────────────────────────────────────────

    @router.post("/users/{user_id}/role-assignments", response_model=AssignmentOut, status_code=201)
    async def grant(
        user_id: str, body: AssignmentIn, request: Request, admin: AdminUser
    ) -> AssignmentOut:
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
            # When an override_rationale was supplied AND conflicts were
            # detected, log the override separately so auditor queries
            # can filter for `role.grant_overridden` regardless of
            # whether the underlying grant succeeded.
            await record_event(
                session,
                actor_user_id=actor.id if actor else None,
                action=ACTION_ROLE_GRANTED,
                target_user_id=user_id,
                scope_type=body.scope_type,
                scope_id=body.scope_id,
                payload={
                    "role": body.role,
                    "override_rationale": body.override_rationale,
                    "conflicts_detected": [c.proposed_role.value for c in conflicts],
                },
                ip_address=_client_ip(request),
            )
            if conflicts and body.override_rationale:
                await record_event(
                    session,
                    actor_user_id=actor.id if actor else None,
                    action=ACTION_ROLE_GRANT_OVERRIDDEN,
                    target_user_id=user_id,
                    scope_type=body.scope_type,
                    scope_id=body.scope_id,
                    payload={
                        "role": body.role,
                        "override_rationale": body.override_rationale,
                        "conflicts": [
                            {
                                "existing_role": c.existing_role.value,
                                "proposed_role": c.proposed_role.value,
                                "rationale": c.rationale,
                            }
                            for c in conflicts
                        ],
                    },
                    ip_address=_client_ip(request),
                )
        return AssignmentOut.model_validate(assignment)

    @router.delete("/users/{user_id}/role-assignments/{assignment_id}", status_code=204)
    async def revoke(user_id: str, assignment_id: str, request: Request, admin: AdminUser) -> None:
        async with get_db_session() as session:
            user_repo = UserRepository(session)
            from ..persistence.models import RoleAssignment

            existing = await session.get(RoleAssignment, assignment_id)
            ok = await user_repo.revoke_role(assignment_id)
            if not ok:
                raise HTTPException(404, "Assignment not found.")
            actor = await user_repo.get_by_sub(admin.sub)
            await record_event(
                session,
                actor_user_id=actor.id if actor else None,
                action=ACTION_ROLE_REVOKED,
                target_user_id=user_id,
                scope_type=existing.scope_type if existing else None,
                scope_id=existing.scope_id if existing else None,
                payload={"role": existing.role if existing else None},
                ip_address=_client_ip(request),
            )

    # ── Training records ───────────────────────────────────────────

    @router.post("/users/{user_id}/training-records", response_model=TrainingOut, status_code=201)
    async def add_training(
        user_id: str, body: TrainingIn, request: Request, admin: AdminUser
    ) -> TrainingOut:
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
            await record_event(
                session,
                actor_user_id=actor.id if actor else None,
                action=ACTION_TRAINING_RECORDED,
                target_user_id=user_id,
                payload={
                    "training_type": body.training_type,
                    "topic": body.topic,
                    "verified": body.verify,
                },
                ip_address=_client_ip(request),
            )
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

    # ── Sprint U4 — PI countersigning ──────────────────────────────

    class _CountersignIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        password: str = Field(
            ...,
            description=(
                "Caller's password — re-verified against Cognito per "
                "21 CFR Part 11 §11.200 (two signature components: the "
                "active session + an explicit credential challenge)."
            ),
        )

    @router.post("/delegation-entries/{entry_id}/sign", response_model=DelegationOut)
    async def sign_delegation_entry(
        entry_id: str,
        body: _CountersignIn,
        request: Request,
        admin: AdminUser,
    ) -> DelegationOut:
        """PI countersigns a delegation entry their team member captured
        in onboarding. Reauths password per Part 11 §11.200, then sets
        signed_by_pi_user_id + signed_at. Caller must hold the
        principal_investigator role at trial or study scope matching the
        entry's trial.
        """
        from sqlalchemy import select

        from ..auth.cognito_admin import verify_user_password_async
        from ..persistence.models import DelegationLogEntry, EcrfStudy

        settings = get_settings()
        async with get_db_session() as session:
            entry = await session.get(DelegationLogEntry, entry_id)
            if entry is None:
                raise HTTPException(404, "Delegation entry not found.")
            if entry.signed_at is not None:
                raise HTTPException(422, "Entry already signed.")

            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            if actor is None:
                raise HTTPException(404, "Caller has no local user record.")

            # Check PI authority on this trial. Acceptable scopes:
            #   - global PI grant (super-admin posture)
            #   - trial:<entry.trial_id> PI grant
            #   - study:<id> where EcrfStudy.trial_id == entry.trial_id
            assignments = await user_repo.assignments_for_user(actor.id)
            study_ids = list(
                (
                    await session.scalars(
                        select(EcrfStudy.id).where(EcrfStudy.trial_id == entry.trial_id)
                    )
                ).all()
            )
            is_pi = any(
                a.role == "principal_investigator"
                and (
                    a.scope_type == "global"
                    or (a.scope_type == "trial" and a.scope_id == entry.trial_id)
                    or (a.scope_type == "study" and a.scope_id in study_ids)
                )
                for a in assignments
            )
            if not is_pi:
                raise HTTPException(
                    403,
                    "Only the Principal Investigator on this trial can countersign.",
                )

            # Part 11 §11.200 password reauth — only when Cognito is wired.
            if settings.auth_enabled:
                ok = await verify_user_password_async(
                    admin.sub,
                    body.password,
                    region=settings.cognito_region,
                    user_pool_id=settings.cognito_user_pool_id,
                    client_id=settings.cognito_client_id,
                    client_secret=settings.cognito_client_secret or "",
                )
                if not ok:
                    raise HTTPException(401, "Password re-authentication failed.")

            from datetime import UTC as _UTC
            from datetime import datetime as _datetime

            entry.signed_by_pi_user_id = actor.id
            entry.signed_at = _datetime.now(_UTC)
            await session.flush()

            await record_event(
                session,
                actor_user_id=actor.id,
                action=ACTION_DELEGATION_SIGNED,
                target_user_id=entry.user_id,
                scope_type="trial",
                scope_id=entry.trial_id,
                payload={"entry_id": entry_id, "study_role": entry.study_role},
                ip_address=_client_ip(request),
            )
            return DelegationOut.model_validate(entry)

    # ── Sprint U4 — Delegation queue + training-expiring ───────────

    @router.get("/delegation-queue", response_model=list[DelegationOut])
    async def delegation_queue(admin: AdminUser) -> list[DelegationOut]:
        """Unsigned delegation entries on any trial the caller is PI on.
        Used by the U4 admin UI to surface a PI's pending sign queue.
        """
        from sqlalchemy import select

        from ..persistence.models import DelegationLogEntry, EcrfStudy

        async with get_db_session() as session:
            user_repo = UserRepository(session)
            actor = await user_repo.get_by_sub(admin.sub)
            if actor is None:
                return []
            assignments = await user_repo.assignments_for_user(actor.id)
            pi_trial_ids: set[str] = set()
            for a in assignments:
                if a.role != "principal_investigator":
                    continue
                if a.scope_type == "trial" and a.scope_id:
                    pi_trial_ids.add(a.scope_id)
                elif a.scope_type == "study" and a.scope_id:
                    study = await session.get(EcrfStudy, a.scope_id)
                    if study and study.trial_id:
                        pi_trial_ids.add(study.trial_id)
                # global PI sees everything — fall through to a
                # broader query.
            if any(
                a.role == "principal_investigator" and a.scope_type == "global" for a in assignments
            ):
                # Global PI: all unsigned entries.
                stmt = (
                    select(DelegationLogEntry)
                    .where(DelegationLogEntry.signed_at.is_(None))
                    .order_by(DelegationLogEntry.start_date)
                )
            elif pi_trial_ids:
                stmt = (
                    select(DelegationLogEntry)
                    .where(
                        DelegationLogEntry.trial_id.in_(pi_trial_ids),
                        DelegationLogEntry.signed_at.is_(None),
                    )
                    .order_by(DelegationLogEntry.start_date)
                )
            else:
                return []
            entries = list((await session.scalars(stmt)).all())
        return [DelegationOut.model_validate(e) for e in entries]

    @router.get("/training-expiring", response_model=list[TrainingOut])
    async def training_expiring(
        _: AdminUser,
        within_days: int = Query(default=30, ge=1, le=365),
    ) -> list[TrainingOut]:
        """Training records whose expires_date is within `within_days` of
        now, or already expired. Drives the U4 admin "expiry alerts"
        panel + supports an internal cron job (future)."""
        from datetime import UTC as _UTC
        from datetime import datetime as _datetime
        from datetime import timedelta as _td

        from sqlalchemy import select

        from ..persistence.models import TrainingRecord

        cutoff = _datetime.now(_UTC) + _td(days=within_days)
        async with get_db_session() as session:
            stmt = (
                select(TrainingRecord)
                .where(
                    TrainingRecord.expires_date.isnot(None),
                    TrainingRecord.expires_date <= cutoff,
                )
                .order_by(TrainingRecord.expires_date)
            )
            records = list((await session.scalars(stmt)).all())
        return [TrainingOut.model_validate(r) for r in records]

    # ── Sprint U4 — Audit query endpoint ───────────────────────────

    class AuditEntryOut(BaseModel):
        model_config = ConfigDict(from_attributes=True)
        id: str
        actor_user_id: str | None
        action: str
        target_user_id: str | None
        scope_type: str | None
        scope_id: str | None
        payload_json: str
        ip_address: str | None
        created_at: datetime

    @router.get("/audit", response_model=list[AuditEntryOut])
    async def query_audit(
        _: AdminUser,
        actor_user_id: str | None = Query(default=None),
        target_user_id: str | None = Query(default=None),
        action: str | None = Query(default=None),
        from_ts: datetime | None = Query(default=None, alias="from"),
        to_ts: datetime | None = Query(default=None, alias="to"),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[AuditEntryOut]:
        """Filtered audit-log query — drives the U4 admin Audit tab.
        Append-only data; this endpoint never mutates."""
        from sqlalchemy import select

        from ..persistence.models import UserAdminAuditEntry

        if action is not None and action not in KNOWN_ACTIONS:
            raise HTTPException(422, f"Unknown action {action!r}.")
        async with get_db_session() as session:
            stmt = select(UserAdminAuditEntry).order_by(UserAdminAuditEntry.created_at.desc())
            if actor_user_id is not None:
                stmt = stmt.where(UserAdminAuditEntry.actor_user_id == actor_user_id)
            if target_user_id is not None:
                stmt = stmt.where(UserAdminAuditEntry.target_user_id == target_user_id)
            if action is not None:
                stmt = stmt.where(UserAdminAuditEntry.action == action)
            if from_ts is not None:
                stmt = stmt.where(UserAdminAuditEntry.created_at >= from_ts)
            if to_ts is not None:
                stmt = stmt.where(UserAdminAuditEntry.created_at <= to_ts)
            stmt = stmt.limit(limit)
            rows = list((await session.scalars(stmt)).all())
        return [AuditEntryOut.model_validate(r) for r in rows]

    @router.get("/audit/actions", response_model=list[str])
    async def list_audit_actions(_: AdminUser) -> list[str]:
        """Canonical action names — drives the U4 admin filter dropdown."""
        return sorted(KNOWN_ACTIONS)

    # ── Sprint U4 — PDF reports ────────────────────────────────────

    @router.get("/reports/delegation-log/{trial_id}.pdf")
    async def report_delegation_log(trial_id: str, _: AdminUser) -> Response:
        from sqlalchemy import select

        from ..persistence.models import ClinicalTrial, User, UserProfile
        from ..reports.user_admin_reports import (
            assemble_delegation_log_data,
            build_delegation_log_pdf,
        )

        async with get_db_session() as session:
            trial = await session.get(ClinicalTrial, trial_id)
            if trial is None:
                raise HTTPException(404, "Trial not found.")
            admin_repo = UserAdminRepository(session)
            entries = await admin_repo.list_delegation_entries_for_trial(trial_id)
            user_ids = {e.user_id for e in entries} | {
                e.signed_by_pi_user_id for e in entries if e.signed_by_pi_user_id
            }
            users_by_id: dict[str, Any] = {}
            profiles_by_id: dict[str, Any] = {}
            if user_ids:
                users = await session.scalars(select(User).where(User.id.in_(user_ids)))
                users_by_id = {u.id: u for u in users}
                profiles = await session.scalars(
                    select(UserProfile).where(UserProfile.user_id.in_(user_ids))
                )
                profiles_by_id = {p.user_id: p for p in profiles}
            data = assemble_delegation_log_data(
                trial_id=trial.id,
                trial_title=trial.title,
                sponsor=trial.sponsor,
                indication=trial.indication,
                entries=entries,
                users_by_id=users_by_id,
                profiles_by_id=profiles_by_id,
            )
        pdf = build_delegation_log_pdf(data)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="delegation-log-{trial.id[:8]}.pdf"'
                ),
            },
        )

    @router.get("/reports/training-matrix/{site_id}.pdf")
    async def report_training_matrix(site_id: str, _: AdminUser) -> Response:
        from datetime import UTC as _UTC
        from datetime import datetime as _datetime
        from datetime import timedelta as _td

        from sqlalchemy import select

        from ..persistence.models import (
            AccountSite,
            RoleAssignment,
            TrainingRecord,
            User,
            UserProfile,
        )
        from ..reports.user_admin_reports import (
            TrainingMatrixData,
            TrainingRow,
            build_training_matrix_pdf,
        )

        async with get_db_session() as session:
            site = await session.get(AccountSite, site_id)
            if site is None:
                raise HTTPException(404, "Site not found.")
            # Members: users with a RoleAssignment scoped to this site.
            assignments = await session.scalars(
                select(RoleAssignment).where(
                    RoleAssignment.scope_type == "site",
                    RoleAssignment.scope_id == site_id,
                )
            )
            user_ids = list({a.user_id for a in assignments.all()})
            users_by_id: dict[str, Any] = {}
            profiles_by_id: dict[str, Any] = {}
            records: list[Any] = []
            if user_ids:
                users = await session.scalars(select(User).where(User.id.in_(user_ids)))
                users_by_id = {u.id: u for u in users}
                profiles = await session.scalars(
                    select(UserProfile).where(UserProfile.user_id.in_(user_ids))
                )
                profiles_by_id = {p.user_id: p for p in profiles}
                trs = await session.scalars(
                    select(TrainingRecord).where(TrainingRecord.user_id.in_(user_ids))
                )
                records = list(trs)
            now = _datetime.now(_UTC)
            soon = now + _td(days=30)
            rows: list[TrainingRow] = []
            expired_count = 0
            expiring_count = 0
            for r in records:
                user = users_by_id.get(r.user_id)
                profile = profiles_by_id.get(r.user_id)
                name = " ".join(
                    filter(
                        None,
                        [profile and profile.first_name, profile and profile.last_name],
                    )
                )
                expired = bool(r.expires_date and r.expires_date <= now)
                expiring_soon = bool(r.expires_date and not expired and r.expires_date <= soon)
                if expired:
                    expired_count += 1
                elif expiring_soon:
                    expiring_count += 1
                rows.append(
                    TrainingRow(
                        user_email=user.email if user else "—",
                        user_name=name,
                        topic=r.topic,
                        provider=r.provider,
                        completed_date=r.completed_date,
                        expires_date=r.expires_date,
                        expired=expired,
                        expiring_soon=expiring_soon,
                    )
                )
            data = TrainingMatrixData(
                site_id=site.id,
                site_name=site.name,
                site_code=site.code,
                members=len(user_ids),
                expired_count=expired_count,
                expiring_soon_count=expiring_count,
                rows=rows,
            )
        pdf = build_training_matrix_pdf(data)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="training-matrix-{site.id[:8]}.pdf"'
                ),
            },
        )

    @router.get("/reports/audit-log.pdf")
    async def report_audit_log(
        _: AdminUser,
        from_ts: datetime | None = Query(default=None, alias="from"),
        to_ts: datetime | None = Query(default=None, alias="to"),
        action: str | None = Query(default=None),
    ) -> Response:
        from sqlalchemy import select

        from ..persistence.models import User, UserAdminAuditEntry
        from ..reports.user_admin_reports import (
            AuditLogData,
            AuditLogRow,
            build_audit_log_pdf,
        )

        async with get_db_session() as session:
            stmt = select(UserAdminAuditEntry).order_by(UserAdminAuditEntry.created_at.desc())
            if from_ts is not None:
                stmt = stmt.where(UserAdminAuditEntry.created_at >= from_ts)
            if to_ts is not None:
                stmt = stmt.where(UserAdminAuditEntry.created_at <= to_ts)
            if action is not None:
                if action not in KNOWN_ACTIONS:
                    raise HTTPException(422, f"Unknown action {action!r}.")
                stmt = stmt.where(UserAdminAuditEntry.action == action)
            stmt = stmt.limit(5000)
            entries = list((await session.scalars(stmt)).all())
            user_ids = {e.actor_user_id for e in entries if e.actor_user_id} | {
                e.target_user_id for e in entries if e.target_user_id
            }
            user_emails: dict[str, str] = {}
            if user_ids:
                users = await session.scalars(select(User).where(User.id.in_(user_ids)))
                for u in users:
                    if u.email:
                        user_emails[u.id] = u.email
            rows = [
                AuditLogRow(
                    created_at=e.created_at,
                    actor_email=user_emails.get(e.actor_user_id) if e.actor_user_id else None,
                    action=e.action,
                    target_email=user_emails.get(e.target_user_id) if e.target_user_id else None,
                    scope_type=e.scope_type,
                    scope_id=e.scope_id,
                    payload_json=e.payload_json,
                    ip_address=e.ip_address,
                )
                for e in entries
            ]
        filter_bits = []
        if action:
            filter_bits.append(f"action={action}")
        filter_summary = " · ".join(filter_bits) if filter_bits else "(no filters)"
        data = AuditLogData(
            title="User-admin audit log",
            from_ts=from_ts,
            to_ts=to_ts,
            filter_summary=filter_summary,
            rows=rows,
        )
        pdf = build_audit_log_pdf(data)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="user-admin-audit.pdf"'},
        )

    return router
