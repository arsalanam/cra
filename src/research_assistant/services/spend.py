"""T1 spend quota — spend-ledger writers (docs/t1-spend-quota.md).

Append-only USD accounting. Each metered event becomes one `spend_ledger`
row, priced at write time via `config/bedrock_pricing.py` so later pricing
changes never rewrite history. Zero-cost events (errored turns with
placeholder usage, empty tool counts) write nothing — a SUM over the
ledger is unaffected either way, and skipping keeps the table meaningful.

Writers never raise: the ledger is accounting, and a pricing hiccup must
not fail a turn that already succeeded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..config.bedrock_pricing import compute_message_cost
from ..persistence.models import DEFAULT_ACCOUNT_NAME, Account, SpendLedger

logger = logging.getLogger(__name__)


# ── Budget enforcement (D3: hard stop) ──────────────────────────────────


@dataclass(frozen=True)
class BudgetStatus:
    """Snapshot of one account's budget window."""

    account_id: str
    account_name: str
    limit_usd: float  # 0 = budget disabled
    spent_usd: float
    start_at: datetime | None
    warn_percent: int

    @property
    def enabled(self) -> bool:
        return self.limit_usd > 0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd) if self.enabled else 0.0

    @property
    def percent_used(self) -> float:
        if not self.enabled:
            return 0.0
        return min(999.0, round(self.spent_usd / self.limit_usd * 100.0, 1))

    @property
    def warning(self) -> bool:
        return self.enabled and self.percent_used >= self.warn_percent


class AccountBudgetExceeded(Exception):
    """The account's trial budget is spent — hard 429 stop (D3)."""

    def __init__(self, status: BudgetStatus) -> None:
        self.status = status
        super().__init__(
            f"Account {status.account_name!r} budget exhausted: "
            f"${status.spent_usd:.2f} of ${status.limit_usd:.2f}"
        )


async def get_account_spend(
    session: AsyncSession, *, account_id: str, since: datetime | None = None
) -> float:
    """Cumulative ledger USD for one account (optionally windowed)."""
    stmt = select(func.coalesce(func.sum(SpendLedger.usd), 0.0)).where(
        SpendLedger.account_id == account_id
    )
    if since is not None:
        stmt = stmt.where(SpendLedger.created_at >= since)
    return float(await session.scalar(stmt) or 0.0)


async def get_budget_status(session: AsyncSession, *, account_id: str) -> BudgetStatus | None:
    """Budget snapshot for an account; None if the account doesn't exist."""
    account = await session.get(Account, account_id)
    if account is None:
        return None
    since = account.budget_start_at or account.created_at
    spent = await get_account_spend(session, account_id=account_id, since=since)
    return BudgetStatus(
        account_id=account.id,
        account_name=account.name,
        limit_usd=float(account.budget_usd or 0.0),
        spent_usd=spent,
        start_at=since,
        warn_percent=int(account.budget_warn_percent or 80),
    )


async def enforce_account_budget(
    session: AsyncSession, *, account_id: str | None
) -> BudgetStatus | None:
    """Pre-flight hard stop: raise AccountBudgetExceeded when the account's
    cumulative spend since its budget start date has reached the cap.

    Mirrors `enforce_daily_token_quota`'s posture — checked before the
    turn runs, so the final turn may overshoot by one turn's cost (see
    docs/t1-spend-quota.md). None / unknown accounts and disabled budgets
    (limit 0) pass through.
    """
    if account_id is None:
        return None
    status = await get_budget_status(session, account_id=account_id)
    if status is None or not status.enabled:
        return status
    if status.spent_usd >= status.limit_usd:
        raise AccountBudgetExceeded(status)
    return status


def build_budget_payload(status: BudgetStatus | None) -> dict[str, Any] | None:
    """JSON-friendly budget axis for TurnResponse.quota / the budgets UI."""
    if status is None:
        return None
    return {
        "account_id": status.account_id,
        "account_name": status.account_name,
        "enabled": status.enabled,
        "limit_usd": round(status.limit_usd, 2),
        "spent_usd": round(status.spent_usd, 6),
        "remaining_usd": round(status.remaining_usd, 6),
        "percent_used": status.percent_used,
        "warn_percent": status.warn_percent,
        "warning": status.warning,
        "start_at": status.start_at.isoformat() if status.start_at else None,
    }


