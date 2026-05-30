"""Dispatcher routing for the registration_drafter specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "I need to register this trial on ClinicalTrials.gov",
        "Help me prepare a PRS submission",
        "Draft an EU CTR record for this study",
        "How do I get a CTIS trial number",
        "Prepare the EudraCT submission for this trial",
        "I need to register a trial in Europe",
        "Build a trial registration draft for me",
    ],
)
def test_routes_to_registration_drafter(msg: str) -> None:
    assert classify(msg, None) == "registration_drafter"


@pytest.mark.parametrize(
    "msg",
    [
        "Intake confirmed",
        "Core fields confirmed",
        "Drafts confirmed",
        "Finalize registration",
    ],
)
def test_continuation_messages_stay_in_registration(msg: str) -> None:
    assert classify(msg, None) == "registration_drafter"


def test_slash_register_routes_to_registration_drafter() -> None:
    assert classify("/register", None) == "registration_drafter"
    assert classify("/ctgov", None) == "registration_drafter"
    assert classify("/ctis", None) == "registration_drafter"


def test_definitional_question_about_ctgov_routes_to_general_qa() -> None:
    assert classify("what is ClinicalTrials.gov?", None) == "general_qa"


def test_sap_keywords_still_route_to_sap_drafter_not_registration() -> None:
    """Sample-size keywords must not be hijacked by registration triggers."""
    assert classify("compute the sample size for this trial", None) == "sap_drafter"
