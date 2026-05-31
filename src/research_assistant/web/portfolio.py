"""Portfolio dashboard endpoints (P1 #9).

Per-user rollups across the three persistence stores:

  GET /api/portfolio/threads     — per-user thread rollup (workflow,
                                    last_turn_kind, last_activity).
  GET /api/portfolio/sr-projects — per-user SR project rollup (status,
                                    screening progress).
  GET /api/portfolio/deployments — eCRF deployments visible to the
                                    user (study_designer / PI / DM /
                                    coordinator / monitor scope).

  GET /api/portfolio/org         — admin-only org-wide rollup
                                    (per-user totals; no row-level
                                    data leaked). Gated by the new
                                    `portfolio.read_org` permission.

Researcher sees their own data. Admin holds `portfolio.read_org` for
the institutional rollup. No new persistence — the endpoints aggregate
existing repository rows.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from ..auth import SessionPayload
from ..auth.rbac import Permission
from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.models import StudyDeployment, StudyLock, Subject
from ..persistence.database import get_db_session
from ..persistence.models import DEFAULT_USER_ID, Message, SrReview, Thread, User
from ..persistence.user_repository import UserRepository
from .auth import CurrentUser
from .authz import require_permission_scoped

logger = logging.getLogger(__name__)


# ── DTOs ───────────────────────────────────────────────────────────────


class PortfolioThreadOut(BaseModel):
    id: str
    title: str
    workflow: str | None
    last_turn_kind: str | None
    message_count: int
    last_activity: datetime


class PortfolioSrReviewOut(BaseModel):
    id: str
    title: str
    status: str | None
    n_candidates: int
    n_included: int
    n_excluded: int
    n_pending: int
    last_activity: datetime


class PortfolioDeploymentOut(BaseModel):
    id: str
    name: str
    status: str
    is_locked: bool
    n_subjects: int
    last_activity: datetime


class PortfolioRolloutOut(BaseModel):
    threads_by_workflow: dict[str, int]
    threads_total: int
    sr_projects_total: int
    deployments_total: int


class UserTotalsOut(BaseModel):
    sub: str
    email: str | None
    name: str | None
    role_summary: list[str]
    thread_count: int
    sr_project_count: int
    deployment_count: int
    last_activity: datetime | None


class OrgRolloutOut(BaseModel):
    org_totals: PortfolioRolloutOut
    users: list[UserTotalsOut]


# ── Helpers ────────────────────────────────────────────────────────────


async def _resolve_user_id(user: SessionPayload) -> str:
    """Mirror the helper in web/threads.py — translate session sub → local user id."""
    if user.sub == DEFAULT_USER_ID:
        return DEFAULT_USER_ID
    async with get_db_session() as session:
        local = await UserRepository(session).get_by_sub(user.sub)
    if local is None:
        return DEFAULT_USER_ID
    return local.id


def _last_turn_kind(final_answer: str | None) -> str | None:
    if not final_answer:
        return None
    try:
        return str(json.loads(final_answer).get("kind"))
    except (json.JSONDecodeError, AttributeError):
        return None


# ── Router factory ─────────────────────────────────────────────────────


def create_portfolio_router() -> APIRouter:
    router = APIRouter(prefix="/portfolio", tags=["portfolio"])

    @router.get("/threads", response_model=list[PortfolioThreadOut])
    async def list_my_threads(
        user: CurrentUser,
        limit: int = 100,
    ) -> list[PortfolioThreadOut]:
        owner_id = await _resolve_user_id(user)
        async with get_db_session() as session:
            stmt = (
                select(Thread)
                .where(Thread.user_id == owner_id)
                .order_by(Thread.updated_at.desc())
                .limit(limit)
            )
            threads = list((await session.scalars(stmt)).all())
            out: list[PortfolioThreadOut] = []
            for t in threads:
                count_stmt = select(func.count()).where(Message.thread_id == t.id)
                count = int(await session.scalar(count_stmt) or 0)
                last_assistant_stmt = (
                    select(Message)
                    .where(Message.thread_id == t.id, Message.role == "assistant")
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                last_assistant = (
                    await session.scalars(last_assistant_stmt)
                ).first()
                kind = _last_turn_kind(
                    last_assistant.final_answer if last_assistant else None
                )
                out.append(
                    PortfolioThreadOut(
                        id=t.id,
                        title=t.title,
                        workflow=t.workflow,
                        last_turn_kind=kind,
                        message_count=count,
                        last_activity=t.updated_at,
                    )
                )
            return out

    @router.get("/sr-projects", response_model=list[PortfolioSrReviewOut])
    async def list_my_sr_projects(
        user: CurrentUser,
    ) -> list[PortfolioSrReviewOut]:
        owner_id = await _resolve_user_id(user)
        from ..auth.rbac import ScopeType
        from ..persistence.models import RoleAssignment, SrCandidate

        async with get_db_session() as session:
            # Owned projects.
            own_stmt = (
                select(SrReview)
                .where(SrReview.created_by == owner_id)
                .order_by(SrReview.updated_at.desc())
            )
            owned = list((await session.scalars(own_stmt)).all())
            # Plus projects where the user holds a sr_review-scoped role.
            member_stmt = select(RoleAssignment.scope_id).where(
                RoleAssignment.user_id == owner_id,
                RoleAssignment.scope_type == ScopeType.SR_REVIEW.value,
            )
            member_ids = [
                r for r in (await session.scalars(member_stmt)).all() if r is not None
            ]
            if member_ids:
                member_stmt2 = (
                    select(SrReview)
                    .where(SrReview.id.in_(member_ids))
                    .order_by(SrReview.updated_at.desc())
                )
                members = list((await session.scalars(member_stmt2)).all())
            else:
                members = []
            seen: set[str] = set()
            combined: list[SrReview] = []
            for p in [*owned, *members]:
                if p.id in seen:
                    continue
                combined.append(p)
                seen.add(p.id)

            out: list[PortfolioSrReviewOut] = []
            for p in combined:
                count_stmt = (
                    select(SrCandidate.current_status, func.count())
                    .where(SrCandidate.sr_review_id == p.id)
                    .group_by(SrCandidate.current_status)
                )
                status_counts: dict[str, int] = {
                    row[0] or "unknown": int(row[1])
                    for row in (await session.execute(count_stmt)).all()
                }
                n_candidates = sum(status_counts.values())
                n_included = status_counts.get("included", 0)
                n_excluded = status_counts.get("excluded", 0)
                n_pending = (
                    n_candidates - n_included - n_excluded
                )
                out.append(
                    PortfolioSrReviewOut(
                        id=p.id,
                        title=p.name,
                        status=p.status,
                        n_candidates=n_candidates,
                        n_included=n_included,
                        n_excluded=n_excluded,
                        n_pending=n_pending,
                        last_activity=p.updated_at,
                    )
                )
            return out

    async def _visible_deployments(
        owner_id: str,
    ) -> list[PortfolioDeploymentOut]:
        from ..auth.rbac import ScopeType
        from ..persistence.clinical.models import Site
        from ..persistence.models import RoleAssignment

        async with get_db_session() as session:
            asgn_stmt = select(RoleAssignment).where(
                RoleAssignment.user_id == owner_id
            )
            assignments = list((await session.scalars(asgn_stmt)).all())
        global_admin = any(
            a.scope_type == ScopeType.GLOBAL.value and a.role == "admin"
            for a in assignments
        )
        scoped_study_ids = {
            a.scope_id
            for a in assignments
            if a.scope_type == ScopeType.STUDY.value and a.scope_id
        }
        site_scope_ids = {
            a.scope_id
            for a in assignments
            if a.scope_type == ScopeType.SITE.value and a.scope_id
        }
        async with get_clinical_session() as cs:
            stmt = select(StudyDeployment).order_by(
                StudyDeployment.created_at.desc()
            )
            deployments = list((await cs.scalars(stmt)).all())
            out: list[PortfolioDeploymentOut] = []
            for d in deployments:
                visible = global_admin
                if not visible and d.research_study_id in scoped_study_ids:
                    visible = True
                if not visible and site_scope_ids:
                    site_stmt = select(Site.id).where(
                        Site.deployment_id == d.id, Site.id.in_(site_scope_ids)
                    )
                    if (await cs.scalars(site_stmt)).first() is not None:
                        visible = True
                if not visible:
                    continue
                subj_count_stmt = select(func.count()).where(
                    Subject.deployment_id == d.id
                )
                n_subjects = int(await cs.scalar(subj_count_stmt) or 0)
                lock_stmt = (
                    select(StudyLock)
                    .where(
                        StudyLock.deployment_id == d.id,
                        StudyLock.unlocked_at.is_(None),
                    )
                    .order_by(StudyLock.locked_at.desc())
                )
                lock_row = (await cs.scalars(lock_stmt)).first()
                out.append(
                    PortfolioDeploymentOut(
                        id=d.id,
                        name=d.name,
                        status=d.status,
                        is_locked=lock_row is not None,
                        n_subjects=n_subjects,
                        last_activity=d.created_at,
                    )
                )
            return out

    @router.get("/deployments", response_model=list[PortfolioDeploymentOut])
    async def list_my_deployments(
        user: CurrentUser,
    ) -> list[PortfolioDeploymentOut]:
        """eCRF deployments the caller can see.

        Filtering: a deployment is included when the caller holds any
        eCRF-scoped RoleAssignment that resolves to it (global, study,
        or site scope). Researcher-only callers see an empty list.
        """
        owner_id = await _resolve_user_id(user)
        return await _visible_deployments(owner_id)

    @router.get("/summary", response_model=PortfolioRolloutOut)
    async def get_summary(user: CurrentUser) -> PortfolioRolloutOut:
        """Aggregated counts for the per-user dashboard hero cards."""
        owner_id = await _resolve_user_id(user)
        async with get_db_session() as session:
            workflow_stmt = (
                select(Thread.workflow, func.count())
                .where(Thread.user_id == owner_id)
                .group_by(Thread.workflow)
            )
            by_workflow_raw = (await session.execute(workflow_stmt)).all()
            by_workflow = {
                (row[0] or "unrouted"): int(row[1]) for row in by_workflow_raw
            }
            threads_total = int(sum(by_workflow.values()))
            sr_stmt = select(func.count()).where(SrReview.created_by == owner_id)
            sr_total = int(await session.scalar(sr_stmt) or 0)
        deployments = await _visible_deployments(owner_id)
        return PortfolioRolloutOut(
            threads_by_workflow=by_workflow,
            threads_total=threads_total,
            sr_projects_total=sr_total,
            deployments_total=len(deployments),
        )

    @router.get("/org", response_model=OrgRolloutOut)
    async def get_org_rollup(
        user: SessionPayload = require_permission_scoped(
            Permission.PORTFOLIO_READ_ORG
        ),
    ) -> OrgRolloutOut:
        """Admin-only org rollup. Per-user totals (no row-level data
        beyond the user's name + sub)."""
        async with get_db_session() as session:
            users = list((await session.scalars(select(User))).all())
            thread_counts_stmt = (
                select(Thread.user_id, func.count())
                .group_by(Thread.user_id)
            )
            thread_counts: dict[str, int] = {
                (row[0] or DEFAULT_USER_ID): int(row[1])
                for row in (await session.execute(thread_counts_stmt)).all()
            }
            sr_counts_stmt = (
                select(SrReview.created_by, func.count())
                .group_by(SrReview.created_by)
            )
            sr_counts: dict[str, int] = {
                (row[0] or DEFAULT_USER_ID): int(row[1])
                for row in (await session.execute(sr_counts_stmt)).all()
            }
            from ..persistence.models import RoleAssignment

            asgn_stmt = select(RoleAssignment)
            role_counts_per_user: dict[str, Counter[str]] = {}
            for a in (await session.scalars(asgn_stmt)).all():
                if a.user_id is None:
                    continue
                role_counts_per_user.setdefault(a.user_id, Counter())[a.role] += 1
            last_activity_stmt = (
                select(Thread.user_id, func.max(Thread.updated_at))
                .group_by(Thread.user_id)
            )
            last_activity_map: dict[str, datetime] = {
                (row[0] or DEFAULT_USER_ID): row[1]
                for row in (await session.execute(last_activity_stmt)).all()
                if row[1] is not None
            }

        async with get_clinical_session() as cs:
            n_deployments_total = int(
                await cs.scalar(select(func.count()).select_from(StudyDeployment)) or 0
            )

        per_user: list[UserTotalsOut] = []
        for u in users:
            role_counter = role_counts_per_user.get(u.id, Counter())
            per_user.append(
                UserTotalsOut(
                    sub=u.cognito_sub or u.id,
                    email=u.email,
                    name=u.email or u.id,
                    role_summary=sorted(role_counter.keys()),
                    thread_count=thread_counts.get(u.id, 0),
                    sr_project_count=sr_counts.get(u.id, 0),
                    deployment_count=0,  # per-user FK not tracked; org total below.
                    last_activity=last_activity_map.get(u.id),
                )
            )
        org_totals = PortfolioRolloutOut(
            threads_by_workflow={},
            threads_total=sum(thread_counts.values()),
            sr_projects_total=sum(sr_counts.values()),
            deployments_total=n_deployments_total,
        )
        return OrgRolloutOut(org_totals=org_totals, users=per_user)

    return router


__all__ = ["create_portfolio_router"]
