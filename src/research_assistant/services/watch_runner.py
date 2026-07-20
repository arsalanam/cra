"""Watch runner — the per-watch execution loop.

Invoked by APScheduler at the watch's cron schedule (or on-demand via
`/api/watches/{id}/run-now`). Steps:

  1. Load the watch row.
  2. Re-execute the saved search via `tools.clinical.search_papers._fan_out`.
  3. Diff returned PMIDs against `watch.baseline_pmids_json`.
  4. If there are new PMIDs, hand them to the watch_triage agent with the
     saved PICO and the watch's triage_threshold.
  5. Persist a WatchRun row with the diff + triage results.
  6. If `summary.notify` is True, create a Notification.
  7. Bump the watch's `baseline_pmids_json`, `last_run_at`, `last_run_status`.

Errors at any stage are caught and recorded on the WatchRun rather than
re-raised — leaving APScheduler with a clean job history. The scheduler
is dumb on purpose; the runner owns all business logic.
"""

from __future__ import annotations

import json
import logging

from ..agent.specialists.watch_triage import triage_run
from ..domain.meta_analysis import PicoTable
from ..persistence.database import get_db_session
from ..persistence.models import DEFAULT_USER_ID, _utcnow
from ..persistence.repository import AccountError, AccountRepository, WatchRepository
from ..tools.clinical.search_papers import _fan_out
from .quota import DailyTokenQuotaExceeded, enforce_daily_token_quota
from .spend import AccountBudgetExceeded, enforce_account_budget

logger = logging.getLogger(__name__)


async def run_watch(watch_id: str) -> None:
    """APScheduler job entrypoint. Never raises — records errors on the run."""
    run_id: str | None = None
    try:
        async with get_db_session() as session:
            repo = WatchRepository(session)
            watch = await repo.get_watch(watch_id)
            if watch is None:
                logger.warning("run_watch: watch %s no longer exists; skipping", watch_id)
                return
            if watch.status != "active":
                logger.info("run_watch: watch %s is %s; skipping", watch_id, watch.status)
                return
            run = await repo.add_run(watch_id)
            run_id = run.id

        await _execute_run(watch_id, run_id)
    except Exception as e:
        logger.exception("run_watch: unhandled failure for %s", watch_id)
        if run_id is not None:
            try:
                async with get_db_session() as session:
                    repo = WatchRepository(session)
                    await repo.finish_run(run_id, status="error", error_message=str(e)[:500])
                    await repo.update_watch(
                        watch_id,
                        last_run_at=_utcnow(),
                        last_run_status="error",
                    )
            except Exception:
                logger.exception("run_watch: also failed to record error state for %s", watch_id)


