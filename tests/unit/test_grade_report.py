"""GRADE report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.grade import (
    DowngradeReason,
    GradeDocument,
    GradeIntake,
    OutcomeAssessment,
    OutcomeSpec,
    PrismaChecklist,
    PrismaItem,
    SofRow,
    SofTable,
)
from research_assistant.reports.grade import (
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


def _intake() -> GradeIntake:
    return GradeIntake(
        research_question="SGLT2i for HF mortality",
        outcomes_to_assess=[OutcomeSpec(name="All-cause mortality")],
    )


def _assess() -> OutcomeAssessment:
    return OutcomeAssessment(
        outcome_name="All-cause mortality",
        study_design="rct",
        n_studies=8,
        n_participants=4250,
        effect_estimate="RR 0.72",
        confidence_interval="0.58 to 0.89",
        risk_of_bias=DowngradeReason(level="none", rationale="RoB 2.0: low across all studies"),
        inconsistency=DowngradeReason(level="none", rationale="I²=18%"),
        indirectness=DowngradeReason(level="none", rationale="PICO match"),
        imprecision=DowngradeReason(level="none", rationale="OIS met"),
        publication_bias=DowngradeReason(level="none", rationale="symmetric funnel"),
    )


def _sof() -> SofTable:
    return SofTable(
        research_question="SGLT2i for HF mortality",
        rows=[
            SofRow(
                outcome_name="All-cause mortality",
                n_studies=8,
                n_participants=4250,
                effect_estimate="RR 0.72",
                confidence_interval="0.58 to 0.89",
                certainty="high",
                importance="critical",
            )
        ],
    )


def _prisma() -> PrismaChecklist:
    items = [
        PrismaItem(
            section="Methods",
            item_id=str(i),
            item_text=f"PRISMA item {i}",
            reported="yes",
            location=f"§{i}",
        )
        for i in range(1, 28)
    ]
    return PrismaChecklist(review_title="SGLT2i SR/MA", items=items)


# ── Assembler ────────────────────────────────────────────────────────────


def test_assembler_returns_none_when_no_grade_turn() -> None:
    messages = [_msg(role="assistant", final_answer='{"kind": "answer"}')]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_each_stage() -> None:
    messages = [
        _msg(role="assistant", final_answer=_intake().model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=_assess().model_dump_json()),
        _msg(role="assistant", msg_id="m3", final_answer=_sof().model_dump_json()),
        _msg(role="assistant", msg_id="m4", final_answer=_prisma().model_dump_json()),
    ]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    assert out.intake is not None
    assert len(out.assessments) == 1
    assert out.sof_table is not None
    assert out.prisma_checklist is not None


def test_assembler_prefers_final_document_when_present() -> None:
    doc = GradeDocument(
        intake=_intake(),
        assessments=[_assess()],
        sof_table=_sof(),
        prisma_checklist=_prisma(),
        is_final=True,
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    assert out.document is not None
    assert out.document.is_final is True


# ── Builders ─────────────────────────────────────────────────────────────


def test_build_pdf_emits_pdf_signature() -> None:
    doc = GradeDocument(
        intake=_intake(),
        assessments=[_assess()],
        sof_table=_sof(),
        prisma_checklist=_prisma(),
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_build_docx_emits_zip_signature() -> None:
    doc = GradeDocument(
        intake=_intake(),
        assessments=[_assess()],
        sof_table=_sof(),
        prisma_checklist=_prisma(),
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    docx = build_docx(out, images_dir=Path("/tmp"))
    assert docx[:4] == b"PK\x03\x04"


def test_pdf_with_only_sof_table_renders() -> None:
    """The PRISMA checklist is optional — should not crash if absent."""
    sof = _sof()
    messages = [_msg(role="assistant", final_answer=sof.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")
