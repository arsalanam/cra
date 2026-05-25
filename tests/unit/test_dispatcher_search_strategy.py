"""Unit tests for the dispatcher routing the search-strategy specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify
from research_assistant.agent.specialists import (
    general_qa,
    meta_analysis,
    search_strategy,
)


@pytest.mark.parametrize(
    "msg",
    [
        "/search build me a query for ACS",
        "/strategy ACS PPI bleeding",
    ],
)
def test_slash_commands_route_to_search_strategy(msg: str) -> None:
    assert classify(msg, current_workflow=None) == search_strategy.WORKFLOW_NAME


@pytest.mark.parametrize(
    "msg",
    [
        "Build me a search strategy for PPI in post-PCI patients",
        "I want a PubMed query for atrial fibrillation",
        "Help me build a boolean search for asthma trials",
        "Translate this into MeSH terms for PubMed",
    ],
)
def test_keywords_trigger_search_strategy(msg: str) -> None:
    assert classify(msg, current_workflow=None) == search_strategy.WORKFLOW_NAME


@pytest.mark.parametrize(
    "msg",
    [
        "Confirmed query terms — population: ACS adults; intervention: PPI",
        "Tighten: restrict to last 10 years",
        "Broaden: drop the RCT-only filter",
        "Finalize strategy",
    ],
)
def test_continuation_prefixes_stay_in_search_strategy(msg: str) -> None:
    # No current_workflow needed — the continuation prefix wins regardless.
    assert classify(msg, current_workflow=None) == search_strategy.WORKFLOW_NAME


def test_search_keywords_beat_meta_analysis_keywords() -> None:
    """A 'search strategy for [research question]' must NOT route to meta_analysis
    even when the embedded question matches meta_analysis trigger patterns."""
    msg = "Build me a search strategy for: does PPI reduce bleeding in PCI patients"
    assert classify(msg, current_workflow=None) == search_strategy.WORKFLOW_NAME


def test_meta_analysis_question_still_routes_to_meta_analysis() -> None:
    """Sanity: the canonical research-question shape without 'search strategy'
    framing still goes to meta_analysis."""
    msg = "Does adding a PPI to DAPT reduce upper GI bleeding in post-PCI patients?"
    assert classify(msg, current_workflow=None) == meta_analysis.WORKFLOW_NAME


def test_pinned_search_strategy_thread_stays_pinned() -> None:
    """Once a thread is pinned, ambiguous follow-ups stay in the workflow."""
    msg = "what about including observational studies"
    assert (
        classify(msg, current_workflow=search_strategy.WORKFLOW_NAME)
        == search_strategy.WORKFLOW_NAME
    )


def test_definitional_question_breaks_out_to_general_qa() -> None:
    """Definitional openings always go to general_qa, even mid-strategy thread."""
    msg = "what is a MeSH term?"
    assert classify(msg, current_workflow=search_strategy.WORKFLOW_NAME) == general_qa.WORKFLOW_NAME
