"""Cost rollup service — turn token spend into USD by user / workflow / model (P2 #6).

Aggregates `done` stream events (which already carry per-turn `usage`)
through the Bedrock pricing table. No new persistence — all rollups are
read-side aggregations over the existing event stream.

Used by:
  • GET /api/portfolio/costs       — per-user dashboard
  • GET /api/portfolio/org/costs   — admin-only institutional rollup
  • Per-thread cost on the existing /api/portfolio/threads endpoint
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.bedrock_pricing import compute_message_cost, lookup
from ..persistence.models import Message, StreamEvent, Thread

logger = logging.getLogger(__name__)


@dataclass
class CostRollup:
    """Per-user / per-org / per-thread cost summary."""

    usd_total: float = 0.0
    usd_this_month: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    by_workflow: dict[str, float] = field(default_factory=dict)
    by_model_family: dict[str, float] = field(default_factory=dict)
    n_turns: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "usd_total": round(self.usd_total, 6),
            "usd_this_month": round(self.usd_this_month, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "by_workflow": {k: round(v, 6) for k, v in self.by_workflow.items()},
            "by_model_family": {
                k: round(v, 6) for k, v in self.by_model_family.items()
            },
            "n_turns": self.n_turns,
        }


def _month_start(now: datetime | None = None) -> datetime:
    n = now or datetime.now(UTC)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _parse_done_event(evt: StreamEvent) -> tuple[int, int, str | None, str | None]:
    """Return (input_tokens, output_tokens, workflow, model_id) for a
    done event. Missing fields default to None / 0."""
    try:
        data = json.loads(evt.data)
    except (json.JSONDecodeError, TypeError):
        return (0, 0, None, None)
    if not isinstance(data, dict):
        return (0, 0, None, None)
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return (0, 0, None, None)
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    workflow = data.get("workflow")
    # model_id may live under usage.model_id (new) or be absent (legacy).
    model_id = (
        usage.get("model_id")
        or usage.get("model")
        or data.get("model_id")
    )
    return (
        input_tokens,
        output_tokens,
        str(workflow) if workflow else None,
        str(model_id) if model_id else None,
    )


async def rollup_for_user(
    session: AsyncSession,
    *,
    user_id: str,
    now: datetime | None = None,
) -> CostRollup:
    """Aggregate cost across all of a user's threads."""
    month_start = _month_start(now)
    # Pull done events scoped to the user's threads.
    user_thread_stmt = select(Thread.id).where(Thread.user_id == user_id)
    done_stmt = (
        select(StreamEvent)
        .join(Message, StreamEvent.message_id == Message.id)
        .where(
            StreamEvent.event_type == "done",
            Message.thread_id.in_(user_thread_stmt),
        )
    )
    rollup = CostRollup()
    by_workflow: dict[str, float] = {}
    by_family: dict[str, float] = {}
    for evt in (await session.scalars(done_stmt)).all():
        input_t, output_t, workflow, model_id = _parse_done_event(evt)
        if input_t == 0 and output_t == 0:
            continue
        pricing = lookup(model_id)
        cost = compute_message_cost(
            input_tokens=input_t,
            output_tokens=output_t,
            model_id=model_id,
        )
        rollup.usd_total += cost
        rollup.input_tokens += input_t
        rollup.output_tokens += output_t
        rollup.n_turns += 1
        if workflow:
            by_workflow[workflow] = by_workflow.get(workflow, 0.0) + cost
        by_family[pricing.family] = by_family.get(pricing.family, 0.0) + cost
        if evt.created_at >= month_start:
            rollup.usd_this_month += cost
    rollup.by_workflow = by_workflow
    rollup.by_model_family = by_family
    return rollup


async def rollup_for_thread(
    session: AsyncSession,
    *,
    thread_id: str,
) -> float:
    """Return cumulative USD for one thread. Used to decorate
    /api/portfolio/threads rows."""
    done_stmt = (
        select(StreamEvent)
        .join(Message, StreamEvent.message_id == Message.id)
        .where(
            StreamEvent.event_type == "done",
            Message.thread_id == thread_id,
        )
    )
    total = 0.0
    for evt in (await session.scalars(done_stmt)).all():
        input_t, output_t, _, model_id = _parse_done_event(evt)
        if input_t == 0 and output_t == 0:
            continue
        total += compute_message_cost(
            input_tokens=input_t,
            output_tokens=output_t,
            model_id=model_id,
        )
    return total


async def rollup_for_org(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> tuple[CostRollup, dict[str, CostRollup]]:
    """Aggregate cost across every user. Returns (org_totals,
    per_user_rollups). Caller is responsible for gating on
    portfolio.read_org."""
    month_start = _month_start(now)
    done_stmt = (
        select(StreamEvent, Thread.user_id)
        .join(Message, StreamEvent.message_id == Message.id)
        .join(Thread, Message.thread_id == Thread.id)
        .where(StreamEvent.event_type == "done")
    )
    org = CostRollup()
    per_user: dict[str, CostRollup] = {}
    org_by_workflow: dict[str, float] = {}
    org_by_family: dict[str, float] = {}
    for evt, user_id in (await session.execute(done_stmt)).all():
        input_t, output_t, workflow, model_id = _parse_done_event(evt)
        if input_t == 0 and output_t == 0:
            continue
        pricing = lookup(model_id)
        cost = compute_message_cost(
            input_tokens=input_t,
            output_tokens=output_t,
            model_id=model_id,
        )
        user_rollup = per_user.setdefault(user_id or "(unknown)", CostRollup())
        user_rollup.usd_total += cost
        user_rollup.input_tokens += input_t
        user_rollup.output_tokens += output_t
        user_rollup.n_turns += 1
        if evt.created_at >= month_start:
            user_rollup.usd_this_month += cost
        if workflow:
            user_rollup.by_workflow[workflow] = (
                user_rollup.by_workflow.get(workflow, 0.0) + cost
            )
        user_rollup.by_model_family[pricing.family] = (
            user_rollup.by_model_family.get(pricing.family, 0.0) + cost
        )
        org.usd_total += cost
        org.input_tokens += input_t
        org.output_tokens += output_t
        org.n_turns += 1
        if evt.created_at >= month_start:
            org.usd_this_month += cost
        if workflow:
            org_by_workflow[workflow] = org_by_workflow.get(workflow, 0.0) + cost
        org_by_family[pricing.family] = (
            org_by_family.get(pricing.family, 0.0) + cost
        )
    org.by_workflow = org_by_workflow
    org.by_model_family = org_by_family
    return org, per_user


__all__ = [
    "CostRollup",
    "rollup_for_org",
    "rollup_for_thread",
    "rollup_for_user",
]
