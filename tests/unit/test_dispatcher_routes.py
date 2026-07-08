"""Route semantics — sticky flags, scoped continuations, handoff seeds,
and the implicit-route authorization fallback.

Complements the per-workflow test_dispatcher_* files, which cover keyword
routing per specialist; this file covers the Route metadata introduced by
the agent-loop review (docs/agent-loop-review.md, findings A1/A2/A4).
"""

from __future__ import annotations

from typing import Any

import pytest

from research_assistant.agent import dispatcher
from research_assistant.agent.dispatcher import (
    Route,
    SkillNotAuthorizedError,
    classify_route,
    dispatch,
)
from research_assistant.auth.rbac import ROLE_PERMISSIONS, Role

# ── Definitional detours (A1) ────────────────────────────────────────────


def test_definitional_mid_workflow_is_non_sticky() -> None:
    route = classify_route("what is heterogeneity?", "meta_analysis")
    assert route.workflow == "general_qa"
    assert route.rule == "definitional"
    assert route.sticky is False


def test_definitional_on_fresh_thread_is_sticky() -> None:
    route = classify_route("what is a forest plot?", None)
    assert route.workflow == "general_qa"
    assert route.sticky is True


def test_workflow_survives_after_definitional_detour() -> None:
    """The detour doesn't unpin: a later free-form message stays in the
    workflow because the thread's `current_workflow` was never changed."""
    detour = classify_route("why is I² important?", "meta_analysis")
    assert detour.sticky is False
    followup = classify_route("ok continue with the extraction", "meta_analysis")
    assert followup.workflow == "meta_analysis"
    assert followup.rule == "pinned"


# ── Scoped continuations (A2) ────────────────────────────────────────────


def test_own_continuation_prefix_stays_in_workflow() -> None:
    route = classify_route("CSR intake confirmed", "csr_drafter")
    assert route.workflow == "csr_drafter"
    assert route.rule == "continuation"
    assert route.sticky is True


def test_foreign_continuation_prefix_cannot_hijack_pinned_thread() -> None:
    """'Intake confirmed' is registration_drafter's button text. A thread
    pinned to csr_drafter must NOT jump to registration_drafter."""
    route = classify_route("Intake confirmed", "csr_drafter")
    assert route.workflow == "csr_drafter"
    assert route.rule == "pinned"


def test_unpinned_thread_honours_any_continuation_prefix() -> None:
    route = classify_route("NMA PICO confirmed", None)
    assert route.workflow == "nma"
    assert route.rule == "continuation"


def test_handoff_seed_switches_workflow_from_pinned_thread() -> None:
    route = classify_route("Draft CSR from meta-analysis", "meta_analysis")
    assert route.workflow == "csr_drafter"
    assert route.rule == "handoff"
    assert route.sticky is True


def test_handoff_seed_works_on_fresh_thread() -> None:
    route = classify_route("Draft lay summary from CSR", None)
    assert route.workflow == "lay_summary"
    assert route.rule == "handoff"


def test_rob_adhoc_trigger_still_reachable_from_anywhere() -> None:
    route = classify_route("Run RoB on PMID 12345678", "meta_analysis")
    assert route.workflow == "risk_of_bias"
    assert route.rule == "handoff"


# ── Rule labels for the other paths ──────────────────────────────────────


def test_slash_command_rule() -> None:
    route = classify_route("/meta effect of statins on LDL", None)
    assert route.workflow == "meta_analysis"
    assert route.rule == "slash"


def test_keyword_rule() -> None:
    route = classify_route("run a meta-analysis on SGLT2 inhibitors", None)
    assert route.workflow == "meta_analysis"
    assert route.rule == "keyword"


def test_default_rule() -> None:
    route = classify_route("hello there", None)
    assert route.workflow == "general_qa"
    assert route.rule == "default"


def test_classify_shim_returns_workflow_string() -> None:
    assert dispatcher.classify("hello there", None) == "general_qa"


# ── Authorization fallback (A4) ──────────────────────────────────────────


@pytest.fixture
def stub_general_qa(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace general_qa.run_turn so dispatch() never hits Bedrock."""
    calls: dict[str, Any] = {}

    async def _fake_run_turn(user_message: str, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
        calls["message"] = user_message
        return object(), {}

    monkeypatch.setattr(dispatcher.SPECIALISTS["general_qa"], "run_turn", _fake_run_turn)
    return calls


async def test_keyword_route_without_permission_falls_back_to_general_qa(
    stub_general_qa: dict[str, Any],
) -> None:
    """Student lacks search_strategy; a keyword-inferred route must not 403."""
    student = ROLE_PERMISSIONS[Role.STUDENT]
    _, meta, route = await dispatch(
        "build a search strategy for statins in pubmed",
        effective_permissions=student,
    )
    assert route == Route("general_qa", rule="auth_fallback", sticky=False)
    assert meta["route"]["rule"] == "auth_fallback"
    assert stub_general_qa["message"].startswith("build a search strategy")


async def test_slash_forced_route_without_permission_still_403s() -> None:
    student = ROLE_PERMISSIONS[Role.STUDENT]
    with pytest.raises(SkillNotAuthorizedError) as exc:
        await dispatch("/search statins query", effective_permissions=student)
    assert exc.value.workflow == "search_strategy"


async def test_authorized_keyword_route_is_unchanged(stub_general_qa: dict[str, Any]) -> None:
    """A permitted implicit route runs its own specialist, not the fallback."""
    student = ROLE_PERMISSIONS[Role.STUDENT]
    _, meta, route = await dispatch(
        "hello there",
        effective_permissions=student,
    )
    assert route.workflow == "general_qa"
    assert route.rule == "default"
    assert meta["route"]["sticky"] is True
