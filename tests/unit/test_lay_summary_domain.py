"""Pydantic schemas for the lay_summary specialist (P2 #2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.lay_summary import (
    AudienceProfile,
    EvidenceIntake,
    LaySummaryDocument,
    LaySummaryDraft,
    LaySummarySection,
    RecruitmentIntake,
    ResultsIntake,
)


def _audience(target_grade: int = 6, language: str = "en") -> AudienceProfile:
    return AudienceProfile(
        target_grade=target_grade,
        language=language,  # type: ignore[arg-type]
        region="",
        population_descriptor="adults",
    )


def _sections() -> list[LaySummarySection]:
    section_ids = [
        "what_this_is_about",
        "what_we_did",
        "what_we_found",
        "what_this_means_for_you",
        "next_steps",
    ]
    return [
        LaySummarySection(
            section_id=sid,  # type: ignore[arg-type]
            heading=sid.replace("_", " ").title(),
            body=f"Plain-language body for {sid}. This is short.",
        )
        for sid in section_ids
    ]


def _draft() -> LaySummaryDraft:
    return LaySummaryDraft(
        title="What this study is about",
        one_line_summary=(
            "We are testing a new pill for asthma in children to see "
            "if it helps them breathe more easily."
        ),
        sections=_sections(),
        plain_language_glossary=[],
    )


# ── AudienceProfile ───────────────────────────────────────────────────────


def test_audience_default_target_grade_is_six() -> None:
    a = AudienceProfile()
    assert a.target_grade == 6
    assert a.language == "en"
    assert a.region == ""


def test_audience_rejects_grade_outside_4_to_12() -> None:
    with pytest.raises(ValidationError):
        AudienceProfile(target_grade=3)
    with pytest.raises(ValidationError):
        AudienceProfile(target_grade=13)


def test_audience_rejects_unknown_language() -> None:
    with pytest.raises(ValidationError):
        AudienceProfile(language="zh")  # type: ignore[arg-type]


def test_audience_accepts_region_freetext() -> None:
    a = AudienceProfile(language="es", region="Mexico")
    assert a.region == "Mexico"


# ── Intake variants ───────────────────────────────────────────────────────


def test_recruitment_intake_requires_protocol_summary() -> None:
    with pytest.raises(ValidationError):
        RecruitmentIntake(
            audience=_audience(),
            protocol_summary="too short",  # min_length=20
            study_title="A Phase 2 Trial",
            sponsor="Acme",
        )


def test_evidence_intake_requires_pmid_sources() -> None:
    with pytest.raises(ValidationError):
        EvidenceIntake(
            audience=_audience(),
            pico_question="Does X reduce mortality in adults with sepsis?",
            pooled_effect_summary="Small mortality reduction.",
            pmid_sources=[],  # min_length=1
        )


def test_results_intake_accepts_valid_nct_id() -> None:
    intake = ResultsIntake(
        audience=_audience(),
        trial_title="The X Trial",
        sponsor="Acme",
        nct_id="NCT12345678",
        primary_outcome_summary="HR 0.81 (0.71, 0.93).",
        safety_summary="Most common AE: headache.",
        derived_from_ids=["sandbox:cox_ph:OS"],
    )
    assert intake.nct_id == "NCT12345678"


def test_results_intake_rejects_malformed_nct_id() -> None:
    with pytest.raises(ValidationError):
        ResultsIntake(
            audience=_audience(),
            trial_title="The X Trial",
            sponsor="Acme",
            nct_id="not-a-real-id",
            primary_outcome_summary="HR 0.81.",
            safety_summary="Headache.",
            derived_from_ids=["sandbox:cox_ph:OS"],
        )


# ── LaySummaryDraft ───────────────────────────────────────────────────────


def test_draft_requires_exactly_five_sections() -> None:
    with pytest.raises(ValidationError):
        LaySummaryDraft(
            title="x",
            one_line_summary="A one-line summary that meets the min length easily.",
            sections=_sections()[:4],  # only 4
        )


def test_draft_joined_body_concatenates_summary_plus_section_bodies() -> None:
    d = _draft()
    body = d.joined_body
    assert d.one_line_summary in body
    for sec in d.sections:
        assert sec.body in body
        # Headings are deliberately NOT included so they don't skew the
        # readability metric.
        assert sec.heading not in body


def test_draft_rejects_overlong_strapline() -> None:
    """The one-liner has a max_length so the cover-band doesn't wrap."""
    with pytest.raises(ValidationError):
        LaySummaryDraft(
            title="x",
            one_line_summary="word " * 100,  # way over 240 chars
            sections=_sections(),
        )


# ── LaySummaryDocument ────────────────────────────────────────────────────


def test_document_default_is_not_final() -> None:
    doc = LaySummaryDocument(
        source_kind="evidence_meta_analysis",
        audience=_audience(),
        draft=_draft(),
        grade_actual=6.4,
        citations=["12345678"],
    )
    assert doc.is_final is False


def test_document_grade_within_target_true_when_under_target_plus_one() -> None:
    doc = LaySummaryDocument(
        source_kind="recruitment_protocol",
        audience=_audience(target_grade=6),
        draft=_draft(),
        grade_actual=6.9,
        citations=[],
    )
    assert doc.grade_within_target is True


def test_document_grade_within_target_false_when_over_threshold() -> None:
    doc = LaySummaryDocument(
        source_kind="recruitment_protocol",
        audience=_audience(target_grade=6),
        draft=_draft(),
        grade_actual=8.5,
        citations=[],
    )
    assert doc.grade_within_target is False


def test_document_readability_attempts_capped_at_three() -> None:
    with pytest.raises(ValidationError):
        LaySummaryDocument(
            source_kind="recruitment_protocol",
            audience=_audience(),
            draft=_draft(),
            grade_actual=6.0,
            readability_attempts=4,  # over le=3
            citations=[],
        )


def test_document_round_trips_through_json() -> None:
    """Specialists persist via `model_dump_json()` — round-trip must
    survive computed_field + Literal discriminator + nested draft."""
    doc = LaySummaryDocument(
        source_kind="evidence_meta_analysis",
        audience=_audience(target_grade=8, language="es"),
        draft=_draft(),
        grade_actual=7.2,
        readability_attempts=2,
        citations=["12345678", "87654321"],
        is_final=True,
    )
    raw = doc.model_dump_json()
    back = LaySummaryDocument.model_validate_json(raw)
    assert back.grade_actual == 7.2
    assert back.readability_attempts == 2
    assert back.audience.language == "es"
    assert back.is_final is True
    assert back.draft.sections[0].section_id == "what_this_is_about"


def test_document_kind_discriminator_pinned() -> None:
    doc = LaySummaryDocument(
        source_kind="results_trial",
        audience=_audience(),
        draft=_draft(),
        grade_actual=5.5,
    )
    assert doc.kind == "lay_summary_document"