async def _execute_run(watch_id: str, run_id: str) -> None:
    """Inner runner — does the actual work. May raise; caller records the error."""
    # Snapshot the watch's config to plain Python so we don't hold a session
    # across the long-running search + triage calls.
    async with get_db_session() as session:
        repo = WatchRepository(session)
        watch = await repo.get_watch(watch_id)
        if watch is None:
            raise RuntimeError(f"watch {watch_id} disappeared mid-run")
        pico_data = json.loads(watch.pico_json)
        baseline: set[str] = set(json.loads(watch.baseline_pmids_json))
        sources_filter: set[str] = set(json.loads(watch.sources_json))
        triage_threshold = watch.triage_threshold
        search_query = watch.search_query
        watch_name = watch.name

    # Re-execute the saved search.
    raw = await _fan_out(search_query, max_results=50)
    envelope = json.loads(raw)

    if "error" in envelope and not envelope.get("studies"):
        async with get_db_session() as session:
            repo = WatchRepository(session)
            await repo.finish_run(
                run_id,
                status="error",
                error_message=str(envelope["error"])[:500],
            )
            await repo.update_watch(watch_id, last_run_at=_utcnow(), last_run_status="error")
        return

    studies = envelope.get("studies") or []
    if sources_filter:
        studies = [s for s in studies if s.get("source") in sources_filter]

    current_pmids = {s["pmid"] for s in studies if s.get("pmid")}
    new_pmids = current_pmids - baseline
    removed_pmids = baseline - current_pmids  # rare — retractions/withdrawals

    logger.info(
        "run_watch %s: total=%d new=%d removed=%d",
        watch_id,
        len(current_pmids),
        len(new_pmids),
        len(removed_pmids),
    )

    if not new_pmids:
        async with get_db_session() as session:
            repo = WatchRepository(session)
            await repo.finish_run(
                run_id,
                status="no_change",
                total_hits=len(current_pmids),
                removed_pmids_json=json.dumps(sorted(removed_pmids)),
            )
            await repo.update_watch(
                watch_id,
                last_run_at=_utcnow(),
                last_run_status="no_change",
            )
        return

    # Pre-flight the daily token quota + the watch owner's account budget
    # before the Bedrock triage call. On exceeded, record `quota_exceeded`
    # and skip — crucially do NOT bump the baseline, so the next run
    # (after quota reset / budget raise) re-discovers these same PMIDs
    # and triages them then.
    async with get_db_session() as session:
        try:
            await enforce_daily_token_quota(session)
            try:
                owner_account = await AccountRepository(session).resolve_account_for_user(
                    watch.user_id or DEFAULT_USER_ID
                )
            except AccountError:
                owner_account = None  # account layer not seeded (tests) — skip
            await enforce_account_budget(session, account_id=owner_account)
        except (DailyTokenQuotaExceeded, AccountBudgetExceeded) as quota_exc:
            repo = WatchRepository(session)
            await repo.finish_run(
                run_id,
                status="quota_exceeded",
                total_hits=len(current_pmids),
                new_pmids_json=json.dumps(sorted(new_pmids)),
                removed_pmids_json=json.dumps(sorted(removed_pmids)),
                error_message=str(quota_exc)[:500],
            )
            await repo.update_watch(
                watch_id,
                last_run_at=_utcnow(),
                last_run_status="quota_exceeded",
            )
            logger.warning(
                "run_watch %s: skipped triage — daily token quota exceeded "
                "(%s); will retry next scheduled run",
                watch_id,
                quota_exc,
            )
            return

    # Triage the new papers.
    new_paper_records = [
        {
            "pmid": s["pmid"],
            "title": s.get("title", ""),
            "abstract": s.get("abstract", ""),
            "journal": s.get("journal", ""),
            "year": s.get("year"),
        }
        for s in studies
        if s.get("pmid") in new_pmids
    ]
    pico = PicoTable.model_validate(pico_data)
    summary, _usage = await triage_run(
        pico=pico,
        new_papers=new_paper_records,
        triage_threshold=triage_threshold,
    )

    triages_json = json.dumps([t.model_dump() for t in summary.triages])

    async with get_db_session() as session:
        repo = WatchRepository(session)
        await repo.finish_run(
            run_id,
            status="success",
            total_hits=len(current_pmids),
            new_pmids_json=json.dumps(sorted(new_pmids)),
            removed_pmids_json=json.dumps(sorted(removed_pmids)),
            triage_results_json=triages_json,
            significance_summary=summary.significance_summary,
        )
        await repo.update_watch(
            watch_id,
            last_run_at=_utcnow(),
            last_run_status="success",
            baseline_pmids_json=json.dumps(sorted(current_pmids)),
        )
        if summary.notify:
            material_count = sum(1 for t in summary.triages if t.materiality >= triage_threshold)
            await repo.add_notification(
                watch_id=watch_id,
                run_id=run_id,
                title=(f"{material_count} material new paper(s) for {watch_name!r}"),
                summary=summary.significance_summary,
                new_paper_count=len(new_pmids),
            )
            logger.info(
                "run_watch %s: notification raised (%d material of %d new)",
                watch_id,
                material_count,
                len(new_pmids),
            )
