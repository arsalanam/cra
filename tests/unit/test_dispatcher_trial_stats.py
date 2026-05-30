"""Dispatcher routing for the trial_stats specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "Run a Kaplan-Meier on OS and PFS",
        "Fit a Cox PH model comparing Drug A to placebo",
        "MMRM on HbA1c change from baseline at week 24",
        "log-rank test for the time-to-event endpoints",
        "Generate a subgroup forest by sex and age group",
        "ITT vs PP analysis for the primary endpoint",
        "Per-protocol analysis on PFS",
        "Compute the interaction p-value for SEX × treatment",
        "Trial-stats workflow for this locked dataset",
        "Efficacy analysis from ADTTE",
    ],
)
def test_routes_to_trial_stats(msg: str) -> None:
    assert classify(msg, None) == "trial_stats"


@pytest.mark.parametrize(
    "msg",
    [
        "Trial-stats intake confirmed",
        "Populations confirmed",
        "Time-to-event confirmed",
        "Continuous results confirmed",
        "Binary results confirmed",
        "Subgroup results confirmed",
        "Refine trial-stats: drop PFS and re-run",
        "Finalize trial-stats",
        "Draft trial-stats from ADTTE",
        "Draft trial-stats from CDISC",
    ],
)
def test_continuation_messages_stay_in_trial_stats(msg: str) -> None:
    assert classify(msg, None) == "trial_stats"


def test_slash_commands_route_to_trial_stats() -> None:
    assert classify("/trial-stats", None) == "trial_stats"
    assert classify("/trialstats", None) == "trial_stats"
    assert classify("/efficacy", None) == "trial_stats"
    assert classify("/km", None) == "trial_stats"
    assert classify("/mmrm", None) == "trial_stats"


def test_definitional_question_routes_to_general_qa() -> None:
    """A "what is" question should not enter the workflow even when it
    names a trial-stats method."""
    assert classify("what is Kaplan-Meier?", None) == "general_qa"
    assert classify("explain MMRM analysis", None) == "general_qa"


def test_csr_and_trial_stats_keywords_route_to_csr_first() -> None:
    """CSR-specific phrases are checked before trial-stats so the
    operator who asks for a CSR drafter session keeps the CSR routing."""
    assert classify("Draft a clinical study report from ADTTE", None) == "csr_drafter"
    assert classify("Build a CSR drafter session for the lock", None) == "csr_drafter"


def test_continuation_does_not_collide_with_csr() -> None:
    """`Trial-stats intake confirmed` is not the same as CSR's
    `CSR intake confirmed` — make sure the disambiguator works."""
    assert classify("CSR intake confirmed", None) == "csr_drafter"
    assert classify("Trial-stats intake confirmed", None) == "trial_stats"
