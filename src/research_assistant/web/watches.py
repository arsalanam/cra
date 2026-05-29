"""REST endpoints for living-review watches + notifications.

Endpoints:
  GET    /api/watches                  list watches (current user)
  POST   /api/watches                  create from WatchSpec
  GET    /api/watches/{id}             one watch (with run history embedded)
  PATCH  /api/watches/{id}             pause/resume/update schedule/threshold
  DELETE /api/watches/{id}             delete
  GET    /api/watches/{id}/runs        run history (limit param)
  POST   /api/watches/{id}/run-now     manual trigger (queues immediate run)
  GET    /api/notifications            list (with ?unread_only=true)
  POST   /api/notifications/{id}/read  mark read

RBAC-3: every endpoint is scoped to the calling user's `users.id`
(resolved via `web.threads.resolve_local_user_id`). Auth-disabled / dev
mode keeps DEFAULT_USER_ID as the shared bucket — same behaviour as
before, just plumbed through the resolver.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from ..domain.living_review import (
    NotificationView,
    PaperTriage,
    WatchRunView,
    WatchSpec,
    WatchView,
)
from ..persistence.database import get_db_session
from ..persistence.models import DEFAULT_USER_ID, LiteratureWatch, Notification, WatchRun
from ..persistence.repository import WatchRepository
from ..services.scheduler import (
    get_next_run,
    register_watch,
    trigger_now,
    unregister_watch,
)
from ..tools.clinical.search_papers import _fan_out
from .auth import CurrentUser
from .threads import resolve_local_user_id

logger = logging.getLogger(__name__)


def _watch_to_view(w: LiteratureWatch) -> WatchView:
    return WatchView(
        id=w.id,
        name=w.name,
        status=w.status,
        schedule_cron=w.schedule_cron,
        triage_threshold=w.triage_threshold,
        sources=json.loads(w.sources_json) if w.sources_json else [],
        baseline_size=len(json.loads(w.baseline_pmids_json) or []),
        last_run_at=w.last_run_at,
        last_run_status=w.last_run_status,
        next_run_at=w.next_run_at,
        created_at=w.created_at,
    )


def _run_to_view(r: WatchRun) -> WatchRunView:
    triages_raw = json.loads(r.triage_results_json) if r.triage_results_json else []
    return WatchRunView(
        id=r.id,
        started_at=r.started_at,
        finished_at=r.finished_at,
        status=r.status,
        total_hits=r.total_hits,
        new_pmids=json.loads(r.new_pmids_json) if r.new_pmids_json else [],
        triages=[PaperTriage.model_validate(t) for t in triages_raw],
        significance_summary=r.significance_summary,
        error_message=r.error_message,
    )


async def _notif_to_view(n: Notification, watch_lookup: dict[str, str]) -> NotificationView:
    return NotificationView(
        id=n.id,
        watch_id=n.watch_id,
        watch_name=watch_lookup.get(n.watch_id, "(deleted watch)"),
        run_id=n.run_id,
        title=n.title,
        summary=n.summary,
        new_paper_count=n.new_paper_count,
        created_at=n.created_at,
        read_at=n.read_at,
    )


class WatchPatch(BaseModel):
    """Partial-update payload."""

    name: str | None = None
    status: str | None = None  # "active" | "paused"
    schedule_cron: str | None = None
    triage_threshold: float | None = None

    model_config = ConfigDict(extra="forbid")


def create_watches_router() -> APIRouter:
    router = APIRouter(prefix="/watches", tags=["watches"])

    @router.get("", response_model=list[WatchView])
    async def list_watches(user: CurrentUser) -> list[WatchView]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            watches = await repo.list_watches(user_id=owner)
            views = [_watch_to_view(w) for w in watches]
        # Enrich with the live next_run_time from APScheduler.
        for v, w in zip(views, watches, strict=False):
            if w.status == "active":
                next_at = get_next_run(w.id)
                if next_at is not None:
                    v.next_run_at = next_at  # type: ignore[assignment]
        return views

    @router.post("", response_model=WatchView)
    async def create_watch(spec: WatchSpec, user: CurrentUser) -> WatchView:
        # Snapshot the current PMID set so the first scheduled run only
        # surfaces *genuinely* new papers. If the spec already includes
        # baseline_pmids (frontend pre-populated), use those; otherwise
        # run the search once now to populate.
        baseline = list(spec.baseline_pmids)
        if not baseline:
            try:
                envelope = json.loads(await _fan_out(spec.search_query, max_results=50))
                studies = envelope.get("studies") or []
                baseline = sorted(
                    {
                        s["pmid"]
                        for s in studies
                        if s.get("pmid")
                        and (not spec.sources or s.get("source") in set(spec.sources))
                    }
                )
                logger.info(
                    "create_watch: pre-populated baseline with %d PMIDs from initial search",
                    len(baseline),
                )
            except Exception:
                logger.exception(
                    "create_watch: initial baseline search failed; "
                    "starting with empty baseline (first run will surface all current hits)"
                )

        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            watch = await repo.create_watch(
                name=spec.name,
                pico_json=spec.pico.model_dump_json(),
                search_query=spec.search_query,
                sources_json=json.dumps(spec.sources),
                schedule_cron=spec.schedule_cron,
                triage_threshold=spec.triage_threshold,
                baseline_pmids_json=json.dumps(baseline),
                user_id=owner,
            )
            view = _watch_to_view(watch)

        # Register with the scheduler — fail loudly if cron is bad. The
        # session has already committed; on bad cron we delete the watch
        # and surface a 400 so the user can fix it.
        try:
            register_watch(watch)
            view.next_run_at = get_next_run(watch.id)  # type: ignore[assignment]
        except ValueError as e:
            async with get_db_session() as session:
                repo = WatchRepository(session)
                await repo.delete_watch(watch.id)
            raise HTTPException(400, str(e)) from e
        return view

    def _owns_watch(watch: LiteratureWatch | None, owner_id: str) -> bool:
        """Same 404-not-403 leak protection as threads."""
        if watch is None:
            return False
        return (watch.user_id or DEFAULT_USER_ID) == owner_id

    @router.get("/{watch_id}", response_model=WatchView)
    async def get_watch(watch_id: str, user: CurrentUser) -> WatchView:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            watch = await repo.get_watch(watch_id)
            if not _owns_watch(watch, owner):
                raise HTTPException(404, "Watch not found")
            assert watch is not None
            view = _watch_to_view(watch)
        if watch.status == "active":
            next_at = get_next_run(watch.id)
            if next_at is not None:
                view.next_run_at = next_at  # type: ignore[assignment]
        return view

    @router.patch("/{watch_id}", response_model=WatchView)
    async def update_watch(
        watch_id: str, body: WatchPatch, user: CurrentUser
    ) -> WatchView:
        updates = body.model_dump(exclude_unset=True)
        if "status" in updates and updates["status"] not in ("active", "paused"):
            raise HTTPException(400, "status must be 'active' or 'paused'")
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            existing = await repo.get_watch(watch_id)
            if not _owns_watch(existing, owner):
                raise HTTPException(404, "Watch not found")
            watch = await repo.update_watch(watch_id, **updates)
            if watch is None:
                raise HTTPException(404, "Watch not found")

        # Re-register / unregister based on new state.
        try:
            if watch.status == "active":
                register_watch(watch)
            else:
                unregister_watch(watch.id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

        async with get_db_session() as session:
            repo = WatchRepository(session)
            fresh = await repo.get_watch(watch_id)
            assert fresh is not None
            view = _watch_to_view(fresh)
        if fresh.status == "active":
            view.next_run_at = get_next_run(fresh.id)  # type: ignore[assignment]
        return view

    @router.delete("/{watch_id}")
    async def delete_watch(watch_id: str, user: CurrentUser) -> dict[str, bool]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            existing = await repo.get_watch(watch_id)
            if not _owns_watch(existing, owner):
                raise HTTPException(404, "Watch not found")
            ok = await repo.delete_watch(watch_id)
        if not ok:
            raise HTTPException(404, "Watch not found")
        try:
            unregister_watch(watch_id)
        except Exception:
            logger.exception("Failed to unregister deleted watch %s", watch_id)
        return {"ok": True}

    @router.get("/{watch_id}/runs", response_model=list[WatchRunView])
    async def list_runs(
        watch_id: str,
        user: CurrentUser,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[WatchRunView]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            watch = await repo.get_watch(watch_id)
            if not _owns_watch(watch, owner):
                raise HTTPException(404, "Watch not found")
            runs = await repo.list_runs(watch_id, limit=limit)
            return [_run_to_view(r) for r in runs]

    @router.post("/{watch_id}/run-now")
    async def run_now(watch_id: str, user: CurrentUser) -> dict[str, str]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            watch = await repo.get_watch(watch_id)
            if not _owns_watch(watch, owner):
                raise HTTPException(404, "Watch not found")
        trigger_now(watch_id)
        return {"status": "queued"}

    return router


def create_notifications_router() -> APIRouter:
    router = APIRouter(prefix="/notifications", tags=["notifications"])

    @router.get("", response_model=list[NotificationView])
    async def list_notifications(
        user: CurrentUser,
        unread_only: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[NotificationView]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            notifs = await repo.list_notifications(
                user_id=owner, unread_only=unread_only, limit=limit
            )
            # Build a lookup: watch_id → name (avoid N+1 fetch)
            watch_ids = {n.watch_id for n in notifs}
            lookup: dict[str, str] = {}
            for wid in watch_ids:
                w = await repo.get_watch(wid)
                if w is not None:
                    lookup[wid] = w.name
            return [await _notif_to_view(n, lookup) for n in notifs]

    @router.get("/unread-count")
    async def unread_count(user: CurrentUser) -> dict[str, int]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            return {"count": await repo.count_unread(user_id=owner)}

    @router.post("/{notification_id}/read")
    async def mark_read(notification_id: str, user: CurrentUser) -> dict[str, bool]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = WatchRepository(session)
            notif = await session.get(Notification, notification_id)
            if notif is None or (notif.user_id or DEFAULT_USER_ID) != owner:
                raise HTTPException(404, "Notification not found")
            ok = await repo.mark_notification_read(notification_id)
        if not ok:
            raise HTTPException(404, "Notification not found")
        return {"ok": True}

    return router
