"""LLM fallback classifier (step 9, docs/agent-loop-review.md).

The classifier is consulted ONLY for rule="default" turns, is gated by
`dispatcher_llm_fallback_enabled`, and fails open — dispatch is never
worse than the regex default. Tests stub `classify_with_llm`; the live
Bedrock path is intentionally not exercised here.
"""

from __future__ import annotations

from typing import Any

import pytest

from research_assistant.agent import dispatcher, fallback_classifier
from research_assistant.agent.dispatcher import Route, dispatch
from research_assistant.auth.rbac import ROLE_PERMISSIONS, Role

_NO_SIGNAL_MSG = "hello there"  # regex cascade → rule="default"


@pytest.fixture
def fallback_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip the (conftest-disabled) setting on for one test. get_settings()
    re-reads env on every call, so the env var is the control surface."""
    monkeypatch.setenv("DISPATCHER_LLM_FALLBACK_ENABLED", "true")


@pytest.fixture
def stub_specialist(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub EVERY specialist's run_turn so dispatch never hits Bedrock."""
    calls: dict[str, Any] = {}

    def _make(name: str) -> Any:
        async def _fake_run_turn(user_message: str, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
            calls["workflow"] = name
            calls["message"] = user_message
            return object(), {}

        return _fake_run_turn

    for name, module in dispatcher.SPECIALISTS.items():
        monkeypatch.setattr(module, "run_turn", _make(name))
    return calls


def _stub_llm(monkeypatch: pytest.MonkeyPatch, answer: str | None) -> dict[str, int]:
    counter = {"calls": 0}

    async def _fake(user_message: str) -> str | None:
        counter["calls"] += 1
        return answer

    monkeypatch.setattr(fallback_classifier, "classify_with_llm", _fake)
    return counter


async def test_llm_fallback_reroutes_default_turns(
    fallback_enabled: None,
    stub_specialist: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _stub_llm(monkeypatch, "sap_drafter")
    _, meta, route = await dispatch(_NO_SIGNAL_MSG)
    assert counter["calls"] == 1
    assert route == Route("sap_drafter", rule="llm_fallback", sticky=True)
    assert stub_specialist["workflow"] == "sap_drafter"
    assert meta["route"]["rule"] == "llm_fallback"


async def test_llm_failure_keeps_regex_default(
    fallback_enabled: None,
    stub_specialist: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_llm(monkeypatch, None)  # classifier failed / timed out
    _, _, route = await dispatch(_NO_SIGNAL_MSG)
    assert route.workflow == "general_qa"
    assert route.rule == "default"
    assert stub_specialist["workflow"] == "general_qa"


async def test_llm_agreeing_with_general_qa_keeps_default_rule(
    fallback_enabled: None,
    stub_specialist: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_llm(monkeypatch, "general_qa")
    _, _, route = await dispatch(_NO_SIGNAL_MSG)
    assert route.rule == "default"  # no rewrite when the LLM agrees


async def test_disabled_setting_skips_classifier(
    stub_specialist: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conftest disables the setting — the classifier must not be called."""
    counter = _stub_llm(monkeypatch, "sap_drafter")
    _, _, route = await dispatch(_NO_SIGNAL_MSG)
    assert counter["calls"] == 0
    assert route.rule == "default"


async def test_keyword_matches_never_consult_the_llm(
    fallback_enabled: None,
    stub_specialist: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _stub_llm(monkeypatch, "sap_drafter")
    _, _, route = await dispatch("run a meta-analysis on SGLT2 inhibitors")
    assert counter["calls"] == 0
    assert route == Route("meta_analysis", rule="keyword", sticky=True)


async def test_llm_fallback_route_respects_rbac_fallback(
    fallback_enabled: None,
    stub_specialist: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An LLM-picked workflow the caller can't run degrades to general_qa
    (llm_fallback is an implicit rule), never a 403."""
    _stub_llm(monkeypatch, "search_strategy")  # student lacks this skill
    _, _, route = await dispatch(
        _NO_SIGNAL_MSG,
        effective_permissions=ROLE_PERMISSIONS[Role.STUDENT],
    )
    assert route == Route("general_qa", rule="auth_fallback", sticky=False)
    assert stub_specialist["workflow"] == "general_qa"


async def test_classify_with_llm_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any exception inside the classifier returns None, never raises."""

    def _boom() -> Any:
        raise RuntimeError("bedrock unreachable")

    monkeypatch.setattr(fallback_classifier, "_get_agent", _boom)
    assert await fallback_classifier.classify_with_llm("anything") is None
