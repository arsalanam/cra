"""Dispatcher routing for the csr_drafter specialist."""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "Draft a Clinical Study Report for this trial",
        "I need a CSR drafter for the Phase 2 study",
        "Prepare a CSR document for FDA submission",
        "Build a CSR draft from the locked database",
        "Help with the ICH E3 report",
        "Draft a CSR using the ADTTE results",
    ],
)
def test_routes_to_csr_drafter(msg: str) -> None:
    assert classify(msg, None) == "csr_drafter"


@pytest.mark.parametrize(
    "msg",
    [
        "CSR intake confirmed",
        "CSR synopsis confirmed",
        "CSR data sections confirmed",
        "Refine CSR: shorten the safety section",
        "Finalize CSR",
        "Draft CSR from meta-analysis",
        "Draft CSR from ADTTE",
        "Draft CSR from SAP",
    ],
)
def test_continuation_messages_stay_in_csr(msg: str) -> None:
    assert classify(msg, None) == "csr_drafter"


def test_slash_csr_routes_to_csr_drafter() -> None:
    assert classify("/csr", None) == "csr_drafter"
    assert classify("/e3", None) == "csr_drafter"
    assert classify("/study-report", None) == "csr_drafter"


def test_csr_intake_confirmed_does_not_collide_with_registration() -> None:
    """The disambiguated prefix 'CSR intake confirmed' must route to
    csr_drafter even though 'Intake confirmed' (no prefix) belongs to
    registration_drafter."""
    assert classify("CSR intake confirmed", None) == "csr_drafter"
    assert classify("Intake confirmed", None) == "registration_drafter"


def test_definitional_question_about_csr_routes_to_general_qa() -> None:
    assert classify("what is a CSR?", None) == "general_qa"
