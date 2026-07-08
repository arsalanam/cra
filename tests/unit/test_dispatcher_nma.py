"""Dispatcher routing for the nma specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "Run a network meta-analysis on 5 DOACs for AF",
        "I want NMA pooling DOAC trials",
        "Indirect comparison of 4 statins for cholesterol",
        "Build a league table for the SGLT2 inhibitors",
        "Mixed-treatment comparison of antihypertensives",
        "Compare 5 DOACs head-to-head where direct trials don't exist",
        "Ranking of treatments by efficacy across all RCTs",
    ],
)
def test_routes_to_nma(msg: str) -> None:
    assert classify(msg, None) == "nma"


@pytest.mark.parametrize(
    "msg",
    [
        "NMA PICO confirmed",
        "NMA studies selected",
        "NMA extraction confirmed",
        "Run Bayesian NMA",
        "Refine NMA: drop trial X",
        "Finalize NMA",
    ],
)
def test_continuation_messages_stay_in_nma(msg: str) -> None:
    assert classify(msg, None) == "nma"


def test_slash_commands_route_to_nma() -> None:
    assert classify("/nma", None) == "nma"
    assert classify("/network-ma", None) == "nma"
    assert classify("/indirect-comparison", None) == "nma"
    assert classify("/league-table", None) == "nma"


def test_two_arm_compare_stays_in_meta_analysis() -> None:
    """`compare 2 drugs` is pairwise — NMA below 3 arms makes no sense.
    The NMA pattern matches `compare [3-9]|\\d{2,}` so 2-arm comparisons
    should still route to meta_analysis."""
    assert classify("Compare 2 drugs for hypertension", None) == "meta_analysis"


def test_three_plus_arm_compare_routes_to_nma() -> None:
    assert classify("Compare 3 drugs for hypertension", None) == "nma"
    assert classify("Compare 10 antibiotics for UTI", None) == "nma"


def test_pairwise_meta_analysis_still_routes_to_meta_analysis() -> None:
    """A normal pairwise meta-analysis ask should NOT route to NMA."""
    assert classify("Does aspirin reduce stroke vs placebo", None) == "meta_analysis"


@pytest.mark.parametrize(
    "msg",
    [
        "what is network meta-analysis?",
        # "what does X tell me" is a definitional ask — answer it, don't spin up
        # the NMA workflow (which would then time out on a question).
        "What does SUCRA tell me about these treatments?",
    ],
)
def test_definitional_question_about_nma_routes_to_general_qa(msg: str) -> None:
    assert classify(msg, None) == "general_qa"
