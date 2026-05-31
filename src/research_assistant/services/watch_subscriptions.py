"""Group-level living-review subscription orchestrator (P2 #4).

Wraps `SubscriptionRepository` with the policy that the API + a future
watch_runner hook want:

  1. Vote → re-tally → if quorum clears AND no group notification has
     fired yet for that (subscription, run), fan out one Notification
     per member.
  2. On a freshly-finished WatchRun, prepare a tally row for every
     active subscription attached to that run's watch — used by the UI
     to surface "5 runs awaiting your vote".

No new persistence here. The repo + tally rules carry all the state;
the service composes them.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..persistence.models import LiteratureWatchSubscription, WatchRun
from ..persistence.repository import (
    SubscriptionError,
    SubscriptionRepository,
)

logger = logging.getLogger(__name__)


async def record_vote_and_fanout(
    session: AsyncSession,
    *,
    subscription_id: str,
    run_id: str,
    voter_user_id: str,
    vote: str,
    rationale: str = "",
) -> dict[str, object]:
    """Record a vote and, if it tips quorum, fan out group notifications.

    Returns the post-vote tally dict. When `quorum_cleared` is True the
    return also carries `notifications_created` = N (the number of
    fresh Notification rows added — 0 if every member already had one,
    matching the idempotent fan-out posture).
    """
    repo = SubscriptionRepository(session)
    sub = await repo.get_subscription(subscription_id)
    if sub is None:
        raise SubscriptionError(f"Subscription {subscription_id!r} not found.")
    if sub.status != "active":
        raise SubscriptionError(
            f"Subscription is {sub.status!r}; voting only allowed on active subscriptions."
        )
    await repo.record_vote(
        subscription_id=subscription_id,
        run_id=run_id,
        voter_user_id=voter_user_id,
        vote=vote,
        rationale=rationale,
    )
    tally = await repo.tally(subscription_id=subscription_id, run_id=run_id)
    notifications_created = 0
    if tally["quorum_cleared"]:
        title, summary, paper_count = await _format_group_alert(session, sub, run_id, tally)
        created = await repo.fanout_group_notification(
            subscription_id=subscription_id,
            run_id=run_id,
            title=title,
            summary=summary,
            new_paper_count=paper_count,
        )
        notifications_created = len(created)
        if notifications_created:
            logger.info(
                "group quorum cleared: subscription=%s run=%s yes=%d "
                "threshold=%d → fanned out %d notification(s)",
                subscription_id,
                run_id,
                tally["yes"],
                tally["threshold"],
                notifications_created,
            )
    tally["notifications_created"] = notifications_created
    return tally


async def _format_group_alert(
    session: AsyncSession,
    sub: LiteratureWatchSubscription,
    run_id: str,
    tally: dict[str, object],
) -> tuple[str, str, int]:
    """Compose the title + summary + paper-count for a group alert.

    Pulled out so future i18n / templating doesn't bleed into the
    record_vote path."""
    run = await session.get(WatchRun, run_id)
    paper_count = run.total_hits if run is not None else 0
    yes = tally["yes"]
    threshold = tally["threshold"]
    n_voters = tally["n_voters"]
    title = f"[Group quorum] {sub.name}"
    summary = (
        f"The group voted to flag run {run_id} as practice-changing "
        f"({yes}/{n_voters} yes-votes, threshold {threshold}). "
        f"{paper_count} new paper(s) in the run."
    )
    return title, summary, paper_count


__all__ = [
    "record_vote_and_fanout",
]