def _tokens(usage: dict[str, Any], key: str) -> int:
    try:
        return int(usage.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


async def record_turn_spend(
    session: AsyncSession,
    *,
    account_id: str | None,
    trial_id: str | None,
    thread_id: str,
    message_id: str,
    usage: dict[str, Any],
    tool_usage: dict[str, int] | None = None,
    vision_usage: dict[str, Any] | None = None,
) -> list[SpendLedger]:
    """Write the ledger rows for one completed turn.

    Up to three rows: `turn` (main-model tokens), `search` (Tavily
    web_search calls), `vision` (describe_image converse tokens). Rows are
    added to *session* but not committed — the caller owns the transaction
    (web/dispatch writes them alongside the done event).
    """
    if account_id is None:
        # Threads are attributed at creation and backfilled at startup, so
        # this only happens in the SET NULL window after an account delete.
        logger.debug("Spend ledger skipped — thread %s has no account", thread_id)
        return []

    settings = get_settings()
    rows: list[SpendLedger] = []
    try:
        input_tokens = _tokens(usage, "input_tokens")
        output_tokens = _tokens(usage, "output_tokens")
        if input_tokens or output_tokens:
            model_id = str(usage.get("model_id") or settings.bedrock_model_id)
            rows.append(
                SpendLedger(
                    account_id=account_id,
                    trial_id=trial_id,
                    thread_id=thread_id,
                    message_id=message_id,
                    category="turn",
                    quantity=input_tokens + output_tokens,
                    model_id=model_id,
                    usd=compute_message_cost(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        model_id=model_id,
                    ),
                )
            )

        searches = int((tool_usage or {}).get("web_search", 0) or 0)
        if searches:
            rows.append(
                SpendLedger(
                    account_id=account_id,
                    trial_id=trial_id,
                    thread_id=thread_id,
                    message_id=message_id,
                    category="search",
                    quantity=searches,
                    model_id=None,
                    usd=searches * settings.tavily_price_per_search_usd,
                )
            )

        if vision_usage:
            v_in = _tokens(vision_usage, "input_tokens")
            v_out = _tokens(vision_usage, "output_tokens")
            if v_in or v_out:
                rows.append(
                    SpendLedger(
                        account_id=account_id,
                        trial_id=trial_id,
                        thread_id=thread_id,
                        message_id=message_id,
                        category="vision",
                        quantity=v_in + v_out,
                        model_id=settings.vision_model_id,
                        usd=compute_message_cost(
                            input_tokens=v_in,
                            output_tokens=v_out,
                            model_id=settings.vision_model_id,
                        ),
                    )
                )

        session.add_all(rows)
        await session.flush()
    except Exception:  # pragma: no cover - defensive; accounting must not fail turns
        logger.warning("Spend-ledger write failed for thread %s", thread_id, exc_info=True)
        return []
    return rows


async def record_embedding_spend(
    session: AsyncSession,
    *,
    tokens: int,
    model_id: str,
) -> SpendLedger | None:
    """Write one `embedding` ledger row for a drain batch.

    The publication cache is shared infrastructure (passages carry no
    owner), so background embedding bills the Default Account as platform
    overhead — see the attribution note in docs/t1-spend-quota.md. Added
    to *session*, not committed; caller owns the transaction.
    """
    if tokens <= 0:
        return None
    try:
        default_account = await session.scalar(
            select(Account).where(Account.name == DEFAULT_ACCOUNT_NAME)
        )
        if default_account is None:
            logger.debug("Spend ledger skipped — no default account seeded")
            return None
        row = SpendLedger(
            account_id=default_account.id,
            category="embedding",
            quantity=tokens,
            model_id=model_id,
            usd=compute_message_cost(input_tokens=tokens, output_tokens=0, model_id=model_id),
        )
        session.add(row)
        await session.flush()
    except Exception:  # pragma: no cover - defensive
        logger.warning("Embedding spend-ledger write failed", exc_info=True)
        return None
    return row
