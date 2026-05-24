"""Unit tests for services.quota daily token-quota enforcement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.config import Settings
from research_assistant.persistence.repository import ThreadRepository
from research_assistant.services.quota import (
    DailyTokenQuotaExceeded,
    DailyTokenTotals,
    build_quota_payload,
    enforce_daily_token_quota,
    get_today_token_totals,
    today_utc_start,
)


async def _seed_done_event(
    session: AsyncSession,
    *,
    input_tokens: int,
    output_tokens: int,
    created_at: datetime | None = None,
) -> None:
    """Seed one (thread → message → done stream event) tuple with usage."""
    repo = ThreadRepository(session)
    thread = await repo.create_thread(title="t")
    msg = await repo.add_message(
        thread_id=thread.id, role="assistant", final_answer="{}"
    )
    evt = await repo.add_stream_event(
        message_id=msg.id,
        event_type="done",
        data={"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}},
        sequence_num=0,
    )
    if created_at is not None:
        evt.created_at = created_at
        await session.flush()


# ── get_today_token_totals ────────────────────────────────────────────────


async def test_totals_empty(db_session: AsyncSession) -> None:
    totals = await get_today_token_totals(db_session)
    assert totals.input_tokens == 0
    assert totals.output_tokens == 0
    assert totals.day_start == today_utc_start()


async def test_totals_sum_multiple_done_events(db_session: AsyncSession) -> None:
    await _seed_done_event(db_session, input_tokens=1000, output_tokens=200)
    await _seed_done_event(db_session, input_tokens=2500, output_tokens=400)
    await _seed_done_event(db_session, input_tokens=750, output_tokens=150)

    totals = await get_today_token_totals(db_session)
    assert totals.input_tokens == 4250
    assert totals.output_tokens == 750


async def test_totals_exclude_yesterday(db_session: AsyncSession) -> None:
    yesterday = today_utc_start() - timedelta(hours=2)  # before today's window
    await _seed_done_event(
        db_session, input_tokens=99_999, output_tokens=99_999, created_at=yesterday
    )
    await _seed_done_event(db_session, input_tokens=100, output_tokens=50)

    totals = await get_today_token_totals(db_session)
    assert totals.input_tokens == 100
    assert totals.output_tokens == 50


async def test_totals_skip_malformed_events(db_session: AsyncSession) -> None:
    """A row whose `data` JSON is missing `usage` is silently skipped."""
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread(title="t")
    msg = await repo.add_message(thread_id=thread.id, role="assistant", final_answer="{}")
    await repo.add_stream_event(
        message_id=msg.id,
        event_type="done",
        data={"no_usage_here": True},  # missing usage key
        sequence_num=0,
    )
    await _seed_done_event(db_session, input_tokens=500, output_tokens=100)

    totals = await get_today_token_totals(db_session)
    assert totals.input_tokens == 500
    assert totals.output_tokens == 100


# ── enforce_daily_token_quota ─────────────────────────────────────────────


async def test_enforce_passes_under_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_INPUT_TOKENS_PER_DAY", "10000")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS_PER_DAY", "2000")
    await _seed_done_event(db_session, input_tokens=1000, output_tokens=200)

    totals = await enforce_daily_token_quota(db_session)
    assert totals.input_tokens == 1000
    assert totals.output_tokens == 200


async def test_enforce_raises_on_input_over(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_INPUT_TOKENS_PER_DAY", "5000")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS_PER_DAY", "10000")
    await _seed_done_event(db_session, input_tokens=5000, output_tokens=100)

    with pytest.raises(DailyTokenQuotaExceeded) as exc_info:
        await enforce_daily_token_quota(db_session)
    assert exc_info.value.dimension == "input"
    assert exc_info.value.limit == 5000
    assert exc_info.value.current == 5000


async def test_enforce_raises_on_output_over(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_INPUT_TOKENS_PER_DAY", "1000000")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS_PER_DAY", "500")
    await _seed_done_event(db_session, input_tokens=100, output_tokens=600)

    with pytest.raises(DailyTokenQuotaExceeded) as exc_info:
        await enforce_daily_token_quota(db_session)
    assert exc_info.value.dimension == "output"
    assert exc_info.value.limit == 500
    assert exc_info.value.current == 600


async def test_enforce_disabled_with_zero(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Limits of 0 are an escape-hatch for ops — no enforcement."""
    monkeypatch.setenv("MAX_INPUT_TOKENS_PER_DAY", "0")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS_PER_DAY", "0")
    await _seed_done_event(db_session, input_tokens=10_000_000, output_tokens=10_000_000)

    totals = await enforce_daily_token_quota(db_session)
    assert totals.input_tokens == 10_000_000


# ── DailyTokenQuotaExceeded behaviour ─────────────────────────────────────


def test_hours_until_reset_is_nonneg() -> None:
    """The reset countdown is always >= 0 (even at the exact UTC boundary)."""
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    exc = DailyTokenQuotaExceeded(
        dimension="input", limit=1, current=1, day_start=day_start
    )
    h = exc.hours_until_reset()
    assert 0.0 <= h <= 24.0


# ── build_quota_payload ──────────────────────────────────────────────────


def _settings(in_limit: int, out_limit: int) -> Settings:
    return Settings(
        max_input_tokens_per_day=in_limit,
        max_output_tokens_per_day=out_limit,
    )


def test_build_quota_payload_under_limits() -> None:
    totals = DailyTokenTotals(
        input_tokens=1000, output_tokens=200, day_start=today_utc_start()
    )
    payload = build_quota_payload(totals, _settings(10_000, 2_000))

    assert payload["input_tokens"] == {
        "used": 1000, "limit": 10_000, "remaining": 9000, "percent": 10.0,
    }
    assert payload["output_tokens"] == {
        "used": 200, "limit": 2_000, "remaining": 1800, "percent": 10.0,
    }
    assert payload["enforcement_enabled"] is True
    assert 0.0 <= payload["hours_until_reset"] <= 24.0


def test_build_quota_payload_at_limit_clamps_to_100() -> None:
    """percent is capped at 100 even when used > limit."""
    totals = DailyTokenTotals(
        input_tokens=15_000, output_tokens=0, day_start=today_utc_start()
    )
    payload = build_quota_payload(totals, _settings(10_000, 0))
    assert payload["input_tokens"]["percent"] == 100.0
    assert payload["input_tokens"]["remaining"] == 0


def test_build_quota_payload_disabled_axis() -> None:
    """limit=0 → remaining None, percent 0, enforcement_enabled reflects the OTHER axis."""
    totals = DailyTokenTotals(
        input_tokens=9_999_999, output_tokens=5, day_start=today_utc_start()
    )
    payload = build_quota_payload(totals, _settings(0, 100))
    assert payload["input_tokens"]["remaining"] is None
    assert payload["input_tokens"]["percent"] == 0.0
    assert payload["output_tokens"]["remaining"] == 95
    assert payload["enforcement_enabled"] is True  # output limit > 0


def test_build_quota_payload_fully_disabled() -> None:
    totals = DailyTokenTotals(
        input_tokens=1, output_tokens=1, day_start=today_utc_start()
    )
    payload = build_quota_payload(totals, _settings(0, 0))
    assert payload["enforcement_enabled"] is False
