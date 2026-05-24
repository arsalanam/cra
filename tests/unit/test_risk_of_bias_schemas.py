"""Unit tests for the risk_of_bias domain schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.risk_of_bias import (
    ROB_DOMAINS_BY_TOOL,
    DomainDistribution,
    RobAssessments,
    RobDomain,
    RobSummary,
    StudyRobAssessment,
)


def _sample_assessment(pmid: str = "20925534", overall: str = "low") -> StudyRobAssessment:
    return StudyRobAssessment(
        pmid=pmid,
        title="COGENT — Clopidogrel with or without omeprazole",
        study_design="Randomized Controlled Trial",
        domains=[
            RobDomain(
                domain="Randomization process",
                judgment="low",
                justification="Sequence generated centrally with permuted blocks.",
                quote="randomly assigned in a 1:1 ratio with permuted blocks",
            ),
            RobDomain(
                domain="Deviations from intended interventions",
                judgment="low",
                justification="ITT analysis with low cross-over.",
            ),
            RobDomain(
                domain="Missing outcome data",
                judgment="some_concerns",
                justification="6% loss to follow-up; reasons not fully described.",
            ),
            RobDomain(
                domain="Measurement of the outcome",
                judgment="low",
                justification="Endoscopically confirmed events adjudicated blindly.",
            ),
            RobDomain(
                domain="Selection of the reported result",
                judgment="no_information",
                justification="Abstract does not describe pre-registered analysis plan.",
            ),
        ],
        overall_judgment=overall,  # type: ignore[arg-type]
        overall_rationale="Some concerns from missing outcome data.",
    )


def test_canonical_domain_lists_present() -> None:
    """ROB_DOMAINS_BY_TOOL is what the prompt references — keep it stable."""
    assert len(ROB_DOMAINS_BY_TOOL["RoB 2.0"]) == 5
    assert len(ROB_DOMAINS_BY_TOOL["ROBINS-I"]) == 7
    assert len(ROB_DOMAINS_BY_TOOL["Newcastle-Ottawa"]) == 3
    assert len(ROB_DOMAINS_BY_TOOL["QUADAS-2"]) == 4


def test_assessments_round_trip() -> None:
    obj = RobAssessments(
        tool="RoB 2.0",
        assessments=[_sample_assessment(), _sample_assessment("30873575", "some_concerns")],
        summary="Two RCTs assessed; randomization low across both, missing-data weak.",
    )
    dumped = obj.model_dump_json()
    assert '"kind":"rob_assessments"' in dumped
    revived = RobAssessments.model_validate_json(dumped)
    assert revived.tool == "RoB 2.0"
    assert len(revived.assessments) == 2
    assert revived.assessments[0].domains[0].judgment == "low"


def test_summary_round_trip_with_distribution() -> None:
    obj = RobSummary(
        tool="RoB 2.0",
        assessments=[_sample_assessment()],
        domain_distribution=[
            DomainDistribution(domain="Randomization process", low=4, some_concerns=1, high=0, no_information=0),
            DomainDistribution(domain="Selection of the reported result", low=0, some_concerns=2, high=0, no_information=3),
        ],
        summary_plot_image="rob_summary.png",
        narrative="Most studies low for randomization; selective reporting under-described.",
        sensitivity_recommendations=["Re-pool excluding PMID 11111111 (overall high)."],
        high_rob_pmids=["11111111"],
        is_final=False,
    )
    dumped = obj.model_dump_json()
    revived = RobSummary.model_validate_json(dumped)
    assert revived.kind == "rob_summary"
    assert revived.domain_distribution[1].no_information == 3
    assert revived.high_rob_pmids == ["11111111"]
    assert revived.is_final is False


def test_judgment_constrained_to_literal() -> None:
    """A made-up judgment value must be rejected — the UI relies on these four."""
    with pytest.raises(ValidationError):
        RobDomain.model_validate(
            {"domain": "x", "judgment": "very high", "justification": "y"}
        )


def test_tool_constrained_to_literal() -> None:
    with pytest.raises(ValidationError):
        RobAssessments.model_validate(
            {"tool": "GRADE", "assessments": [], "summary": "x"}
        )


def test_no_information_judgment_is_valid() -> None:
    """no_information is a first-class judgment — explicitly allowed for silent abstracts."""
    d = RobDomain(
        domain="Allocation concealment",
        judgment="no_information",
        justification="Abstract does not describe allocation concealment.",
    )
    assert d.judgment == "no_information"
