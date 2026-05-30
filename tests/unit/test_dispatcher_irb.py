"""Dispatcher routing for the irb_drafter specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "I need to prepare an IRB submission",
        "Help draft the informed consent form",
        "I need an ICF for this study",
        "Draft a protocol synopsis for the IRB",
        "What does 21 CFR 50.25 require",
        "Build an IRB packet",
        "We're submitting to the ethics committee",
        "ICH E6 requirements for consent",
    ],
)
def test_routes_to_irb_drafter(msg: str) -> None:
    assert classify(msg, None) == "irb_drafter"


@pytest.mark.parametrize(
    "msg",
    [
        "Synopsis confirmed",
        "Refine ICF: shorten the procedures section",
        "ICF confirmed",
        "Finalize IRB",
        "Draft IRB packet from registration intake",
    ],
)
def test_continuation_messages_stay_in_irb(msg: str) -> None:
    assert classify(msg, None) == "irb_drafter"


def test_slash_irb_routes_to_irb_drafter() -> None:
    assert classify("/irb", None) == "irb_drafter"
    assert classify("/icf", None) == "irb_drafter"
    assert classify("/consent", None) == "irb_drafter"


def test_definitional_question_about_consent_routes_to_general_qa() -> None:
    assert classify("what is informed consent?", None) == "general_qa"
