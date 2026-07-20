"""T1 spend quota — Q2: metering + ledger writers.

Pins the pricing-at-write invariants from docs/t1-spend-quota.md: one
ledger row per metered category, priced with the right model table
(main model / vision model / Titan / Tavily flat rate), zero-cost events
writing nothing, and the vision/embedding capture paths feeding them.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.agent.deps import AgentDeps
from research_assistant.agent.specialists._runner import turn_meta
from research_assistant.config.bedrock_pricing import compute_message_cost, lookup
from research_assistant.persistence.models import (
    DEFAULT_ACCOUNT_NAME,
    Account,
    SpendLedger,
)
from research_assistant.persistence.repository import AccountRepository
from research_assistant.services.spend import record_embedding_spend, record_turn_spend
from research_assistant.tools.general.describe_image import _impl

_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_TITAN = "amazon.titan-embed-text-v2:0"


async def _seed_account(db: AsyncSession, name: str = "Spend program") -> Account:
    return await AccountRepository(db).create_account(name=name)


# ── pricing table ───────────────────────────────────────────────────────


def test_titan_embedding_pricing_resolves() -> None:
    pricing = lookup(_TITAN)
    assert pricing.family == "embedding"
    assert pricing.input_per_1k_usd == pytest.approx(0.00002)
    assert pricing.output_per_1k_usd == 0.0


def test_titan_cost_is_input_only() -> None:
    usd = compute_message_cost(input_tokens=100_000, output_tokens=0, model_id=_TITAN)
    assert usd == pytest.approx(0.002)


# ── record_turn_spend ───────────────────────────────────────────────────


async def test_turn_spend_writes_priced_turn_row(db_session: AsyncSession) -> None:
    account = await _seed_account(db_session)
    rows = await record_turn_spend(
        db_session,
        account_id=account.id,
        trial_id=None,
        thread_id="t-1",
        message_id="m-1",
        usage={"input_tokens": 10_000, "output_tokens": 2_000, "model_id": _HAIKU},
    )
    assert [r.category for r in rows] == ["turn"]
    assert rows[0].quantity == 12_000
    # Haiku 4.5: 10k × 0.0008/1k + 2k × 0.004/1k
    assert rows[0].usd == pytest.approx(0.008 + 0.008)
    assert rows[0].model_id == _HAIKU


async def test_turn_spend_prices_searches_and_vision(db_session: AsyncSession) -> None:
    account = await _seed_account(db_session)
    rows = await record_turn_spend(
        db_session,
        account_id=account.id,
        trial_id="trial-9",
        thread_id="t-2",
        message_id="m-2",
        usage={"input_tokens": 1_000, "output_tokens": 100, "model_id": _HAIKU},
        tool_usage={"web_search": 3, "calculator": 5},
        vision_usage={"input_tokens": 2_000, "output_tokens": 500},
    )
    by_cat = {r.category: r for r in rows}
    assert set(by_cat) == {"turn", "search", "vision"}
    assert by_cat["search"].quantity == 3
    assert by_cat["search"].usd == pytest.approx(3 * 0.008)
    assert by_cat["search"].model_id is None
    # Vision priced with the vision model (Sonnet), not the turn model.
    assert by_cat["vision"].quantity == 2_500
    assert by_cat["vision"].usd == pytest.approx(
        compute_message_cost(input_tokens=2_000, output_tokens=500, model_id="sonnet-4-6")
    )
    # Trial attribution rides every row.
    assert all(r.trial_id == "trial-9" for r in rows)


async def test_turn_spend_skips_zero_usage(db_session: AsyncSession) -> None:
    """Errored turns carry placeholder zeros — nothing to account."""
    account = await _seed_account(db_session)
    rows = await record_turn_spend(
        db_session,
        account_id=account.id,
        trial_id=None,
        thread_id="t-3",
        message_id="m-3",
        usage={"input_tokens": 0, "output_tokens": 0},
        tool_usage={},
    )
    assert rows == []
    assert (await db_session.scalars(select(SpendLedger))).all() == []


async def test_turn_spend_without_account_writes_nothing(
    db_session: AsyncSession,
) -> None:
    rows = await record_turn_spend(
        db_session,
        account_id=None,
        trial_id=None,
        thread_id="t-4",
        message_id="m-4",
        usage={"input_tokens": 5_000, "output_tokens": 500, "model_id": _HAIKU},
    )
    assert rows == []


# ── record_embedding_spend ──────────────────────────────────────────────


async def test_embedding_spend_bills_default_account(db_session: AsyncSession) -> None:
    default = Account(name=DEFAULT_ACCOUNT_NAME, status="active")
    db_session.add(default)
    await db_session.flush()

    row = await record_embedding_spend(db_session, tokens=50_000, model_id=_TITAN)
    assert row is not None
    assert row.account_id == default.id
    assert row.category == "embedding"
    assert row.quantity == 50_000
    assert row.usd == pytest.approx(0.001)


async def test_embedding_spend_without_default_account_is_none(
    db_session: AsyncSession,
) -> None:
    assert await record_embedding_spend(db_session, tokens=1_000, model_id=_TITAN) is None


async def test_embedding_spend_zero_tokens_is_none(db_session: AsyncSession) -> None:
    assert await record_embedding_spend(db_session, tokens=0, model_id=_TITAN) is None


# ── vision capture → turn_meta ──────────────────────────────────────────


def _fake_result(input_tokens: int = 100, output_tokens: int = 10) -> MagicMock:
    result = MagicMock()
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.requests = 1
    usage.tool_calls = 0
    result.usage.return_value = usage
    return result


def test_turn_meta_surfaces_vision_usage() -> None:
    deps = AgentDeps(vision_input_tokens=1_500, vision_output_tokens=300)
    meta = turn_meta(_fake_result(), deps)
    assert meta["vision_usage"] == {"input_tokens": 1_500, "output_tokens": 300}


def test_turn_meta_omits_vision_usage_when_unused() -> None:
    meta = turn_meta(_fake_result(), AgentDeps())
    assert "vision_usage" not in meta


async def test_describe_image_accumulates_vision_tokens() -> None:
    deps = AgentDeps()
    response = {
        "output": {"message": {"content": [{"text": "A forest plot."}]}},
        "usage": {"inputTokens": 1_234, "outputTokens": 56},
    }
    client = MagicMock()
    client.converse.return_value = response
    with patch("boto3.client", return_value=client):
        text = await _impl(b"\x89PNG fake", "image/png", "what is this?", deps=deps)
    assert text == "A forest plot."
    assert deps.vision_input_tokens == 1_234
    assert deps.vision_output_tokens == 56
