"""REST endpoints for the account layer (Sprint A1).

Endpoints (all under /api/):

  POST   /accounts                              create
  GET    /accounts                              list (caller's memberships)
  GET    /accounts/{account_id}                 detail + members + sites + trials counts
  PATCH  /accounts/{account_id}                 update (name / description / status)
  DELETE /accounts/{account_id}                 archive (soft)

  POST   /accounts/{account_id}/members         add (idempotent upsert)
  GET    /accounts/{account_id}/members         list
  DELETE /accounts/{account_id}/members/{uid}   remove (refuses to remove owner)

  POST   /accounts/{account_id}/sites           create AccountSite
  GET    /accounts/{account_id}/sites           list
  PATCH  /account-sites/{site_id}               update
  DELETE /account-sites/{site_id}               archive (status=inactive)

  POST   /accounts/{account_id}/trials          create ClinicalTrial
  GET    /accounts/{account_id}/trials          list (filterable by status)
  GET    /trials/{trial_id}                     detail (artefact links + sites + deployment count)
  PATCH  /trials/{trial_id}                     update (status / phase / artefact links)
  POST   /trials/{trial_id}/sites               assign AccountSite
  DELETE /trials/{trial_id}/sites/{site_id}     unassign

Permission gating:
  • Account CRUD + member-mgmt + site-create → require account.manage.
  • Account read → membership check at runtime (a member who isn't admin
    can still see the account they belong to).
  • Trial read / list → trial.read (caller must also be an account member).
  • Trial CRUD + site assignment → trial.manage.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..auth import SessionPayload
from ..auth.rbac import Permission
from ..persistence.database import get_db_session
from ..persistence.models import (
    DEFAULT_USER_ID,
    Account,
    EcrfStudy,
    User,
)
from ..persistence.repository import AccountError, AccountRepository
from ..persistence.user_repository import UserRepository
from .auth import CurrentUser
from .authz import require_permission_scoped
from .threads import resolve_local_user_id

logger = logging.getLogger(__name__)


# ── DTOs ─────────────────────────────────────────────────────────────────


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None
    status: str | None = None  # active | archived


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str | None = None
    user_id: str | None = None
    role: str = "member"  # owner | admin | member | observer


class MemberView(BaseModel):
    user_id: str
    role: str
    joined_at: datetime
    email: str | None = None
    name: str | None = None


class AccountSiteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=40)
    address: str = ""
    contact_email: str | None = None
    pi_name: str | None = None


class AccountSitePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    code: str | None = None
    address: str | None = None
    contact_email: str | None = None
    pi_name: str | None = None
    status: str | None = None


class AccountSiteView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    name: str
    code: str | None
    address: str
    contact_email: str | None
    pi_name: str | None
    status: str
    created_at: datetime


class AccountView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str
    status: str
    owner_user_id: str | None
    n_members: int
    n_sites: int
    n_trials: int
    n_active_trials: int
    n_locked_trials: int
    created_at: datetime
    updated_at: datetime


class AccountDetailView(AccountView):
    members: list[MemberView] = Field(default_factory=list)
    sites: list[AccountSiteView] = Field(default_factory=list)


class TrialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    sponsor: str = ""
    indication: str = ""
    phase: str | None = None
    protocol_id: str | None = None


class TrialPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    sponsor: str | None = None
    indication: str | None = None
    phase: str | None = None
    protocol_id: str | None = None
    status: str | None = None
    registration_thread_id: str | None = None
    irb_thread_id: str | None = None
    sap_thread_id: str | None = None
    csr_thread_id: str | None = None
    manuscript_thread_id: str | None = None


class TrialView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    title: str
    sponsor: str
    indication: str
    phase: str | None
    protocol_id: str | None
    status: str
    registration_thread_id: str | None
    irb_thread_id: str | None
    sap_thread_id: str | None
    csr_thread_id: str | None
    manuscript_thread_id: str | None
    created_at: datetime
    updated_at: datetime


class DeploymentRefView(BaseModel):
    """Sprint A3: cross-store deployment summary for the trial dashboard
    so the operator can resume Phase-04 work without re-picking from a
    bare list. Lock state pulled from clinical DB; auto-promotion to
    `locked` happens via the A2 trial-status hook."""

    model_config = ConfigDict(extra="forbid")
    deployment_id: str
    name: str
    is_locked: bool


class StudyRefView(BaseModel):
    """Sprint A3: per-study rollup under a Trial — deep-link targets for
    eCRF design (X1) + collector (X2-X12) + multisite (X11)."""

    model_config = ConfigDict(extra="forbid")
    study_id: str
    name: str
    status: str
    deployments: list[DeploymentRefView] = Field(default_factory=list)


class TrialDetailView(TrialView):
    sites: list[AccountSiteView] = Field(default_factory=list)
    n_deployments: int = 0
    studies: list[StudyRefView] = Field(default_factory=list)


class TrialSiteAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_site_id: str
    notes: str = ""


class TrialSiteView(BaseModel):
    id: str
    trial_id: str
    account_site_id: str
    status: str
    activated_at: datetime
    notes: str


class TransferOwnership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_owner_user_id: str | None = None
    new_owner_email: str | None = None


class ArtefactBindIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(registration|irb|sap|csr|manuscript)$")
    thread_id: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────


async def _build_account_view(session: object, account: Account) -> AccountView:
    repo = AccountRepository(session)  # type: ignore[arg-type]
    members = await repo.list_members(account.id)
    sites = await repo.list_sites(account.id)
    trials = await repo.list_trials_for_account(account.id)
    n_active = sum(1 for t in trials if t.status in ("design", "draft", "deployed"))
    n_locked = sum(1 for t in trials if t.status == "locked")
    return AccountView(
        id=account.id,
        name=account.name,
        description=account.description,
        status=account.status,
        owner_user_id=account.owner_user_id,
        n_members=len(members),
        n_sites=len(sites),
        n_trials=len(trials),
        n_active_trials=n_active,
        n_locked_trials=n_locked,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


async def _resolve_invitee_user_id(
    *,
    session: object,
    email: str | None,
    user_id: str | None,
) -> str:
    if not email and not user_id:
        raise HTTPException(422, "Provide either email or user_id.")
    if user_id is not None:
        user = await session.get(User, user_id)  # type: ignore[attr-defined]
        if user is None:
            raise HTTPException(404, f"User {user_id!r} not found.")
        return str(user.id)
    assert email is not None
    repo = UserRepository(session)  # type: ignore[arg-type]
    user = await repo.get_by_email(email.strip().lower())
    if user is None:
        raise HTTPException(
            404,
            f"No user with email {email!r}. Invite them to the platform first.",
        )
    return str(user.id)


async def _require_account_access(
    repo: AccountRepository,
    account_id: str,
    caller_user_id: str,
) -> Account:
    account = await repo.get_account(account_id)
    if account is None:
        raise HTTPException(404, "Account not found.")
    if account.owner_user_id == caller_user_id or caller_user_id == DEFAULT_USER_ID:
        return account
    member = await repo.is_member(account_id=account_id, user_id=caller_user_id)
    if member is None:
        raise HTTPException(404, "Account not found.")
    return account


async def _require_owner_or_admin(
    account: Account,
    member: object,  # AccountMember | None
    caller_user_id: str,
) -> None:
    if caller_user_id == DEFAULT_USER_ID:
        return
    if account.owner_user_id == caller_user_id:
        return
    if member is not None and getattr(member, "role", None) in ("owner", "admin"):
        return
    raise HTTPException(403, "Owner or admin role required for this action.")


# ── Router ───────────────────────────────────────────────────────────────


def create_accounts_router() -> APIRouter:
    router = APIRouter(tags=["accounts"])

    # ── Accounts ────────────────────────────────────────────────────

    @router.post("/accounts", response_model=AccountView, status_code=201)
    async def create_account(
        body: AccountCreate,
        user: SessionPayload = require_permission_scoped(Permission.ACCOUNT_MANAGE),
    ) -> AccountView:
        owner_id = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            try:
                account = await repo.create_account(
                    name=body.name,
                    description=body.description,
                    owner_user_id=owner_id,
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            return await _build_account_view(session, account)

    @router.get("/accounts", response_model=list[AccountView])
    async def list_accounts(user: CurrentUser) -> list[AccountView]:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            accounts = await repo.list_accounts_for_user(caller)
            return [await _build_account_view(session, a) for a in accounts]

    @router.get("/accounts/{account_id}", response_model=AccountDetailView)
    async def get_account(account_id: str, user: CurrentUser) -> AccountDetailView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            account = await _require_account_access(repo, account_id, caller)
            base = await _build_account_view(session, account)
            members = await repo.list_members(account_id)
            sites = await repo.list_sites(account_id)
            member_views: list[MemberView] = []
            for m in members:
                u = await session.get(User, m.user_id)
                member_views.append(
                    MemberView(
                        user_id=m.user_id,
                        role=m.role,
                        joined_at=m.joined_at,
                        email=u.email if u else None,
                        name=u.name if u else None,
                    )
                )
            return AccountDetailView(
                **base.model_dump(),
                members=member_views,
                sites=[AccountSiteView.model_validate(s) for s in sites],
            )

    @router.patch("/accounts/{account_id}", response_model=AccountView)
    async def update_account(
        account_id: str,
        body: AccountPatch,
        user: SessionPayload = require_permission_scoped(Permission.ACCOUNT_MANAGE),
    ) -> AccountView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            account = await _require_account_access(repo, account_id, caller)
            member = await repo.is_member(account_id=account_id, user_id=caller)
            await _require_owner_or_admin(account, member, caller)
            try:
                updated = await repo.update_account(
                    account_id,
                    **{
                        k: v
                        for k, v in body.model_dump(exclude_unset=True).items()
                        if v is not None
                    },
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            assert updated is not None
            return await _build_account_view(session, updated)

    @router.delete("/accounts/{account_id}", status_code=204)
    async def archive_account(
        account_id: str,
        user: SessionPayload = require_permission_scoped(Permission.ACCOUNT_MANAGE),
    ) -> None:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            account = await _require_account_access(repo, account_id, caller)
            member = await repo.is_member(account_id=account_id, user_id=caller)
            await _require_owner_or_admin(account, member, caller)
            await repo.archive_account(account_id)

    # ── Members ─────────────────────────────────────────────────────

    @router.post(
        "/accounts/{account_id}/members",
        response_model=MemberView,
        status_code=201,
    )
    async def add_member(
        account_id: str,
        body: MemberAdd,
        user: SessionPayload = require_permission_scoped(Permission.ACCOUNT_MANAGE),
    ) -> MemberView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            account = await _require_account_access(repo, account_id, caller)
            member_self = await repo.is_member(account_id=account_id, user_id=caller)
            await _require_owner_or_admin(account, member_self, caller)
            try:
                resolved_uid = await _resolve_invitee_user_id(
                    session=session, email=body.email, user_id=body.user_id
                )
                member = await repo.add_member(
                    account_id=account_id,
                    user_id=resolved_uid,
                    role=body.role,
                    invited_by_user_id=caller,
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            u = await session.get(User, resolved_uid)
            return MemberView(
                user_id=member.user_id,
                role=member.role,
                joined_at=member.joined_at,
                email=u.email if u else None,
                name=u.name if u else None,
            )

    @router.get(
        "/accounts/{account_id}/members",
        response_model=list[MemberView],
    )
    async def list_members(account_id: str, user: CurrentUser) -> list[MemberView]:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            await _require_account_access(repo, account_id, caller)
            members = await repo.list_members(account_id)
            out: list[MemberView] = []
            for m in members:
                u = await session.get(User, m.user_id)
                out.append(
                    MemberView(
                        user_id=m.user_id,
                        role=m.role,
                        joined_at=m.joined_at,
                        email=u.email if u else None,
                        name=u.name if u else None,
                    )
                )
            return out

    @router.delete("/accounts/{account_id}/members/{uid}", status_code=204)
    async def remove_member(
        account_id: str,
        uid: str,
        user: SessionPayload = require_permission_scoped(Permission.ACCOUNT_MANAGE),
    ) -> None:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            account = await _require_account_access(repo, account_id, caller)
            member_self = await repo.is_member(account_id=account_id, user_id=caller)
            await _require_owner_or_admin(account, member_self, caller)
            try:
                ok = await repo.remove_member(account_id=account_id, user_id=uid)
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            if not ok:
                raise HTTPException(404, "Member not found.")

    # ── Account sites ───────────────────────────────────────────────

    @router.post(
        "/accounts/{account_id}/sites",
        response_model=AccountSiteView,
        status_code=201,
    )
    async def create_account_site(
        account_id: str,
        body: AccountSiteCreate,
        user: SessionPayload = require_permission_scoped(Permission.ACCOUNT_MANAGE),
    ) -> AccountSiteView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            account = await _require_account_access(repo, account_id, caller)
            member = await repo.is_member(account_id=account_id, user_id=caller)
            await _require_owner_or_admin(account, member, caller)
            try:
                site = await repo.create_site(
                    account_id,
                    name=body.name,
                    code=body.code,
                    address=body.address,
                    contact_email=body.contact_email,
                    pi_name=body.pi_name,
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            return AccountSiteView.model_validate(site)

    @router.get(
        "/accounts/{account_id}/sites",
        response_model=list[AccountSiteView],
    )
    async def list_account_sites(account_id: str, user: CurrentUser) -> list[AccountSiteView]:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            await _require_account_access(repo, account_id, caller)
            sites = await repo.list_sites(account_id)
            return [AccountSiteView.model_validate(s) for s in sites]

    @router.patch(
        "/account-sites/{site_id}",
        response_model=AccountSiteView,
    )
    async def update_account_site(
        site_id: str,
        body: AccountSitePatch,
        user: SessionPayload = require_permission_scoped(Permission.ACCOUNT_MANAGE),
    ) -> AccountSiteView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            site = await repo.get_site(site_id)
            if site is None:
                raise HTTPException(404, "Account site not found.")
            account = await _require_account_access(repo, site.account_id, caller)
            member = await repo.is_member(account_id=site.account_id, user_id=caller)
            await _require_owner_or_admin(account, member, caller)
            try:
                updated = await repo.update_site(
                    site_id,
                    **{
                        k: v
                        for k, v in body.model_dump(exclude_unset=True).items()
                        if v is not None
                    },
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            assert updated is not None
            return AccountSiteView.model_validate(updated)

    # ── Trials ──────────────────────────────────────────────────────

    @router.post(
        "/accounts/{account_id}/trials",
        response_model=TrialView,
        status_code=201,
    )
    async def create_trial(
        account_id: str,
        body: TrialCreate,
        user: SessionPayload = require_permission_scoped(Permission.TRIAL_MANAGE),
    ) -> TrialView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            await _require_account_access(repo, account_id, caller)
            try:
                trial = await repo.create_trial(
                    account_id,
                    title=body.title,
                    sponsor=body.sponsor,
                    indication=body.indication,
                    phase=body.phase,
                    protocol_id=body.protocol_id,
                    created_by_user_id=caller,
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            return TrialView.model_validate(trial)

    @router.get(
        "/accounts/{account_id}/trials",
        response_model=list[TrialView],
    )
    async def list_trials(
        account_id: str,
        user: CurrentUser,
        status: str | None = None,
    ) -> list[TrialView]:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            await _require_account_access(repo, account_id, caller)
            trials = await repo.list_trials_for_account(account_id, status=status)
            return [TrialView.model_validate(t) for t in trials]

    @router.get(
        "/trials/{trial_id}",
        response_model=TrialDetailView,
    )
    async def get_trial(trial_id: str, user: CurrentUser) -> TrialDetailView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            trial = await repo.get_trial(trial_id)
            if trial is None:
                raise HTTPException(404, "Trial not found.")
            await _require_account_access(repo, trial.account_id, caller)
            assignments = await repo.list_trial_sites(trial_id)
            site_views: list[AccountSiteView] = []
            for a in assignments:
                site_obj = await repo.get_site(a.account_site_id)
                if site_obj is not None:
                    site_views.append(AccountSiteView.model_validate(site_obj))
            # Sprint A3: surface every Study + its Deployments + lock state
            # so /accounts.html trial-detail can deep-link the operator
            # straight into the Phase-04 surface they were working in.
            # Lock state lives in the clinical store; we read both stores in
            # a single GET to avoid an N+1 over per-deployment fetches.
            studies_res = await session.scalars(
                select(EcrfStudy).where(EcrfStudy.trial_id == trial_id)
            )
            studies_list = list(studies_res)
            study_ids = [s.id for s in studies_list]
            study_refs: list[StudyRefView] = []
            n_deployments = 0
            if study_ids:
                from ..persistence.clinical.database import get_clinical_session
                from ..persistence.clinical.models import StudyDeployment, StudyLock

                async with get_clinical_session() as cs:
                    deps_res = await cs.scalars(
                        select(StudyDeployment).where(
                            StudyDeployment.research_study_id.in_(study_ids)
                        )
                    )
                    deps_list = list(deps_res)
                    n_deployments = len(deps_list)
                    locked_ids: set[str] = set()
                    if deps_list:
                        locks_res = await cs.scalars(
                            select(StudyLock).where(
                                StudyLock.deployment_id.in_([d.id for d in deps_list]),
                                StudyLock.locked_at.isnot(None),
                                StudyLock.unlocked_at.is_(None),
                            )
                        )
                        locked_ids = {ln.deployment_id for ln in locks_res}
                    deps_by_study: dict[str, list[DeploymentRefView]] = {}
                    for d in deps_list:
                        deps_by_study.setdefault(d.research_study_id, []).append(
                            DeploymentRefView(
                                deployment_id=d.id,
                                name=d.name,
                                is_locked=d.id in locked_ids,
                            )
                        )
                for s in studies_list:
                    study_refs.append(
                        StudyRefView(
                            study_id=s.id,
                            name=s.name,
                            status=s.status,
                            deployments=deps_by_study.get(s.id, []),
                        )
                    )
            base = TrialView.model_validate(trial)
            return TrialDetailView(
                **base.model_dump(),
                sites=site_views,
                n_deployments=int(n_deployments),
                studies=study_refs,
            )

    @router.patch("/trials/{trial_id}", response_model=TrialView)
    async def update_trial(
        trial_id: str,
        body: TrialPatch,
        user: SessionPayload = require_permission_scoped(Permission.TRIAL_MANAGE),
    ) -> TrialView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            trial = await repo.get_trial(trial_id)
            if trial is None:
                raise HTTPException(404, "Trial not found.")
            await _require_account_access(repo, trial.account_id, caller)
            try:
                updated = await repo.update_trial(
                    trial_id,
                    **{
                        k: v
                        for k, v in body.model_dump(exclude_unset=True).items()
                        if v is not None
                    },
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            assert updated is not None
            return TrialView.model_validate(updated)

    @router.post(
        "/trials/{trial_id}/sites",
        response_model=TrialSiteView,
        status_code=201,
    )
    async def assign_site(
        trial_id: str,
        body: TrialSiteAssign,
        user: SessionPayload = require_permission_scoped(Permission.TRIAL_MANAGE),
    ) -> TrialSiteView:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            trial = await repo.get_trial(trial_id)
            if trial is None:
                raise HTTPException(404, "Trial not found.")
            await _require_account_access(repo, trial.account_id, caller)
            try:
                ts = await repo.assign_site_to_trial(
                    trial_id=trial_id,
                    account_site_id=body.account_site_id,
                    notes=body.notes,
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            return TrialSiteView(
                id=ts.id,
                trial_id=ts.trial_id,
                account_site_id=ts.account_site_id,
                status=ts.status,
                activated_at=ts.activated_at,
                notes=ts.notes,
            )

    @router.delete("/trials/{trial_id}/sites/{site_id}", status_code=204)
    async def unassign_site(
        trial_id: str,
        site_id: str,
        user: SessionPayload = require_permission_scoped(Permission.TRIAL_MANAGE),
    ) -> None:
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            trial = await repo.get_trial(trial_id)
            if trial is None:
                raise HTTPException(404, "Trial not found.")
            await _require_account_access(repo, trial.account_id, caller)
            ok = await repo.unassign_site_from_trial(trial_id=trial_id, account_site_id=site_id)
            if not ok:
                raise HTTPException(404, "Site assignment not found.")

    @router.post(
        "/accounts/{account_id}/transfer-ownership",
        response_model=AccountView,
    )
    async def transfer_ownership(
        account_id: str,
        body: TransferOwnership,
        user: SessionPayload = require_permission_scoped(Permission.ACCOUNT_MANAGE),
    ) -> AccountView:
        """Transfer Account.owner_user_id to a new member. Caller must
        be the current owner. New owner is promoted to 'owner'; old
        owner downgraded to 'admin' (kept in the account)."""
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            account = await repo.get_account(account_id)
            if account is None:
                raise HTTPException(404, "Account not found.")
            # Only the current owner can transfer ownership (admins can't).
            if account.owner_user_id != caller and caller != DEFAULT_USER_ID:
                raise HTTPException(403, "Only the current owner can transfer ownership.")
            try:
                resolved_uid = await _resolve_invitee_user_id(
                    session=session,
                    email=body.new_owner_email,
                    user_id=body.new_owner_user_id,
                )
                updated = await repo.transfer_ownership(
                    account_id=account_id,
                    new_owner_user_id=resolved_uid,
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            return await _build_account_view(session, updated)

    @router.post(
        "/trials/{trial_id}/artefacts",
        response_model=TrialView,
    )
    async def bind_artefact(
        trial_id: str,
        body: ArtefactBindIn,
        user: SessionPayload = require_permission_scoped(Permission.TRIAL_MANAGE),
    ) -> TrialView:
        """Bind a Thread to one of the Trial's artefact slots
        (registration / irb / sap / csr / manuscript). Pass
        thread_id=null to unbind."""
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            trial = await repo.get_trial(trial_id)
            if trial is None:
                raise HTTPException(404, "Trial not found.")
            await _require_account_access(repo, trial.account_id, caller)
            try:
                updated = await repo.bind_trial_artefact(
                    trial_id=trial_id,
                    kind=body.kind,
                    thread_id=body.thread_id,
                )
            except AccountError as e:
                raise HTTPException(422, str(e)) from e
            return TrialView.model_validate(updated)

    @router.get(
        "/deployments/{deployment_id}/trial",
        response_model=TrialView | None,
    )
    async def get_trial_for_deployment(deployment_id: str, user: CurrentUser) -> TrialView | None:
        """Cross-store resolver: deployment → trial. Returns null for
        legacy deployments whose EcrfStudy has no trial_id. Surface for
        collector.html to render the trial badge."""
        from ..services.trial_context import resolve_trial_for_deployment

        trial = await resolve_trial_for_deployment(deployment_id)
        if trial is None:
            return None
        # Verify the caller can see the parent account.
        caller = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = AccountRepository(session)
            try:
                await _require_account_access(repo, trial.account_id, caller)
            except HTTPException:
                return None
        return TrialView.model_validate(trial)

    return router


__all__ = ["create_accounts_router"]
