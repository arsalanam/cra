"""Dispatcher routing for the lay_summary specialist (P2 #2)."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "Draft a lay summary for this trial",
        "I need a plain-language summary",
        "Plain-English summary for parents",
        "Draft a patient-facing summary for the study",
        "Return-of-results letter for the trial",
        "Help me write a plain language summary for my IRB",
        "Draft a plain-language summary at grade 6",
    ],
)
def test_routes_to_lay_summary(msg: str) -> None:
    assert classify(msg, None) == "lay_summary"


@pytest.mark.parametrize(
    "msg",
    [
        "Recruitment intake confirmed",
        "Evidence intake confirmed",
        "Results intake confirmed",
        "Draft confirmed",
        "Reduce reading level",
        "Refine lay summary: simplify the procedures",
        "Finalize lay summary",
        "Draft lay summary from meta-analysis",
        "Draft lay summary from CSR",
        "Draft lay summary from IRB packet",
    ],
)
def test_continuation_messages_stay_in_lay_summary(msg: str) -> None:
    assert classify(msg, None) == "lay_summary"


def test_slash_lay_routes_to_lay_summary() -> None:
    assert classify("/lay", None) == "lay_summary"
    assert classify("/lay-summary", None) == "lay_summary"
    assert classify("/pls", None) == "lay_summary"
    assert classify("/plain-language", None) == "lay_summary"
    assert classify("/patient-summary", None) == "lay_summary"


def test_definitional_question_about_lay_summary_routes_to_general_qa() -> None:
    assert classify("what is a lay summary?", None) == "general_qa"


def test_draft_a_manuscript_still_routes_to_manuscript_not_lay() -> None:
    """The lay_summary regex is checked BEFORE manuscript_drafter, but
    'draft a manuscript' must still land in manuscript_drafter because
    the lay-summary regex requires 'lay' / 'plain' / 'patient' as the
    qualifier on 'summary'."""
    assert classify("Draft a manuscript on this topic", None) == "manuscript_drafter"


def test_pinned_thread_to_meta_analysis_still_takes_lay_continuation() -> None:
    """Continuation prefixes beat workflow pinning, since the user is
    explicitly asking to hand off."""
    assert classify("Draft lay summary from meta-analysis", "meta_analysis") == "lay_summary"
