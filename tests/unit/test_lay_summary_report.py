"""Lay-summary report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.lay_summary import (
    AudienceProfile,
    EvidenceIntake,
    LaySummaryDocument,
    LaySummaryDraft,
    LaySummarySection,
)
from research_assistant.reports.lay_summary import (
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
    return SimpleNamespace(
        id=msg_id,
        role=role,
        input_text=input_text,
        final_answer=final_answer,
    )


def _audience(target_grade: int = 6) -> AudienceProfile:
    return AudienceProfile(
        target_grade=target_grade,
        language="en",
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
            body=f"Plain-language body for {sid}. Short and friendly.",
        )
        for sid in section_ids
    ]


def _draft(glossary: list[dict[str, str]] | None = None) -> LaySummaryDraft:
    return LaySummaryDraft(
        title="What this trial is about",
        one_line_summary=(
            "We are testing a new pill for asthma in children to see "
            "if it helps them breathe more easily."
        ),
        sections=_sections(),
        plain_language_glossary=glossary or [],
    )


def _document(
    source_kind: str = "evidence_meta_analysis",
    grade_actual: float = 6.4,
    citations: list[str] | None = None,
    is_final: bool = False,
) -> LaySummaryDocument:
    return LaySummaryDocument(
        source_kind=source_kind,  # type: ignore[arg-type]
        audience=_audience(),
        draft=_draft(),
        grade_actual=grade_actual,
        citations=citations or [],
        is_final=is_final,
    )


# ── Assembler ────────────────────────────────────────────────────────────


def test_assembler_returns_none_when_no_lay_summary_turn() -> None:
    messages = [
        _msg(role="user", input_text="hi"),
        _msg(role="assistant", final_answer='{"kind": "answer", "text": "x"}'),
    ]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_intake_only() -> None:
    intake = EvidenceIntake(
        audience=_audience(),
        pico_question="Does X reduce mortality in adults with sepsis?",
        pooled_effect_summary="Mortality reduction observed.",
        pmid_sources=["12345678"],
    )
    data = assemble_report_data(
        "thread-1",
        [_msg(role="assistant", final_answer=intake.model_dump_json())],
    )
    assert data is not None
    assert data.intake is not None
    assert data.draft is None
    assert data.document is None


def test_assembler_prefers_document_draft_over_standalone_draft() -> None:
    draft = _draft()
    doc = _document()
    data = assemble_report_data(
        "thread-1",
        [
            _msg(role="assistant", final_answer=draft.model_dump_json()),
            _msg(role="assistant", msg_id="m2", final_answer=doc.model_dump_json()),
        ],
    )
    assert data is not None
    assert data.draft is not None
    # The document's embedded draft is used in preference.
    assert data.draft.title == doc.draft.title


# ── Builders ─────────────────────────────────────────────────────────────


def test_build_pdf_emits_pdf_signature() -> None:
    doc = _document(citations=["12345678"], is_final=True)
    data = assemble_report_data(
        "thread-1",
        [_msg(role="assistant", final_answer=doc.model_dump_json())],
    )
    assert data is not None
    pdf = build_pdf(data, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_build_docx_emits_zip_signature() -> None:
    doc = _document(citations=["12345678"], is_final=True)
    data = assemble_report_data(
        "thread-1",
        [_msg(role="assistant", final_answer=doc.model_dump_json())],
    )
    assert data is not None
    docx = build_docx(data, images_dir=Path("/tmp"))
    assert docx[:4] == b"PK\x03\x04"


def test_pdf_grade_above_target_carries_data() -> None:
    """The report data carries the over-target grade so the renderer
    can warn — pin the round-trip even with the warning badge active."""
    doc = _document(grade_actual=9.8)
    data = assemble_report_data(
        "thread-1",
        [_msg(role="assistant", final_answer=doc.model_dump_json())],
    )
    assert data is not None
    assert data.document is not None
    assert data.document.grade_actual == 9.8
    assert data.document.grade_within_target is False
    # PDF still builds even when over-target.
    pdf = build_pdf(data, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_pdf_includes_glossary_when_present() -> None:
    """Glossary should round-trip into the renderable data path."""
    draft = _draft(
        glossary=[
            {"term": "placebo", "gloss": "a pill with no active medicine"},
            {"term": "randomised", "gloss": "chosen by chance"},
        ]
    )
    doc = LaySummaryDocument(
        source_kind="recruitment_protocol",
        audience=_audience(),
        draft=draft,
        grade_actual=6.0,
    )
    data = assemble_report_data(
        "thread-1",
        [_msg(role="assistant", final_answer=doc.model_dump_json())],
    )
    assert data is not None
    assert data.draft is not None
    assert len(data.draft.plain_language_glossary) == 2
    pdf = build_pdf(data, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")
