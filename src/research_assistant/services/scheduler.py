"""APScheduler wrapper for living-review watches.

Single global AsyncIOScheduler instance, MemoryJobStore. Source of truth
for which watches exist is the LiteratureWatch table — at FastAPI
startup we re-register active watches; on /api/watches CRUD we update
the in-memory schedule. Process restart loses the in-memory schedule
but re-registers from DB cleanly, so nothing is actually lost.

Job execution itself lives in `services.watch_runner` — this module
just maps cron strings to job invocations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from ..persistence.models import LiteratureWatch

logger = logging.getLogger(__name__)


_scheduler: AsyncIOScheduler | None = None


def _job_id(watch_id: str) -> str:
    return f"watch:{watch_id}"


def get_scheduler() -> AsyncIOScheduler:
    """Return the singleton scheduler. Raises if not started."""
    if _scheduler is None:
        raise RuntimeError(
            "Scheduler has not been started. Call start_scheduler() during "
            "FastAPI lifespan startup."
        )
    return _scheduler


async def start_scheduler() -> None:
    """Initialise + start the scheduler. Re-registers all active watches.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.debug("Scheduler already running; skipping start")
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.start()
    logger.info("Scheduler started")

    # Re-register active watches from DB. Imported here to avoid a circular
    # import at module load (persistence → config → scheduler).
    from ..persistence.database import get_db_session
    from ..persistence.repository import WatchRepository

    async with get_db_session() as session:
        repo = WatchRepository(session)
        active = await repo.list_active_watches()

    registered = 0
    for watch in active:
        try:
            register_watch(watch)
            registered += 1
        except Exception:
            logger.exception(
                "Could not register watch %s on startup; skipping", watch.id
            )
    logger.info("Scheduler re-registered %d active watch(es) from DB", registered)


async def stop_scheduler() -> None:
    """Shut down the scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("Scheduler stopped")


def register_watch(watch: LiteratureWatch) -> None:
    """Add or replace the cron job for one watch.

    Raises ValueError if the cron expression is malformed.
    """
    sched = get_scheduler()
    try:
        trigger = CronTrigger.from_crontab(watch.schedule_cron)
    except ValueError as e:
        raise ValueError(
            f"Watch {watch.id!r} has malformed cron {watch.schedule_cron!r}: {e}"
        ) from e

    # Late import to dodge a circular dependency (runner imports models).
    from .watch_runner import run_watch

    sched.add_job(
        run_watch,
        trigger=trigger,
        id=_job_id(watch.id),
        args=[watch.id],
        replace_existing=True,
        max_instances=1,  # don't stack a watch on top of itself if a run runs long
        coalesce=True,    # if missed firings stack up, only run once
        misfire_grace_time=600,  # tolerate a 10-min late firing
    )
    next_fire = sched.get_job(_job_id(watch.id)).next_run_time
    logger.info(
        "Scheduler: registered watch %s (cron=%r, next=%s)",
        watch.id, watch.schedule_cron, next_fire,
    )


def unregister_watch(watch_id: str) -> None:
    """Remove the scheduled job for a watch. No-op if not scheduled."""
    sched = get_scheduler()
    job_id = _job_id(watch_id)
    if sched.get_job(job_id) is not None:
        sched.remove_job(job_id)
        logger.info("Scheduler: unregistered watch %s", watch_id)


def get_next_run(watch_id: str) -> object | None:
    """Return the next scheduled fire time for a watch, or None if not scheduled."""
    sched = get_scheduler()
    job = sched.get_job(_job_id(watch_id))
    return job.next_run_time if job is not None else None


def trigger_now(watch_id: str) -> None:
    """Fire the watch immediately (out-of-schedule). Used by /run-now endpoint."""
    sched = get_scheduler()
    from .watch_runner import run_watch
    sched.add_job(
        run_watch,
        args=[watch_id],
        id=f"watch:{watch_id}:manual:{int(__import__('time').time())}",
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduler: enqueued manual run for watch %s", watch_id)
