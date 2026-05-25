"""Unit tests for the dispatcher routing the risk_of_bias specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify
from research_assistant.agent.specialists import (
    general_qa,
    meta_analysis,
    risk_of_bias,
    search_strategy,
    sr_protocol,
)


@pytest.mark.parametrize(
    "msg",
    [
        "/rob PMID 123, PMID 456",
        "/bias these RCTs",
    ],
)
def test_slash_commands_route_to_risk_of_bias(msg: str) -> None:
    assert classify(msg, current_workflow=None) == risk_of_bias.WORKFLOW_NAME


@pytest.mark.parametrize(
    "msg",
    [
        "Assess the risk of bias for these RCTs",
        "Run a RoB assessment on PMID 12345678",
        "Apply RoB 2.0 to my included studies",
        "I need ROBINS-I judgments for the cohort studies",
        "Use Newcastle-Ottawa for these case-control studies",
        "Run QUADAS-2 on these diagnostic accuracy studies",
    ],
)
def test_keywords_trigger_risk_of_bias(msg: str) -> None:
    assert classify(msg, current_workflow=None) == risk_of_bias.WORKFLOW_NAME


@pytest.mark.parametrize(
    "msg",
    [
        "RoB confirmed — proceed to STEP 3 with these edited assessments",
        "Finalize RoB — lock the summary",
        "Run RoB on PMID 12345 from a fresh thread",
        "Run risk of bias on extracted studies (handoff payload follows)",
    ],
)
def test_continuation_prefixes_stay_in_risk_of_bias(msg: str) -> None:
    assert classify(msg, current_workflow=None) == risk_of_bias.WORKFLOW_NAME


def test_rob_keyword_beats_meta_analysis_keyword() -> None:
    """'risk of bias for our meta-analysis' must route to risk_of_bias, not meta_analysis."""
    msg = "Run risk of bias on the studies in our meta-analysis"
    assert classify(msg, current_workflow=None) == risk_of_bias.WORKFLOW_NAME


def test_rob_keyword_beats_sr_protocol_keyword() -> None:
    """A RoB request mentioning a protocol still goes to risk_of_bias."""
    msg = "I need RoB 2.0 judgments for the protocol's included studies"
    assert classify(msg, current_workflow=None) == risk_of_bias.WORKFLOW_NAME


def test_meta_analysis_question_still_routes_to_meta_analysis() -> None:
    """A bare meta-analysis question (no RoB framing) still goes to meta_analysis."""
    msg = "Does adding a PPI to DAPT reduce upper GI bleeding in post-PCI patients?"
    assert classify(msg, current_workflow=None) == meta_analysis.WORKFLOW_NAME


def test_search_strategy_question_still_routes_to_search_strategy() -> None:
    msg = "Build me a PubMed search strategy for SGLT2 inhibitors"
    assert classify(msg, current_workflow=None) == search_strategy.WORKFLOW_NAME


def test_protocol_question_still_routes_to_sr_protocol() -> None:
    msg = "Draft a PRISMA-P protocol for SGLT2 in T2DM"
    assert classify(msg, current_workflow=None) == sr_protocol.WORKFLOW_NAME


def test_pinned_risk_of_bias_thread_stays_pinned() -> None:
    msg = "what about being stricter on the missing-data domain"
    assert classify(msg, current_workflow=risk_of_bias.WORKFLOW_NAME) == risk_of_bias.WORKFLOW_NAME


def test_definitional_question_breaks_out_to_general_qa() -> None:
    """Definitional openings always go to general_qa, even mid-RoB thread."""
    msg = "what is RoB 2.0?"
    assert classify(msg, current_workflow=risk_of_bias.WORKFLOW_NAME) == general_qa.WORKFLOW_NAME
