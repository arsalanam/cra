"""Daily token-quota enforcement.

Reads from the existing `done` stream events (`StreamEvent.event_type ==
"done"`, JSON `data.usage.{input,output}_tokens`) rather than maintaining
a parallel counter. The existing monthly-usage endpoint in `web/threads.py`
uses the same source — keeping both views off one underlying ledger means
they can never disagree.

Pre-flight (not in-flight): the dispatcher endpoint and watch runner call
`enforce_daily_token_quota` BEFORE making the next Bedrock call. A single
turn that pushes the day over the line still completes (per-turn limits
in `UsageLimits` cap its damage); the NEXT turn is the one that gets
refused. This trades exact accounting for simple code — adequate for a
single-tenant deployment.

When auth lands, the totals query gets a `user_id` partition and limits
become per-user. The settings names already imply per-tenant.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..persistence.repository import ThreadRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyTokenTotals:
    """Today's (UTC) cumulative token spend across all turns + watches."""

    input_tokens: int
    output_tokens: int
    day_start: datetime  # UTC midnight that the totals are scoped to


class DailyTokenQuotaExceeded(Exception):
    """Raised pre-flight when today's spend has already hit a configured cap.

    Carries the offending dimension and the totals so the caller can build
    a useful user-facing message (which axis tripped, how much further it
    would push the budget, when the quota resets).
    """

    def __init__(
        self,
        *,
        dimension: str,
        limit: int,
        current: int,
        day_start: datetime,
    ) -> None:
        super().__init__(f"Daily {dimension}-token quota exceeded: {current} >= {limit}")
        self.dimension = dimension
        self.limit = limit
        self.current = current
        self.day_start = day_start

    def hours_until_reset(self) -> float:
        """Hours until the UTC daily counter resets at 00:00."""
        now = datetime.now(UTC)
        reset = self.day_start + timedelta(days=1)
        return max(0.0, (reset - now).total_seconds() / 3600)


def today_utc_start() -> datetime:
    """UTC midnight for the current day."""
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def get_today_token_totals(
    session: AsyncSession,
    *,
    user_id: str | None = None,
) -> DailyTokenTotals:
    """Sum input + output tokens from today's `done` events.

    `user_id` scopes to a single user's threads (RBAC-3). None ⇒ the
    process-wide total, which is what enforcement reads — daily caps stay
    global by design (the design doc explicitly defers role/tier-based
    ceilings to a later refinement).
    """
    day_start = today_utc_start()
    repo = ThreadRepository(session)
    events = await repo.get_done_events_since(day_start, user_id=user_id)

    total_in = 0
    total_out = 0
    for evt in events:
        try:
            data = json.loads(evt.data)
        except (json.JSONDecodeError, TypeError):
            # Malformed event — already logged elsewhere; skip silently here.
            continue
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict):
            continue
        total_in += int(usage.get("input_tokens") or 0)
        total_out += int(usage.get("output_tokens") or 0)

    return DailyTokenTotals(
        input_tokens=total_in,
        output_tokens=total_out,
        day_start=day_start,
    )


def build_quota_payload(totals: DailyTokenTotals, settings: Settings) -> dict[str, Any]:
    """Frontend-friendly view of today's spend vs configured caps.

    Shape is shared by `GET /api/threads/usage/today` and the per-turn
    `TurnResponse.quota` field so the UI can render one component for both.

    When a limit is 0 (enforcement disabled for that axis), `remaining` is
    null and `percent` is 0.
    """

    def _axis(used: int, limit: int) -> dict[str, Any]:
        if limit <= 0:
            return {"used": used, "limit": 0, "remaining": None, "percent": 0.0}
        remaining = max(0, limit - used)
        percent = min(100.0, (used / limit) * 100.0)
        return {
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "percent": round(percent, 1),
        }

    now = datetime.now(UTC)
    reset = totals.day_start + timedelta(days=1)
    hours_until_reset = max(0.0, (reset - now).total_seconds() / 3600)

    return {
        "input_tokens": _axis(totals.input_tokens, settings.max_input_tokens_per_day),
        "output_tokens": _axis(totals.output_tokens, settings.max_output_tokens_per_day),
        "day_start_utc": totals.day_start.isoformat(),
        "hours_until_reset": round(hours_until_reset, 2),
        "enforcement_enabled": (
            settings.max_input_tokens_per_day > 0 or settings.max_output_tokens_per_day > 0
        ),
    }


async def enforce_daily_token_quota(session: AsyncSession) -> DailyTokenTotals:
    """Raise `DailyTokenQuotaExceeded` if today's totals are at/over a limit.

    Returns the current totals on success so callers that want to log /
    surface remaining headroom don't have to query twice. Limits of 0 are
    treated as "disabled" — useful escape hatch for ops.
    """
    settings = get_settings()
    totals = await get_today_token_totals(session)

    in_limit = settings.max_input_tokens_per_day
    if in_limit > 0 and totals.input_tokens >= in_limit:
        logger.warning(
            "Daily input-token quota exceeded: %d >= %d",
            totals.input_tokens,
            in_limit,
        )
        raise DailyTokenQuotaExceeded(
            dimension="input",
            limit=in_limit,
            current=totals.input_tokens,
            day_start=totals.day_start,
        )

    out_limit = settings.max_output_tokens_per_day
    if out_limit > 0 and totals.output_tokens >= out_limit:
        logger.warning(
            "Daily output-token quota exceeded: %d >= %d",
            totals.output_tokens,
            out_limit,
        )
        raise DailyTokenQuotaExceeded(
            dimension="output",
            limit=out_limit,
            current=totals.output_tokens,
            day_start=totals.day_start,
        )

    return totals
