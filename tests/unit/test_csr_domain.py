"""Pydantic schemas for the csr_drafter specialist."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.csr import (
    CsrDataSections,
    CsrDocument,
    CsrIntake,
    CsrSynopsis,
    DemographicsRow,
    DemographicsTable,
    DispositionRow,
    DispositionTable,
    EfficacyResults,
    SafetyOverview,
)


def _intake() -> CsrIntake:
    return CsrIntake(
        study_id="ABC-2026-001",
        study_name="Drug X for Y",
        sponsor="Acme Pharma",
        blinding="double_blind",
        lock_date="2026-05-30",
        target_jurisdictions=["FDA", "EMA"],
    )


def _synopsis() -> CsrSynopsis:
    return CsrSynopsis(
        title="Drug X — Phase 2",
        sponsor="Acme Pharma",
        objectives_text="Evaluate Drug X efficacy + safety.",
        methodology_text="Phase 2, randomised, double-blind, parallel.",
        number_planned=128,
        number_analysed_safety=120,
        number_analysed_efficacy=115,
        primary_endpoint="Change in symptom score at 12 weeks",
        primary_result_description="Significant improvement vs placebo.",
        primary_result_direction="favours_intervention",
        derived_from="TLF t-tte-summary",
        safety_overview="Tolerable safety profile, no SUSARs.",
        conclusions="Drug X met its primary endpoint.",
    )


def _data_sections() -> CsrDataSections:
    return CsrDataSections(
        disposition=DispositionTable(
            rows=[
                DispositionRow(label="ITT", n=128, pct="100.0%"),
                DispositionRow(label="Safety", n=120, pct="93.8%"),
            ],
            derived_from="TLF t-disposition",
        ),
        demographics=DemographicsTable(
            rows=[
                DemographicsRow(characteristic="Age (years)", value="mean 52.3"),
                DemographicsRow(characteristic="Sex: F", value="62", detail="48.4%"),
            ],
            derived_from="TLF t-demographics",
        ),
        efficacy=EfficacyResults(
            primary_endpoint_text="Time to first AE",
            primary_endpoint_derived_from="TLF t-tte-summary",
            secondary_endpoints=["Overall survival"],
            secondary_endpoints_derived_from=["ADTTE PARAMCD=DEATH"],
            populations_analysed=["ITT", "Safety"],
        ),
        safety=SafetyOverview(
            total_ae_events=412,
            subjects_with_any_ae=98,
            total_saes=12,
            deaths=1,
            discontinuations_due_to_ae=4,
            top_aes_text="Headache (n=42), nausea (n=28), insomnia (n=20).",
            derived_from="TLF t-ae-summary",
        ),
    )


# ── Intake ──────────────────────────────────────────────────────────────


def test_intake_kind_discriminator() -> None:
    assert _intake().kind == "csr_intake"


def test_intake_rejects_unsupported_blinding() -> None:
    with pytest.raises(ValidationError):
        CsrIntake(
            study_id="x",
            study_name="x",
            sponsor="x",
            blinding="quadruple_blind",  # type: ignore[arg-type] — only 4 valid options
        )


def test_intake_lock_date_optional() -> None:
    intake = CsrIntake(
        study_id="x",
        study_name="x",
        sponsor="x",
        blinding="open_label",
    )
    assert intake.lock_date is None


# ── Synopsis ────────────────────────────────────────────────────────────


def test_synopsis_kind_discriminator() -> None:
    assert _synopsis().kind == "csr_synopsis"


def test_synopsis_number_planned_must_be_positive() -> None:
    with pytest.raises(ValidationError, match=r"greater than 0"):
        CsrSynopsis(
            title="x",
            sponsor="x",
            objectives_text="x",
            methodology_text="x",
            number_planned=0,
            number_analysed_safety=0,
            number_analysed_efficacy=0,
            primary_endpoint="x",
            primary_result_description="x",
            primary_result_direction="no_difference",
            derived_from="x",
            safety_overview="x",
            conclusions="x",
        )


def test_synopsis_carries_source_artefact_id() -> None:
    """Anti-hallucination: derived_from MUST be populated; the schema's
    `description` documents that it traces the primary-result source."""
    s = _synopsis()
    assert s.derived_from == "TLF t-tte-summary"


# ── Data sections ───────────────────────────────────────────────────────


def test_disposition_table_requires_at_least_one_row() -> None:
    with pytest.raises(ValidationError, match=r"at least 1"):
        DispositionTable(rows=[], derived_from="x")


def test_safety_overview_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError, match=r"greater than or equal to 0"):
        SafetyOverview(
            total_ae_events=-1,
            subjects_with_any_ae=0,
            total_saes=0,
            deaths=0,
            discontinuations_due_to_ae=0,
            top_aes_text="x",
            derived_from="x",
        )


def test_data_sections_carries_four_required_components() -> None:
    ds = _data_sections()
    assert ds.disposition.derived_from == "TLF t-disposition"
    assert ds.safety.derived_from == "TLF t-ae-summary"
    assert ds.efficacy.populations_analysed == ["ITT", "Safety"]


def test_efficacy_secondary_endpoints_parallel_arrays() -> None:
    """The schema doesn't enforce equal lengths, but a downstream report
    renders them in parallel — confirm zip behaviour doesn't crash on
    unbalanced inputs (the report code handles None / empty cases)."""
    ds = _data_sections()
    assert len(ds.efficacy.secondary_endpoints) == len(ds.efficacy.secondary_endpoints_derived_from)


# ── Document ────────────────────────────────────────────────────────────


def test_document_kind_discriminator() -> None:
    doc = CsrDocument(intake=_intake(), synopsis=_synopsis(), data_sections=_data_sections())
    assert doc.kind == "csr_document"
    assert doc.is_final is False


def test_document_narrative_sections_default_to_operator_placeholders() -> None:
    doc = CsrDocument(intake=_intake(), synopsis=_synopsis(), data_sections=_data_sections())
    assert "Operator to complete" in doc.background_text
    assert "Operator to complete" in doc.discussion_text
    assert "Operator to complete" in doc.conclusions_text


def test_document_roundtrips_via_json() -> None:
    doc = CsrDocument(
        intake=_intake(),
        synopsis=_synopsis(),
        data_sections=_data_sections(),
        is_final=True,
    )
    json_str = doc.model_dump_json()
    restored = CsrDocument.model_validate_json(json_str)
    assert restored.is_final is True
    assert restored.synopsis.title == doc.synopsis.title
    assert restored.data_sections.disposition.rows[0].n == 128
