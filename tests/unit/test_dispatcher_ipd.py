"""Dispatcher routing for the ipd specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "Run an IPD meta-analysis on the statin trials",
        "Pool individual patient data across the 6 RCTs",
        "I have patient-level data from 4 trials and want to compare",
        "IPD MA on diabetes intervention trials",
        "Use subject-level data to test subgroup-by-treatment effects",
        "One-stage model pooling subjects from multiple trials",
        "Treatment × subgroup interaction across trial-level patient data",
        "Subgroup-by-treatment analysis using patient data",
    ],
)
def test_routes_to_ipd(msg: str) -> None:
    assert classify(msg, None) == "ipd"


@pytest.mark.parametrize(
    "msg",
    [
        "IPD intake confirmed",
        "IPD bundle confirmed",
        "IPD main results confirmed",
        "IPD subgroup confirmed",
        "Add IPD subgroup: sex",
        "Refine IPD: drop trial X",
        "Finalize IPD",
    ],
)
def test_continuation_messages_stay_in_ipd(msg: str) -> None:
    assert classify(msg, None) == "ipd"


def test_slash_commands_route_to_ipd() -> None:
    assert classify("/ipd", None) == "ipd"
    assert classify("/ipdma", None) == "ipd"
    assert classify("/ipd-ma", None) == "ipd"
    assert classify("/subject-level", None) == "ipd"


def test_aggregate_meta_analysis_still_routes_to_meta_analysis() -> None:
    """Pairwise meta-analysis ask should NOT route to IPD."""
    assert classify("Does aspirin reduce stroke vs placebo", None) == "meta_analysis"


def test_nma_three_arm_compare_still_routes_to_nma() -> None:
    """NMA's multi-arm 'compare N <noun>' pattern shouldn't get swallowed
    by the IPD trigger."""
    assert classify("Compare 5 DOACs head-to-head", None) == "nma"


def test_definitional_ipd_question_routes_to_general_qa() -> None:
    assert classify("What is IPD meta-analysis?", None) == "general_qa"
