"""Bedrock pricing + cost rollup tests (P2 #6 budget rollup)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from research_assistant.config.bedrock_pricing import (
    ModelPricing,
    compute_message_cost,
    known_families,
    lookup,
)

# ── Pricing table coverage ─────────────────────────────────────────────


def test_known_families_covers_three_canonical_tiers() -> None:
    """The pricing table must carry the three Claude 4.x families the
    platform actually uses."""
    families = set(known_families())
    assert families >= {"haiku", "sonnet", "opus"}


def test_lookup_short_form_haiku_4_5() -> None:
    p = lookup("claude-haiku-4-5")
    assert p.family == "haiku"
    assert p.input_per_1k_usd > 0
    assert p.output_per_1k_usd > p.input_per_1k_usd  # output always pricier


def test_lookup_full_inference_profile_id() -> None:
    """Bedrock's long-form `us.anthropic.claude-haiku-4-5-20251001-v1:0`
    must resolve to the same pricing as the short alias."""
    short = lookup("claude-haiku-4-5")
    long_ = lookup("us.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert short.input_per_1k_usd == long_.input_per_1k_usd
    assert short.output_per_1k_usd == long_.output_per_1k_usd


@pytest.mark.parametrize(
    "model_id, expected_family",
    [
        ("claude-haiku-4-5", "haiku"),
        ("claude-sonnet-4-6", "sonnet"),
        ("claude-opus-4-7", "opus"),
        ("us.anthropic.claude-sonnet-4-6-20251220-v1:0", "sonnet"),
        ("anthropic.claude-3-5-sonnet-20240620-v1:0", "sonnet"),
    ],
)
def test_lookup_resolves_canonical_models(model_id: str, expected_family: str) -> None:
    p = lookup(model_id)
    assert p.family == expected_family


def test_lookup_unknown_model_falls_back_to_sonnet() -> None:
    """Unknown model ids should fall back to a conservative Sonnet
    pricing — we'd rather overestimate than under-attribute spend."""
    fallback = lookup("does-not-exist-yet")
    sonnet = lookup("claude-sonnet-4-6")
    assert fallback.input_per_1k_usd == sonnet.input_per_1k_usd
    assert fallback.output_per_1k_usd == sonnet.output_per_1k_usd


def test_lookup_empty_string_falls_back() -> None:
    assert lookup("").family == "sonnet"
    assert lookup(None).family == "sonnet"


# ── compute_message_cost math ─────────────────────────────────────────


def test_compute_message_cost_zero_tokens_is_zero() -> None:
    assert compute_message_cost(
        input_tokens=0, output_tokens=0, model_id="claude-haiku-4-5"
    ) == 0.0


def test_compute_message_cost_input_and_output_add() -> None:
    """1000 input tokens + 500 output tokens at Haiku rates."""
    p = lookup("claude-haiku-4-5")
    expected = p.input_per_1k_usd * 1.0 + p.output_per_1k_usd * 0.5
    actual = compute_message_cost(
        input_tokens=1000, output_tokens=500, model_id="claude-haiku-4-5"
    )
    assert actual == pytest.approx(expected, rel=1e-9)


def test_compute_message_cost_opus_more_expensive_than_haiku() -> None:
    """Same token volume costs strictly more on Opus than Haiku."""
    common_kwargs = {"input_tokens": 10_000, "output_tokens": 5_000}
    haiku = compute_message_cost(model_id="claude-haiku-4-5", **common_kwargs)
    opus = compute_message_cost(model_id="claude-opus-4-7", **common_kwargs)
    assert opus > haiku
    assert opus / haiku > 5  # Opus is roughly 18× Haiku — wide ratio is safe


# ── CostRollup dataclass ──────────────────────────────────────────────


