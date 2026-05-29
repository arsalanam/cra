"""FDA 3500A report — assembler + PDF/DOCX byte signature smoke tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from research_assistant.reports.sae_3500a import (
    SPONSOR_INPUT,
    Sae3500aData,
    assemble_3500a_data,
    build_docx,
    build_pdf,
)


def _ae(**overrides: object) -> object:
    base = dict(
        id="ae-12345678",
        term_text="severe headache",
        meddra_pt=None,
        start_date=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        end_date=None,
        severity_grade=3,
        outcome="recovering",
        is_serious=True,
        serious_reasons_json='["hospitalisation"]',
        narrative="Patient hospitalised for 2 days due to severe headache.",
        recorded_by="dr-jones",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _subject(**overrides: object) -> object:
    base = dict(subject_code="S-001", sex="F", age_at_event=42)
    base.update(overrides)
    return SimpleNamespace(**base)


def _deployment(**overrides: object) -> object:
    base = dict(name="HTN Trial", research_study_id="study-1")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_assembler_phi_minimises_to_subject_code() -> None:
    """3500A must NOT carry participant names — only the subject_code."""
    data = assemble_3500a_data(
        ae=_ae(), subject=_subject(), deployment=_deployment()
    )
    assert data.subject_code == "S-001"
    # Sponsor-input fields are marked, not fabricated
    assert data.sponsor_name == SPONSOR_INPUT
    assert data.ind_number == SPONSOR_INPUT
    assert data.investigator_name == SPONSOR_INPUT


def test_assembler_propagates_serious_reasons() -> None:
    data = assemble_3500a_data(
        ae=_ae(serious_reasons_json='["life_threatening", "hospitalisation"]'),
        subject=_subject(),
        deployment=_deployment(),
    )
    assert data.serious_reasons == ["life_threatening", "hospitalisation"]


def test_assembler_uses_research_study_name_when_provided() -> None:
    """The product-name field prefers the research-study name (e.g.
    "Drug X trial") over the clinical-store deployment name."""
    data = assemble_3500a_data(
        ae=_ae(),
        subject=_subject(),
        deployment=_deployment(),
        research_study_name="Drug X — HTN Phase III",
    )
    assert data.product_name == "Drug X — HTN Phase III"


def test_build_pdf_emits_pdf_signature() -> None:
    data = assemble_3500a_data(
        ae=_ae(), subject=_subject(), deployment=_deployment()
    )
    payload = build_pdf(data, Path("."))
    assert payload[:5] == b"%PDF-"
    assert len(payload) > 1500


def test_build_docx_emits_zip_signature() -> None:
    data = assemble_3500a_data(
        ae=_ae(), subject=_subject(), deployment=_deployment()
    )
    payload = build_docx(data, Path("."))
    assert payload[:4] == b"PK\x03\x04"
    assert len(payload) > 5000


def test_build_pdf_with_no_narrative_falls_back_to_placeholder() -> None:
    data = assemble_3500a_data(
        ae=_ae(narrative=""), subject=_subject(), deployment=_deployment()
    )
    assert data.narrative == "(narrative not provided)"
    payload = build_pdf(data, Path("."))
    assert payload[:5] == b"%PDF-"


def test_dataclass_directly_constructable() -> None:
    """Sae3500aData should be cheap to construct in tests without going
    through the assembler — every field has a default OR an obvious
    placeholder."""
    Sae3500aData(
        ae_id="x",
        generated_at=datetime.now(UTC),
        subject_code="S-001",
        age_at_event="40",
        sex="F",
        aex_term="headache",
        meddra_pt=None,
        start_date=datetime.now(UTC),
        end_date=None,
        severity_grade=2,
        outcome="recovered",
        is_serious=False,
        serious_reasons=[],
        narrative="x",
        product_name="Drug",
        deployment_name="Trial",
        research_study_id="rs",
        reporter_sub=None,
    )
