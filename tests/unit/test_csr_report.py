"""CSR report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from research_assistant.reports.csr import (
    assemble_report_data,
    build_docx,
    build_pdf,
)


def _msg(
    *,
    role: str = "assistant",
    msg_id: str = "m1",
    final_answer: str | None = None,
    input_text: str | None = None,
) -> Any:
    return SimpleNamespace(id=msg_id, role=role, input_text=input_text, final_answer=final_answer)


def _intake() -> CsrIntake:
    return CsrIntake(
        study_id="ABC-2026-001",
        study_name="Drug X",
        sponsor="Acme",
        blinding="double_blind",
        lock_date="2026-05-30",
        target_jurisdictions=["FDA"],
    )


def _synopsis() -> CsrSynopsis:
    return CsrSynopsis(
        title="Drug X — Phase 2",
        sponsor="Acme",
        objectives_text="x",
        methodology_text="x",
        number_planned=128,
        number_analysed_safety=120,
        number_analysed_efficacy=115,
        primary_endpoint="Change in SBP at 12 weeks",
        primary_result_description="Significant improvement.",
        primary_result_direction="favours_intervention",
        derived_from="TLF t-tte-summary",
        safety_overview="Tolerable profile.",
        conclusions="Drug X met its primary endpoint.",
    )


def _data() -> CsrDataSections:
    return CsrDataSections(
        disposition=DispositionTable(
            rows=[DispositionRow(label="ITT", n=128, pct="100.0%")],
            derived_from="TLF t-disposition",
        ),
        demographics=DemographicsTable(
            rows=[DemographicsRow(characteristic="Age", value="mean 52.3")],
            derived_from="TLF t-demographics",
        ),
        efficacy=EfficacyResults(
            primary_endpoint_text="Time to first AE",
            primary_endpoint_derived_from="TLF t-tte-summary",
        ),
        safety=SafetyOverview(
            total_ae_events=412,
            subjects_with_any_ae=98,
            total_saes=12,
            deaths=1,
            discontinuations_due_to_ae=4,
            top_aes_text="Headache (n=42).",
            derived_from="TLF t-ae-summary",
        ),
    )


# ── Assembler ────────────────────────────────────────────────────────────


def test_assembler_returns_none_when_no_csr_turn() -> None:
    messages = [
        _msg(role="user", input_text="x"),
        _msg(role="assistant", final_answer='{"kind": "answer", "text": "hi"}'),
    ]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_each_stage() -> None:
    intake = _intake()
    synopsis = _synopsis()
    data = _data()
    messages = [
        _msg(role="assistant", final_answer=intake.model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=synopsis.model_dump_json()),
        _msg(role="assistant", msg_id="m3", final_answer=data.model_dump_json()),
    ]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    assert out.intake is not None
    assert out.synopsis is not None
    assert out.data_sections is not None


def test_assembler_prefers_final_document_when_present() -> None:
    intake = _intake()
    doc = CsrDocument(
        intake=intake,
        synopsis=_synopsis(),
        data_sections=_data(),
        is_final=True,
    )
    messages = [
        _msg(role="assistant", final_answer=intake.model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=doc.model_dump_json()),
    ]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    assert out.document is not None
    assert out.document.is_final is True


# ── Builders ─────────────────────────────────────────────────────────────


def test_build_pdf_emits_pdf_signature() -> None:
    doc = CsrDocument(
        intake=_intake(), synopsis=_synopsis(), data_sections=_data()
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_build_docx_emits_zip_signature() -> None:
    doc = CsrDocument(
        intake=_intake(), synopsis=_synopsis(), data_sections=_data()
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    docx = build_docx(out, images_dir=Path("/tmp"))
    assert docx[:4] == b"PK\x03\x04"


def test_pdf_with_only_intake_renders_skeleton() -> None:
    """The drafter may produce only an intake before the user confirms.
    The PDF should still render — all data sections show
    [Operator to complete] placeholders."""
    intake = _intake()
    messages = [_msg(role="assistant", final_answer=intake.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")
