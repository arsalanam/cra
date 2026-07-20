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
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..config.bedrock_pricing import compute_message_cost
from ..persistence.models import DEFAULT_ACCOUNT_NAME, Account, SpendLedger

logger = logging.getLogger(__name__)


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
