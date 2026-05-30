"""Pydantic schemas for the irb_drafter specialist."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.irb import (
    IcfSection,
    InformedConsentForm,
    IrbDocument,
    IrbIntake,
    ProtocolSynopsis,
    SynopsisSection,
)


def _intake() -> IrbIntake:
    return IrbIntake(
        protocol_summary="Phase 2 trial of Drug X in disease Y.",
        jurisdiction="us_irb",
        language="en",
        reading_level_target=8,
        population_descriptor="adults",
    )


def _syn_section(name: str) -> SynopsisSection:
    return SynopsisSection(heading=name, body=f"Body of {name}.")


def _synopsis() -> ProtocolSynopsis:
    return ProtocolSynopsis(
        title="Drug X — Phase 2",
        sponsor="Acme",
        design_summary=_syn_section("Design summary"),
        objectives=_syn_section("Objectives"),
        endpoints=_syn_section("Endpoints"),
        methods=_syn_section("Methods"),
        statistical_considerations=_syn_section("Statistics"),
        eligibility_summary=_syn_section("Eligibility"),
        schedule_summary=_syn_section("Schedule"),
        risks_and_mitigations=_syn_section("Risks"),
    )


def _icf_sections() -> list[IcfSection]:
    section_ids = [
        "purpose",
        "procedures",
        "risks",
        "benefits",
        "alternatives",
        "confidentiality",
        "injury_and_compensation",
        "contacts",
        "voluntariness",
    ]
    return [
        IcfSection(
            section_id=sid,  # type: ignore[arg-type]
            heading=sid.replace("_", " ").title(),
            body=f"This is the {sid} section content.",
        )
        for sid in section_ids
    ]


def _icf(language: str = "en", reading_level_target: int = 8) -> InformedConsentForm:
    return InformedConsentForm(
        title="Consent to participate",
        study_name="Drug X — Phase 2",
        sponsor="Acme",
        language=language,  # type: ignore[arg-type]
        reading_level_target=reading_level_target,
        reading_level_grade_actual=7.8,
        sections=_icf_sections(),
    )


# ── Intake ──────────────────────────────────────────────────────────────


def test_intake_kind_discriminator() -> None:
    intake = _intake()
    assert intake.kind == "irb_intake"


def test_intake_rejects_unsupported_language() -> None:
    with pytest.raises(ValidationError):
        IrbIntake(
            protocol_summary="x",
            jurisdiction="us_irb",
            language="zh",  # type: ignore[arg-type]
        )


def test_intake_reading_level_target_clamped_to_4_12() -> None:
    with pytest.raises(ValidationError):
        IrbIntake(
            protocol_summary="x",
            jurisdiction="us_irb",
            language="en",
            reading_level_target=14,
        )


# ── Protocol synopsis ───────────────────────────────────────────────────


def test_protocol_synopsis_requires_all_eight_sections() -> None:
    with pytest.raises(ValidationError, match=r"Field required"):
        ProtocolSynopsis(  # type: ignore[call-arg]
            title="x",
            sponsor="x",
            design_summary=_syn_section("design"),
            objectives=_syn_section("objectives"),
            # missing: endpoints, methods, statistical_considerations,
            # eligibility_summary, schedule_summary, risks_and_mitigations
        )


# ── Informed consent form ───────────────────────────────────────────────


def test_icf_requires_all_nine_required_element_sections() -> None:
    """21 CFR §50.25(a) A-I. The schema enforces ≥9 sections by length.
    A schema-conformant assembly always includes all 9 unique
    section_ids."""
    icf = _icf()
    assert len(icf.sections) == 9
    section_ids = {s.section_id for s in icf.sections}
    expected = {
        "purpose",
        "procedures",
        "risks",
        "benefits",
        "alternatives",
        "confidentiality",
        "injury_and_compensation",
        "contacts",
        "voluntariness",
    }
    assert section_ids == expected


def test_icf_rejects_too_few_sections() -> None:
    with pytest.raises(ValidationError, match=r"at least 9 items"):
        InformedConsentForm(
            title="x",
            study_name="x",
            sponsor="x",
            language="en",
            reading_level_target=8,
            reading_level_grade_actual=7.0,
            sections=_icf_sections()[:5],
        )


def test_icf_rejects_unsupported_language() -> None:
    with pytest.raises(ValidationError):
        InformedConsentForm(
            title="x",
            study_name="x",
            sponsor="x",
            language="zh",  # type: ignore[arg-type]
            reading_level_target=8,
            reading_level_grade_actual=7.0,
            sections=_icf_sections(),
        )


def test_icf_default_signature_block_uses_voluntariness_language() -> None:
    icf = _icf()
    assert "voluntarily" in icf.signature_block.lower()
    assert "agree" in icf.signature_block.lower()


def test_icf_section_id_constrained_to_required_elements() -> None:
    with pytest.raises(ValidationError):
        IcfSection(
            section_id="randomization",  # type: ignore[arg-type] — not in the §50.25(a) list
            heading="x",
            body="x",
        )


# ── IRB document ────────────────────────────────────────────────────────


def test_irb_document_kind_discriminator() -> None:
    doc = IrbDocument(intake=_intake(), synopsis=_synopsis(), icf=_icf())
    assert doc.kind == "irb_document"
    assert doc.is_final is False


def test_irb_document_roundtrips_via_json() -> None:
    doc = IrbDocument(intake=_intake(), synopsis=_synopsis(), icf=_icf(), is_final=True)
    json_str = doc.model_dump_json()
    restored = IrbDocument.model_validate_json(json_str)
    assert restored.is_final is True
    assert restored.icf.language == "en"
    assert len(restored.icf.sections) == 9