def test_cost_rollup_as_dict_rounds_six_decimals() -> None:
    """USD values are rounded to 6 decimals — enough for fractions of a
    cent without showing IEEE float noise."""
    from research_assistant.services.cost_rollup import CostRollup

    r = CostRollup()
    r.usd_total = 0.1 + 0.2  # 0.30000000000000004 in IEEE 754
    r.usd_this_month = 0.05
    r.by_workflow = {"meta_analysis": 0.1234567}
    r.by_model_family = {"haiku": 0.000001234}
    out = r.as_dict()
    assert out["usd_total"] == 0.3
    assert isinstance(out["by_workflow"], dict)
    assert out["by_workflow"]["meta_analysis"] == 0.123457
    assert out["by_model_family"]["haiku"] == 1e-6


def test_cost_rollup_initial_values_are_zero() -> None:
    from research_assistant.services.cost_rollup import CostRollup

    r = CostRollup()
    assert r.usd_total == 0
    assert r.usd_this_month == 0
    assert r.input_tokens == 0
    assert r.output_tokens == 0
    assert r.n_turns == 0
    assert r.by_workflow == {}
    assert r.by_model_family == {}


# ── _parse_done_event helper ───────────────────────────────────────────


def test_parse_done_event_handles_legacy_no_model_id() -> None:
    """Legacy done events written before P2 #6 don't have a model_id —
    parser should return None and the cost code falls back to the
    default pricing."""
    from types import SimpleNamespace

    from research_assistant.services.cost_rollup import _parse_done_event

    legacy = SimpleNamespace(
        data='{"usage": {"input_tokens": 100, "output_tokens": 50}, "workflow": "meta_analysis"}',
        created_at=datetime.now(UTC),
    )
    input_t, output_t, workflow, model_id = _parse_done_event(legacy)  # type: ignore[arg-type]
    assert input_t == 100
    assert output_t == 50
    assert workflow == "meta_analysis"
    assert model_id is None


def test_parse_done_event_picks_up_model_id_when_present() -> None:
    from types import SimpleNamespace

    from research_assistant.services.cost_rollup import _parse_done_event

    evt = SimpleNamespace(
        data='{"usage": {"input_tokens": 1, "output_tokens": 2, "model_id": "claude-haiku-4-5"}, "workflow": "nma"}',
        created_at=datetime.now(UTC),
    )
    _, _, _, model_id = _parse_done_event(evt)  # type: ignore[arg-type]
    assert model_id == "claude-haiku-4-5"


def test_parse_done_event_tolerates_malformed_json() -> None:
    from types import SimpleNamespace

    from research_assistant.services.cost_rollup import _parse_done_event

    bad = SimpleNamespace(data="not json at all", created_at=datetime.now(UTC))
    assert _parse_done_event(bad) == (0, 0, None, None)  # type: ignore[arg-type]


def test_parse_done_event_tolerates_missing_usage() -> None:
    from types import SimpleNamespace

    from research_assistant.services.cost_rollup import _parse_done_event

    evt = SimpleNamespace(data='{"workflow": "general_qa"}', created_at=datetime.now(UTC))
    assert _parse_done_event(evt) == (0, 0, None, None)  # type: ignore[arg-type]


# ── Month boundary ────────────────────────────────────────────────────


def test_month_start_is_utc_midnight_first_of_month() -> None:
    from research_assistant.services.cost_rollup import _month_start

    now = datetime(2026, 5, 15, 14, 30, 0, tzinfo=UTC)
    start = _month_start(now)
    assert start.year == 2026
    assert start.month == 5
    assert start.day == 1
    assert start.hour == 0
    assert start.minute == 0
    assert start.tzinfo == UTC


def test_month_start_handles_year_boundary() -> None:
    from research_assistant.services.cost_rollup import _month_start

    now = datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC)
    start = _month_start(now)
    assert start.year == 2026
    assert start.month == 1
    assert start.day == 1


# Silence unused import warning when running test_cost_rollup.py in isolation.
_ = timedelta
_ = ModelPricing
