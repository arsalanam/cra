"""Dispatcher routing for the grade_drafter specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "I need a GRADE Summary of Findings table for my meta-analysis",
        "Build a GRADE SoF for the three primary outcomes",
        "Run a GRADE certainty assessment per outcome",
        "Help me with summary of findings",
        "Compute certainty of evidence using GRADE",
        "Generate a PRISMA checklist for my SR",
        "Need the PRISMA 2020 reporting checklist",
    ],
)
def test_routes_to_grade_drafter(msg: str) -> None:
    assert classify(msg, None) == "grade_drafter"


@pytest.mark.parametrize(
    "msg",
    [
        "GRADE intake confirmed",
        "Outcome assessed",
        "SoF confirmed",
        "PRISMA confirmed",
        "Refine SoF: collapse the moderate rows",
        "Finalize GRADE",
        "Draft GRADE from meta-analysis",
    ],
)
def test_continuation_messages_stay_in_grade(msg: str) -> None:
    assert classify(msg, None) == "grade_drafter"


def test_slash_grade_routes_to_grade_drafter() -> None:
    assert classify("/grade", None) == "grade_drafter"
    assert classify("/sof", None) == "grade_drafter"
    assert classify("/prisma-checklist", None) == "grade_drafter"


def test_definitional_question_about_grade_routes_to_general_qa() -> None:
    assert classify("what is GRADE?", None) == "general_qa"


def test_risk_of_bias_keywords_still_route_to_rob_not_grade() -> None:
    """RoB and GRADE share methodological territory but the RoB drafter
    should still win when the user asks for a RoB assessment."""
    assert classify("assess risk of bias for these 8 studies", None) == "risk_of_bias"
