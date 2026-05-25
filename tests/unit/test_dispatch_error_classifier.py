"""Unit tests for the agent-error classifier in web/dispatch.py."""

from __future__ import annotations

from research_assistant.web.dispatch import _classify_agent_error


def test_daily_token_quota_returns_429_with_friendly_message() -> None:
    exc = Exception(
        "status_code: 429, model_name: us.anthropic.claude-sonnet-4-6, "
        "body: {'Error': {'Message': 'Too many tokens per day, please wait "
        "before trying again.', 'Code': 'ThrottlingException'}}"
    )
    status, detail = _classify_agent_error(exc)
    assert status == 429
    assert "daily token quota exceeded" in detail
    assert "00:00 UTC" in detail
    assert "Service Quotas" in detail


def test_generic_throttling_returns_429_with_per_minute_message() -> None:
    exc = Exception(
        "status_code: 429, body: {'Error': {'Code': 'ThrottlingException', "
        "'Message': 'Rate exceeded'}}"
    )
    status, detail = _classify_agent_error(exc)
    assert status == 429
    assert "per-minute throttle" in detail
    assert "Wait ~30 seconds" in detail


def test_unrecognized_error_falls_through_to_500() -> None:
    exc = ValueError("unexpected internal failure")
    status, detail = _classify_agent_error(exc)
    assert status == 500
    assert "unexpected internal failure" in detail
    # No "Agent error:" prefix — the frontend adds its own framing.
    assert not detail.startswith("Agent error:")


def test_daily_quota_check_beats_generic_throttle_check() -> None:
    """A daily-quota error also matches the generic ThrottlingException pattern;
    classifier must return the more specific message."""
    exc = Exception("ThrottlingException: Too many tokens per day, please wait")
    status, detail = _classify_agent_error(exc)
    assert status == 429
    assert "daily token quota" in detail
    assert "per-minute throttle" not in detail
