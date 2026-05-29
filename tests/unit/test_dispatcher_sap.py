"""sap_drafter dispatcher routing.

Locks the trigger patterns + slash commands + continuation prefixes so
unrelated turns ("effect of X on Y") still beat sap_drafter into
meta_analysis but trial-design intent reliably wins.
"""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "/sap",
        "/samplesize",
        "/sample-size",
        "/power",
        "I need a sample size for a trial of drug X vs placebo on bleeding",
        "Draft a Statistical Analysis Plan for this trial",
        "Help me with the SAP",
        "Build a power calculation for the new RCT",
        "PICOT for a prospective trial",
        "ICH E9 analysis plan",
        "What sample-size does this trial need to be powered to detect a 5% reduction?",
    ],
)
def test_routes_to_sap_drafter(msg: str) -> None:
    assert classify(msg, None) == "sap_drafter"


@pytest.mark.parametrize(
    "msg",
    [
        "PICOT confirmed — proceed to STEP 2",
        "Sample size confirmed — move on",
        "Analysis plan confirmed",
        "Finalize SAP — produce the final document",
    ],
)
def test_continuation_messages_stay_in_sap(msg: str) -> None:
    """Even from a fresh thread, the continuation prefix wins."""
    assert classify(msg, None) == "sap_drafter"


def test_meta_analysis_still_wins_for_effect_of_questions() -> None:
    """The classic meta-analysis trigger ('effect of X on Y') must not be
    hijacked by sap_drafter."""
    assert classify("what is the effect of metformin on HbA1c?", None) != "sap_drafter"


def test_definitional_openings_go_to_general_qa() -> None:
    """`what is a SAP` → general_qa, even though SAP is a sap_drafter
    keyword. Definitional openings outrank keyword triggers (existing
    rule)."""
    assert classify("what is a SAP?", None) == "general_qa"


def test_unrelated_message_still_routes_to_general_qa() -> None:
    assert classify("hi", None) == "general_qa"
