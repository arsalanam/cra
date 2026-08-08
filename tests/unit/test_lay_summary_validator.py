"""Plain-language validator — medical-decision phrasing detector.

The lay_summary specialist informs, it never instructs the reader to take,
stop, or change treatment. `find_medical_decision_phrase` is the host-side
backstop for that prompt rule; these lock its behaviour (English only).
"""

from __future__ import annotations

import pytest

from research_assistant.agent.specialists.lay_summary import find_medical_decision_phrase


@pytest.mark.parametrize(
    "text",
    [
        "You should take this medicine every day.",
        "You must stop the tablets if you feel unwell.",
        "You need to increase your dose after week 4.",
        "We recommend you take the drug with food.",
        "Patients should stop taking the medication before surgery.",
        "You can stop taking it once symptoms clear.",
        "Ask your nurse to change your dose as needed.",
    ],
)
def test_flags_medical_decision_language(text: str) -> None:
    assert find_medical_decision_phrase(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        # Referral to a clinician is encouraged, not a medical decision.
        "You should talk to your doctor about whether this trial is right for you.",
        "You should ask your study team any questions you have.",
        "You should contact the site if you have side effects.",
        # Purely descriptive study language.
        "The study looked at whether the drug lowered blood pressure.",
        "Researchers measured how many people recovered within a year.",
        "This summary explains what the trial found in plain language.",
        "",
    ],
)
def test_allows_descriptive_and_referral_language(text: str) -> None:
    assert find_medical_decision_phrase(text) is None


def test_returns_the_matched_phrase() -> None:
    phrase = find_medical_decision_phrase("Remember, you must stop taking it tonight.")
    assert phrase is not None
    assert "must" in phrase.lower()


def test_case_insensitive() -> None:
    assert find_medical_decision_phrase("YOU SHOULD TAKE two pills.") is not None
