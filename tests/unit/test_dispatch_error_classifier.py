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


def test_usage_limit_exceeded_returns_400_with_actionable_message() -> None:
    """A tripped per-turn tool-call cap must surface a friendly 400, not a raw
    500 — the user narrows scope or pastes data instead of retrying blind."""
    from pydantic_ai.exceptions import UsageLimitExceeded

    exc = UsageLimitExceeded(
        "The next tool call(s) would exceed the tool_calls_limit of 100 (tool_calls=101)."
    )
    status, detail = _classify_agent_error(exc)
    assert status == 400
    assert "ran out of research steps" in detail
    assert "narrow the question" in detail


def test_tool_error_budget_exceeded_returns_502() -> None:
    """The per-turn tool-error circuit breaker surfaces a transient 502, not a
    raw 500, so the user knows to retry."""
    from research_assistant.agent.deps import ToolErrorBudgetExceeded

    exc = ToolErrorBudgetExceeded("Aborted after 5 failed tool calls in one turn")
    status, detail = _classify_agent_error(exc)
    assert status == 502
    assert "several tool calls failed in a row" in detail
    assert "retry" in detail.lower()


def test_daily_quota_check_beats_generic_throttle_check() -> None:
    """A daily-quota error also matches the generic ThrottlingException pattern;
    classifier must return the more specific message."""
    exc = Exception("ThrottlingException: Too many tokens per day, please wait")
    status, detail = _classify_agent_error(exc)
    assert status == 429
    assert "daily token quota" in detail
    assert "per-minute throttle" not in detail
