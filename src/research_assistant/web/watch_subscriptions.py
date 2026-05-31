"""REST endpoints for group-level living-review subscriptions (P2 #4).

Endpoints (all under /api/watch-subscriptions):

  GET    /                          list subscriptions the caller is a member of
  POST   /                          create from a watch_id + group config
  GET    /{sub_id}                  one subscription (with members embedded)
  PATCH  /{sub_id}                  update name / status / min_votes / min_fraction
  DELETE /{sub_id}                  delete (cascades to members + votes + group notifications)

  POST   /{sub_id}/members          add a member by email or local user_id
  DELETE /{sub_id}/members/{uid}    remove a member

  GET    /{sub_id}/runs             surface WatchRuns awaiting vote (+ tally summary)
  POST   /{sub_id}/runs/{run_id}/vote     cast yes/no/abstain (idempotent upsert)
  GET    /{sub_id}/runs/{run_id}/tally    current vote breakdown + quorum status

Permissions:
  • watch.manage     — create / update / delete a subscription, manage members.
  • watch_subscription.vote — cast a vote (the user must also be a member).
  • watch.read       — see subscriptions you own.

Membership additionally gates per-subscription reads at runtime: a user
who has watch.read globally but is not a member of subscription X cannot
see X's tally. The membership check happens in the endpoint after the
permission gate.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..auth import SessionPayload
from ..auth.rbac import Permission
from ..persistence.database import get_db_session
from ..persistence.models import (
    DEFAULT_USER_ID,
    LiteratureWatchSubscription,
    SubscriptionMember,
    User,
)
from ..persistence.repository import (
    SubscriptionError,
    SubscriptionRepository,
    WatchRepository,
)
from ..persistence.user_repository import UserRepository
from ..services.watch_subscriptions import record_vote_and_fanout
from .auth import CurrentUser
from .authz import require_permission_scoped
from .threads import resolve_local_user_id

logger = logging.getLogger(__name__)


# ── DTOs ─────────────────────────────────────────────────────────────────


class SubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=200)
    watch_id: str
    description: str = ""
    min_votes: int = Field(default=2, ge=1)
    min_fraction: float = Field(default=0.5, ge=0.0, le=1.0)


class SubscriptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None
    status: str | None = None  # "active" | "paused"
    min_votes: int | None = Field(default=None, ge=1)
    min_fraction: float | None = Field(default=None, ge=0.0, le=1.0)


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Caller supplies either an email (preferred — resolved via UserRepository)
    # or a local user_id directly. The endpoint rejects requests that provide
    # neither.
    email: str | None = None
    user_id: str | None = None
    role: str = "voter"


class MemberView(BaseModel):
    user_id: str
    role: str
    joined_at: datetime
    email: str | None = None
    name: str | None = None


class SubscriptionView(BaseModel):
    id: str
    name: str
    description: str
    watch_id: str
    watch_name: str | None = None
    owner_user_id: str | None
    status: str
    min_votes: int
    min_fraction: float
    n_voters: int
    n_observers: int
    created_at: datetime
    updated_at: datetime


class SubscriptionDetailView(SubscriptionView):
    members: list[MemberView] = Field(default_factory=list)


class VoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vote: str = Field(pattern="^(yes|no|abstain)$")
    rationale: str = ""


class TallyView(BaseModel):
    subscription_id: str
    run_id: str
    yes: int
    no: int
    abstain: int
    n_voters: int
    threshold: int
    quorum_cleared: bool
    notifications_created: int | None = None


class RunSummaryView(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    total_hits: int
    status: str
    tally: TallyView
    has_voted: bool


# ── Helpers ──────────────────────────────────────────────────────────────


async def _build_subscription_view(
    sub: LiteratureWatchSubscription,
    *,
    session: Any,
) -> SubscriptionView:
    members = await SubscriptionRepository(session).list_members(sub.id)
    watch_repo = WatchRepository(session)
    watch = await watch_repo.get_watch(sub.watch_id)
    return SubscriptionView(
        id=sub.id,
        name=sub.name,
        description=sub.description,
        watch_id=sub.watch_id,
        watch_name=watch.name if watch else None,
        owner_user_id=sub.owner_user_id,
        status=sub.status,
        min_votes=sub.min_votes,
        min_fraction=sub.min_fraction,
        n_voters=sum(1 for m in members if m.role == "voter"),
        n_observers=sum(1 for m in members if m.role == "observer"),
        created_at=sub.created_at,
        updated_at=sub.updated_at,
    )


async def _resolve_member_user_id(
    *,
    session: Any,
    email: str | None,
    user_id: str | None,
) -> str:
    """Map the {email | user_id} pair from a MemberAdd to a local user id.

    Raises HTTPException(404) if no matching user. The email path is
    case-insensitive."""
    if not email and not user_id:
        raise HTTPException(422, "Provide either email or user_id.")
    if user_id is not None:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(404, f"User {user_id!r} not found.")
        return str(user.id)
    assert email is not None
    repo = UserRepository(session)
    user = await repo.get_by_email(email.strip().lower())
    if user is None:
        raise HTTPException(
            404,
            f"No user with email {email!r}. Invite them to the platform first.",
        )
    return str(user.id)


async def _require_owner_or_admin(
    sub: LiteratureWatchSubscription | None,
    *,
    caller_user_id: str,
) -> LiteratureWatchSubscription:
    if sub is None:
        raise HTTPException(404, "Subscription not found.")
    if sub.owner_user_id != caller_user_id and caller_user_id != DEFAULT_USER_ID:
        # DEFAULT_USER_ID is the auth-disabled single-tenant case — keep
        # the legacy "everyone is god" behaviour there.
        raise HTTPException(403, "Only the subscription owner can perform this action.")
    return sub


async def _require_membership(
    *,
    session: Any,
    subscription_id: str,
    caller_user_id: str,
) -> SubscriptionMember:
    member = await SubscriptionRepository(session).is_member(
        subscription_id=subscription_id, user_id=caller_user_id
    )
    if member is None:
        raise HTTPException(403, "You are not a member of this subscription.")
    return member


# ── Router ───────────────────────────────────────────────────────────────


def create_subscriptions_router() -> APIRouter:
    router = APIRouter(
        prefix="/watch-subscriptions",
        tags=["watch-subscriptions"],
    )

    # ── Subscriptions ────────────────────────────────────────────────

    @router.get("", response_model=list[SubscriptionView])
    async def list_subscriptions(user: CurrentUser) -> list[SubscriptionView]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            subs = await repo.list_subscriptions_for_user(owner)
            views: list[SubscriptionView] = []
            for sub in subs:
                views.append(await _build_subscription_view(sub, session=session))
        return views

    @router.post("", response_model=SubscriptionView, status_code=201)
    async def create_subscription(
        body: SubscriptionCreate,
        user: SessionPayload = require_permission_scoped(Permission.WATCH_MANAGE),
    ) -> SubscriptionView:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            try:
                sub = await repo.create_subscription(
                    name=body.name,
                    watch_id=body.watch_id,
                    owner_user_id=owner,
                    description=body.description,
                    min_votes=body.min_votes,
                    min_fraction=body.min_fraction,
                )
            except SubscriptionError as e:
                raise HTTPException(422, str(e)) from e
            return await _build_subscription_view(sub, session=session)

    @router.get("/{sub_id}", response_model=SubscriptionDetailView)
    async def get_subscription(sub_id: str, user: CurrentUser) -> SubscriptionDetailView:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            sub = await repo.get_subscription(sub_id)
            if sub is None:
                raise HTTPException(404, "Subscription not found.")
            # Membership OR ownership required to view detail.
            if sub.owner_user_id != owner and owner != DEFAULT_USER_ID:
                member = await repo.is_member(subscription_id=sub_id, user_id=owner)
                if member is None:
                    raise HTTPException(404, "Subscription not found.")
            base = await _build_subscription_view(sub, session=session)
            members = await repo.list_members(sub_id)
            member_views: list[MemberView] = []
            for m in members:
                u = await session.get(User, m.user_id)
                member_views.append(
                    MemberView(
                        user_id=m.user_id,
                        role=m.role,
                        joined_at=m.joined_at,
                        email=u.email if u is not None else None,
                        name=u.name if u is not None else None,
                    )
                )
            return SubscriptionDetailView(**base.model_dump(), members=member_views)

    @router.patch("/{sub_id}", response_model=SubscriptionView)
    async def update_subscription(
        sub_id: str,
        body: SubscriptionPatch,
        user: SessionPayload = require_permission_scoped(Permission.WATCH_MANAGE),
    ) -> SubscriptionView:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            sub = await repo.get_subscription(sub_id)
            await _require_owner_or_admin(sub, caller_user_id=owner)
            updates = {
                k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None
            }
            try:
                updated = await repo.update_subscription(sub_id, **updates)
            except SubscriptionError as e:
                raise HTTPException(422, str(e)) from e
            assert updated is not None
            return await _build_subscription_view(updated, session=session)

    @router.delete("/{sub_id}", status_code=204)
    async def delete_subscription(
        sub_id: str,
        user: SessionPayload = require_permission_scoped(Permission.WATCH_MANAGE),
    ) -> None:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            sub = await repo.get_subscription(sub_id)
            await _require_owner_or_admin(sub, caller_user_id=owner)
            await repo.delete_subscription(sub_id)

    # ── Members ──────────────────────────────────────────────────────

    @router.post(
        "/{sub_id}/members",
        response_model=MemberView,
        status_code=201,
    )
    async def add_member(
        sub_id: str,
        body: MemberAdd,
        user: SessionPayload = require_permission_scoped(Permission.WATCH_MANAGE),
    ) -> MemberView:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            sub = await repo.get_subscription(sub_id)
            await _require_owner_or_admin(sub, caller_user_id=owner)
            try:
                resolved_uid = await _resolve_member_user_id(
                    session=session, email=body.email, user_id=body.user_id
                )
                member = await repo.add_member(
                    subscription_id=sub_id,
                    user_id=resolved_uid,
                    role=body.role,
                    invited_by_user_id=owner,
                )
            except SubscriptionError as e:
                raise HTTPException(422, str(e)) from e
            u = await session.get(User, resolved_uid)
            return MemberView(
                user_id=member.user_id,
                role=member.role,
                joined_at=member.joined_at,
                email=u.email if u else None,
                name=u.name if u else None,
            )

    @router.delete("/{sub_id}/members/{uid}", status_code=204)
    async def remove_member(
        sub_id: str,
        uid: str,
        user: SessionPayload = require_permission_scoped(Permission.WATCH_MANAGE),
    ) -> None:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            sub = await repo.get_subscription(sub_id)
            await _require_owner_or_admin(sub, caller_user_id=owner)
            ok = await repo.remove_member(subscription_id=sub_id, user_id=uid)
            if not ok:
                raise HTTPException(404, "Member not found.")

    # ── Runs + voting ───────────────────────────────────────────────

    @router.get(
        "/{sub_id}/runs",
        response_model=list[RunSummaryView],
    )
    async def list_subscription_runs(
        sub_id: str,
        user: CurrentUser,
        limit: int = 20,
    ) -> list[RunSummaryView]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            sub = await repo.get_subscription(sub_id)
            if sub is None:
                raise HTTPException(404, "Subscription not found.")
            if sub.owner_user_id != owner and owner != DEFAULT_USER_ID:
                member = await repo.is_member(subscription_id=sub_id, user_id=owner)
                if member is None:
                    raise HTTPException(404, "Subscription not found.")
            watch_repo = WatchRepository(session)
            runs = await watch_repo.list_runs(sub.watch_id, limit=limit)
            views: list[RunSummaryView] = []
            for run in runs:
                tally = await repo.tally(subscription_id=sub_id, run_id=run.id)
                my_votes = await repo.list_votes(subscription_id=sub_id, run_id=run.id)
                has_voted = any(v.voter_user_id == owner for v in my_votes)
                views.append(
                    RunSummaryView(
                        run_id=run.id,
                        started_at=run.started_at,
                        finished_at=run.finished_at,
                        total_hits=run.total_hits,
                        status=run.status,
                        tally=TallyView(**tally),  # type: ignore[arg-type]
                        has_voted=has_voted,
                    )
                )
            return views

    @router.post(
        "/{sub_id}/runs/{run_id}/vote",
        response_model=TallyView,
    )
    async def cast_vote(
        sub_id: str,
        run_id: str,
        body: VoteIn,
        user: SessionPayload = require_permission_scoped(Permission.WATCH_SUBSCRIPTION_VOTE),
    ) -> TallyView:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            try:
                tally = await record_vote_and_fanout(
                    session,
                    subscription_id=sub_id,
                    run_id=run_id,
                    voter_user_id=owner,
                    vote=body.vote,
                    rationale=body.rationale,
                )
            except SubscriptionError as e:
                raise HTTPException(422, str(e)) from e
            return TallyView(**tally)  # type: ignore[arg-type]

    @router.get(
        "/{sub_id}/runs/{run_id}/tally",
        response_model=TallyView,
    )
    async def get_tally(
        sub_id: str,
        run_id: str,
        user: CurrentUser,
    ) -> TallyView:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SubscriptionRepository(session)
            sub = await repo.get_subscription(sub_id)
            if sub is None:
                raise HTTPException(404, "Subscription not found.")
            if sub.owner_user_id != owner and owner != DEFAULT_USER_ID:
                await _require_membership(
                    session=session,
                    subscription_id=sub_id,
                    caller_user_id=owner,
                )
            try:
                tally = await repo.tally(subscription_id=sub_id, run_id=run_id)
            except SubscriptionError as e:
                raise HTTPException(422, str(e)) from e
            return TallyView(**tally)  # type: ignore[arg-type]

    return router


__all__ = [
    "create_subscriptions_router",
]
