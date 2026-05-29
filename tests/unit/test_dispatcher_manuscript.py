"""manuscript_drafter dispatcher routing.

Locks slash commands + trigger keywords + continuations so unrelated
turns ("effect of X on Y") still beat manuscript_drafter into
meta_analysis but composition intent reliably wins.
"""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import classify


@pytest.mark.parametrize(
    "msg",
    [
        "/manuscript",
        "/imrad",
        "/draft",
        "/response",
        "Draft a manuscript on PPI and DAPT for NEJM submission",
        "draft the manuscript please",
        "Compose this analysis as an IMRaD paper",
        "Respond to the reviewers on our paper",
        "Help me draft a point-by-point reviewer response",
        "Draft a cover letter to the editor",
        "Reviewer response document needed",
        "Lancet manuscript format please",
    ],
)
def test_routes_to_manuscript_drafter(msg: str) -> None:
    assert classify(msg, None) == "manuscript_drafter"


@pytest.mark.parametrize(
    "msg",
    [
        "Manuscript intake confirmed — proceed to STEP 2",
        "Refine manuscript: shorten the Discussion to two paragraphs",
        "Finalize manuscript",
        "Reviewer comments: R1 says…",
        "Finalize reviewer response",
        "Draft as manuscript from meta-analysis",
        "Draft as manuscript from SR protocol",
    ],
)
def test_continuation_prefixes_stay_in_manuscript(msg: str) -> None:
    """Even from a fresh thread, the continuation prefix wins routing."""
    assert classify(msg, None) == "manuscript_drafter"


def test_meta_analysis_still_wins_for_effect_of_questions() -> None:
    """'effect of X on Y' is the canonical meta-analysis trigger — must
    not be hijacked by the manuscript drafter."""
    assert classify("what is the effect of metformin on HbA1c?", None) != "manuscript_drafter"


def test_sap_still_wins_for_sample_size_questions() -> None:
    assert classify("sample size for a non-inferiority trial", None) != "manuscript_drafter"


def test_sr_protocol_still_wins_for_PRISMA_questions() -> None:
    """The roadmap trigger 'PRISMA-P' should reach sr_protocol, not the
    manuscript drafter — manuscripts cite PRISMA but the workflow is
    different."""
    assert classify("Draft a PRISMA-P protocol", None) == "sr_protocol"


def test_definitional_opening_routes_to_general_qa() -> None:
    """'what is IMRaD' is a definitional question — general_qa, not
    manuscript_drafter."""
    assert classify("what is IMRaD?", None) == "general_qa"
