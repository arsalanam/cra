"""Unit tests for the dispatcher routing the sr_protocol specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify
from research_assistant.agent.specialists import (
    general_qa,
    meta_analysis,
    search_strategy,
    sr_protocol,
)


@pytest.mark.parametrize(
    "msg",
    [
        "/protocol SGLT2 inhibitors for HF prevention in T2DM",
        "/sr question on SGLT2 inhibitors",
        "/prisma SGLT2 in T2DM",
    ],
)
def test_slash_commands_route_to_sr_protocol(msg: str) -> None:
    assert classify(msg, current_workflow=None) == sr_protocol.WORKFLOW_NAME


@pytest.mark.parametrize(
    "msg",
    [
        "Help me draft a protocol for an SR on SGLT2 inhibitors",
        "I need to write a PRISMA-P protocol for HF prevention",
        "Draft a systematic review protocol on diet and CVD",
        "I want to register a protocol on PROSPERO for this question",
        "Build me a meta-analysis protocol for SGLT2 + HF outcomes",
    ],
)
def test_keywords_trigger_sr_protocol(msg: str) -> None:
    assert classify(msg, current_workflow=None) == sr_protocol.WORKFLOW_NAME


@pytest.mark.parametrize(
    "msg",
    [
        "Methods confirmed — proceed to STEP 3",
        "Finalize protocol — lock the document",
    ],
)
def test_continuation_prefixes_stay_in_sr_protocol(msg: str) -> None:
    assert classify(msg, current_workflow=None) == sr_protocol.WORKFLOW_NAME


def test_protocol_keyword_beats_meta_analysis_keyword() -> None:
    """'meta-analysis protocol' must route to sr_protocol, not meta_analysis."""
    msg = "Draft a meta-analysis protocol for SGLT2 inhibitors in HFpEF"
    assert classify(msg, current_workflow=None) == sr_protocol.WORKFLOW_NAME


def test_protocol_keyword_beats_search_strategy_keyword() -> None:
    """A protocol mentioning 'search strategy' goes to sr_protocol; the
    protocol covers planned search, while search_strategy is for executing one."""
    msg = "I need a PRISMA-P protocol that includes a search strategy section"
    assert classify(msg, current_workflow=None) == sr_protocol.WORKFLOW_NAME


def test_meta_analysis_question_still_routes_to_meta_analysis() -> None:
    """A bare meta-analysis question (no protocol framing) still goes to meta_analysis."""
    msg = "Does adding a PPI to DAPT reduce upper GI bleeding in post-PCI patients?"
    assert classify(msg, current_workflow=None) == meta_analysis.WORKFLOW_NAME


def test_search_strategy_question_still_routes_to_search_strategy() -> None:
    """A bare search-strategy question (no protocol framing) still goes there."""
    msg = "Build me a PubMed search strategy for SGLT2 inhibitors"
    assert classify(msg, current_workflow=None) == search_strategy.WORKFLOW_NAME


def test_pinned_sr_protocol_thread_stays_pinned() -> None:
    msg = "what about including observational studies"
    assert (
        classify(msg, current_workflow=sr_protocol.WORKFLOW_NAME)
        == sr_protocol.WORKFLOW_NAME
    )


def test_definitional_question_breaks_out_to_general_qa() -> None:
    """Definitional openings always go to general_qa, even mid-protocol thread."""
    msg = "what is GRADE?"
    assert (
        classify(msg, current_workflow=sr_protocol.WORKFLOW_NAME)
        == general_qa.WORKFLOW_NAME
    )
