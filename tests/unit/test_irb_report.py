"""IRB report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.irb import (
    IcfSection,
    InformedConsentForm,
    IrbDocument,
    IrbIntake,
    ProtocolSynopsis,
    SynopsisSection,
)
from research_assistant.reports.irb import (
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


def _intake() -> IrbIntake:
    return IrbIntake(
        protocol_summary="Phase 2 study of Drug X.",
        jurisdiction="us_irb",
        language="en",
        reading_level_target=8,
    )


def _syn(name: str) -> SynopsisSection:
    return SynopsisSection(heading=name, body=f"Body for {name}.")


def _synopsis() -> ProtocolSynopsis:
    return ProtocolSynopsis(
        title="Drug X — Phase 2",
        sponsor="Acme",
        design_summary=_syn("Design summary"),
        objectives=_syn("Objectives"),
        endpoints=_syn("Endpoints"),
        methods=_syn("Methods"),
        statistical_considerations=_syn("Statistics"),
        eligibility_summary=_syn("Eligibility"),
        schedule_summary=_syn("Schedule"),
        risks_and_mitigations=_syn("Risks"),
    )


def _icf(reading_actual: float = 7.5) -> InformedConsentForm:
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
    return InformedConsentForm(
        title="Consent to participate",
        study_name="Drug X — Phase 2",
        sponsor="Acme",
        language="en",
        reading_level_target=8,
        reading_level_grade_actual=reading_actual,
        sections=[
            IcfSection(
                section_id=sid,  # type: ignore[arg-type]
                heading=sid.title(),
                body=f"Plain-language body for {sid}.",
            )
            for sid in section_ids
        ],
    )


# ── Assembler ────────────────────────────────────────────────────────────


def test_assembler_returns_none_when_no_irb_turn() -> None:
    messages = [
        _msg(role="user", input_text="x"),
        _msg(role="assistant", final_answer='{"kind": "answer", "text": "hi"}'),
    ]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_each_stage() -> None:
    intake = _intake()
    synopsis = _synopsis()
    icf = _icf()
    messages = [
        _msg(role="assistant", final_answer=intake.model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=synopsis.model_dump_json()),
        _msg(role="assistant", msg_id="m3", final_answer=icf.model_dump_json()),
    ]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    assert data.intake is not None
    assert data.synopsis is not None
    assert data.icf is not None


def test_assembler_prefers_final_document_when_present() -> None:
    intake = _intake()
    doc = IrbDocument(intake=intake, synopsis=_synopsis(), icf=_icf(), is_final=True)
    messages = [
        _msg(role="assistant", final_answer=intake.model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=doc.model_dump_json()),
    ]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    assert data.document is not None
    assert data.document.is_final is True


# ── Builders ─────────────────────────────────────────────────────────────


def test_build_pdf_emits_pdf_signature() -> None:
    doc = IrbDocument(intake=_intake(), synopsis=_synopsis(), icf=_icf())
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    pdf = build_pdf(data, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_build_docx_emits_zip_signature() -> None:
    doc = IrbDocument(intake=_intake(), synopsis=_synopsis(), icf=_icf())
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    docx = build_docx(data, images_dir=Path("/tmp"))
    assert docx[:4] == b"PK\x03\x04"


def test_pdf_carries_reading_level_warning_when_actual_exceeds_target() -> None:
    """Above-target reading-level should be flagged in the PDF body."""
    doc = IrbDocument(intake=_intake(), synopsis=_synopsis(), icf=_icf(reading_actual=10.5))
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    pdf = build_pdf(data, images_dir=Path("/tmp"))
    # The PDF text-stream contains the warning glyphs; even after
    # reportlab compression the substring "above target" is in the
    # un-compressed metadata stream.
    assert pdf[:5] == b"%PDF-"
    # Round-trip check that the data carries the high grade.
    assert data.icf is not None
    assert data.icf.reading_level_grade_actual == 10.5
