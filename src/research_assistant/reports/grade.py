"""GRADE Summary of Findings + PRISMA 2020 checklist report.

Pipeline mirrors the other report modules:

    Message rows ─▶ assemble_report_data() ─▶ GradeReportData
                                                  │
                                                  ├─▶ build_pdf()  ─▶ bytes
                                                  └─▶ build_docx() ─▶ bytes

Two artefacts in one bundle:
  1. GRADE Summary of Findings table with colour-coded certainty
     (high=green, moderate=yellow, low=orange, very_low=red).
  2. PRISMA 2020 reporting checklist (27 items × 4 columns).
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docx import Document
from pydantic import ValidationError
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from research_assistant.domain.grade import (
    PRISMA_2020_ITEMS,
    GradeDocument,
    GradeIntake,
    OutcomeAssessment,
    PrismaChecklist,
    SofTable,
)
from research_assistant.persistence.models import Message
from research_assistant.reports._shared_styles import (
    BORDER,
    DOCX_MUTED,
    LIGHT,
    make_page_decorations,
    make_pdf_styles,
)

logger = logging.getLogger(__name__)


# ── Certainty → colour mapping ──────────────────────────────────────────


_CERTAINTY_COLOURS: dict[str, tuple[colors.Color, colors.Color]] = {
    # (fill, text)
    "high":     (colors.HexColor("#d4edda"), colors.HexColor("#155724")),
    "moderate": (colors.HexColor("#fff3cd"), colors.HexColor("#856404")),
    "low":      (colors.HexColor("#ffe0b3"), colors.HexColor("#7a3e00")),
    "very_low": (colors.HexColor("#f8d7da"), colors.HexColor("#721c24")),
}


@dataclass
class GradeReportData:
    thread_id: str
    title: str
    generated_at: datetime
    intake: GradeIntake | None
    assessments: list[OutcomeAssessment]
    sof_table: SofTable | None
    prisma_checklist: PrismaChecklist | None
    document: GradeDocument | None


def _peek_kind(s: str) -> str | None:
    try:
        kind = json.loads(s).get("kind")
    except json.JSONDecodeError:
        return None
    return str(kind) if kind is not None else None


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> GradeReportData | None:
    intake: GradeIntake | None = None
    assessments: list[OutcomeAssessment] = []
    sof_table: SofTable | None = None
    prisma_checklist: PrismaChecklist | None = None
    document: GradeDocument | None = None

    for msg in messages:
        if msg.role != "assistant" or not msg.final_answer:
            continue
        kind = _peek_kind(msg.final_answer)
        try:
            if kind == "grade_intake":
                intake = GradeIntake.model_validate_json(msg.final_answer)
            elif kind == "outcome_assessment":
                assessments.append(
                    OutcomeAssessment.model_validate_json(msg.final_answer)
                )
            elif kind == "sof_table":
                sof_table = SofTable.model_validate_json(msg.final_answer)
            elif kind == "prisma_checklist":
                prisma_checklist = PrismaChecklist.model_validate_json(
                    msg.final_answer
                )
            elif kind == "grade_document":
                document = GradeDocument.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed %s turn in message %s", kind, msg.id)

    if not any((intake, assessments, sof_table, prisma_checklist, document)):
        return None

    title = "GRADE Summary of Findings + PRISMA 2020"
    if document and document.sof_table.research_question:
        title = f"GRADE — {document.sof_table.research_question[:80]}"
    elif sof_table:
        title = f"GRADE — {sof_table.research_question[:80]}"

    return GradeReportData(
        thread_id=thread_id,
        title=title,
        generated_at=datetime.now(UTC),
        intake=document.intake if document else intake,
        assessments=document.assessments if document else assessments,
        sof_table=document.sof_table if document else sof_table,
        prisma_checklist=(
            document.prisma_checklist if document else prisma_checklist
        ),
        document=document,
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _kv_table(rows: list[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> Table:
    table_rows = [
        [
            Paragraph(f"<b>{k}</b>", styles["Cell"]),
            Paragraph(v or "—", styles["Cell"]),
        ]
        for k, v in rows
    ]
    t = Table(table_rows, colWidths=[1.8 * inch, 5.4 * inch])
    t.setStyle(
        TableStyle(
            [
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def _sof_pdf_table(sof: SofTable, styles: dict[str, ParagraphStyle]) -> Table:
    columns = [
        "Outcome",
        "Studies",
        "Participants",
        "Effect (95% CI)",
        "Certainty",
        "Importance",
    ]
    table_rows: list[list[Paragraph]] = [
        [Paragraph(f"<b>{c}</b>", styles["Cell"]) for c in columns]
    ]
    for r in sof.rows:
        cert_label = r.certainty.replace("_", " ").upper()
        table_rows.append(
            [
                Paragraph(r.outcome_name, styles["Cell"]),
                Paragraph(str(r.n_studies), styles["Cell"]),
                Paragraph(str(r.n_participants), styles["Cell"]),
                Paragraph(
                    f"{r.effect_estimate}<br/>({r.confidence_interval})",
                    styles["Cell"],
                ),
                Paragraph(f"<b>{cert_label}</b>", styles["Cell"]),
                Paragraph(r.importance.replace("_", " "), styles["Cell"]),
            ]
        )
    t = Table(
        table_rows,
        colWidths=[1.6, 0.7, 0.9, 1.7, 1.0, 0.9],
        repeatRows=1,
    )
    # Column widths in inches.
    t._argW = [w * inch for w in t._argW]

    style_cmds: list[Any] = [
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    # Per-row certainty colour for the Certainty column.
    for i, r in enumerate(sof.rows, start=1):
        fill, text = _CERTAINTY_COLOURS.get(
            r.certainty,
            (colors.white, colors.black),
        )
        style_cmds.append(("BACKGROUND", (4, i), (4, i), fill))
        style_cmds.append(("TEXTCOLOR", (4, i), (4, i), text))
    t.setStyle(TableStyle(style_cmds))
    return t


def _domain_summary(d: Any) -> str:
    """One-line summary of a Downgrade / Upgrade reason."""
    return f"{d.level.replace('_', ' ')}: {d.rationale}"


# ── PDF builder ─────────────────────────────────────────────────────────


def build_pdf(data: GradeReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="GRADE + PRISMA 2020")
    doc = BaseDocTemplate(
        buf,
        pagesize=landscape(LETTER),
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.6 * inch,
        title=data.title,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decor)])

    flow: list[Any] = []
    flow.append(Paragraph("GRADE + PRISMA 2020", styles["Eyebrow"]))
    flow.append(Paragraph(data.title, styles["Title"]))
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    flow.append(Paragraph(f"Generated {ts}", styles["Body"]))
    flow.append(Spacer(1, 8))
    flow.append(
        Paragraph(
            "<i>Certainty of evidence rated per outcome per the GRADE "
            "Handbook (Schünemann et al. 2013). Every downgrade carries "
            "a numerical rationale from the underlying meta-analysis. "
            "PRISMA 2020 checklist follows Page et al. (BMJ 2021).</i>",
            styles["Body"],
        )
    )

    # ── Intake ──
    if data.intake:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("Review question + outcomes", styles["H2"]))
        flow.append(
            _kv_table(
                [
                    ("Research question", data.intake.research_question),
                    (
                        "Outcomes assessed",
                        ", ".join(
                            f"{o.name} ({o.importance.replace('_', ' ')})"
                            for o in data.intake.outcomes_to_assess
                        ),
                    ),
                    (
                        "Meta-analysis source",
                        data.intake.ma_reference or "(operator-provided)",
                    ),
                ],
                styles,
            )
        )

    # ── Summary of Findings ──
    flow.append(Spacer(1, 14))
    flow.append(Paragraph("Summary of Findings", styles["H2"]))
    if data.sof_table:
        flow.append(_sof_pdf_table(data.sof_table, styles))
    else:
        flow.append(
            Paragraph(
                "[Operator to complete — assemble the SoF after assessing "
                "each outcome]",
                styles["Body"],
            )
        )

    # ── Per-outcome detail ──
    if data.assessments:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Per-outcome detail", styles["H2"]))
        for assessment in data.assessments:
            flow.append(Spacer(1, 8))
            flow.append(
                Paragraph(
                    f"<b>{assessment.outcome_name}</b> — "
                    f"certainty: <b>{assessment.certainty.replace('_', ' ').upper()}</b>",
                    styles["H3"],
                )
            )
            rows: list[tuple[str, str]] = [
                ("Risk of bias", _domain_summary(assessment.risk_of_bias)),
                ("Inconsistency", _domain_summary(assessment.inconsistency)),
                ("Indirectness", _domain_summary(assessment.indirectness)),
                ("Imprecision", _domain_summary(assessment.imprecision)),
                ("Publication bias", _domain_summary(assessment.publication_bias)),
            ]
            if assessment.study_design == "observational":
                if assessment.large_effect is not None:
                    rows.append(
                        ("Large effect", _domain_summary(assessment.large_effect))
                    )
                if assessment.dose_response is not None:
                    rows.append(
                        ("Dose-response", _domain_summary(assessment.dose_response))
                    )
                if assessment.residual_confounding is not None:
                    rows.append(
                        (
                            "Residual confounding",
                            _domain_summary(assessment.residual_confounding),
                        )
                    )
            flow.append(_kv_table(rows, styles))

    # ── PRISMA checklist ──
    if data.prisma_checklist:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("PRISMA 2020 reporting checklist", styles["H2"]))
        items_by_id: dict[str, Any] = {
            it.item_id: it for it in data.prisma_checklist.items
        }
        prisma_rows: list[list[Paragraph]] = [
            [
                Paragraph("<b>Section</b>", styles["Cell"]),
                Paragraph("<b>#</b>", styles["Cell"]),
                Paragraph("<b>Item</b>", styles["Cell"]),
                Paragraph("<b>Reported</b>", styles["Cell"]),
                Paragraph("<b>Location</b>", styles["Cell"]),
            ]
        ]
        for section, item_id, item_text in PRISMA_2020_ITEMS:
            captured = items_by_id.get(item_id)
            reported = (
                captured.reported.replace("_", " ").title()
                if captured
                else "Not reported"
            )
            location = captured.location if captured else "—"
            prisma_rows.append(
                [
                    Paragraph(section, styles["Cell"]),
                    Paragraph(item_id, styles["Cell"]),
                    Paragraph(item_text, styles["Cell"]),
                    Paragraph(reported, styles["Cell"]),
                    Paragraph(location or "—", styles["Cell"]),
                ]
            )
        t = Table(prisma_rows, colWidths=[1.1, 0.45, 4.5, 0.9, 1.6], repeatRows=1)
        t._argW = [w * inch for w in t._argW]
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                    ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        flow.append(t)

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ────────────────────────────────────────────────────────


def build_docx(data: GradeReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    docx = Document()
    docx.core_properties.title = data.title
    docx.add_heading(data.title, level=0)
    header_runs = docx.add_paragraph(
        f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
    ).runs
    if header_runs:
        header_runs[0].font.color.rgb = DOCX_MUTED

    if data.intake:
        docx.add_heading("Review question + outcomes", level=1)
        for label, value in (
            ("Research question", data.intake.research_question),
            (
                "Outcomes assessed",
                ", ".join(
                    f"{o.name} ({o.importance.replace('_', ' ')})"
                    for o in data.intake.outcomes_to_assess
                ),
            ),
            (
                "Meta-analysis source",
                data.intake.ma_reference or "(operator-provided)",
            ),
        ):
            p = docx.add_paragraph()
            p.add_run(label + ": ").bold = True
            p.add_run(value)

    # SoF
    docx.add_heading("Summary of Findings", level=1)
    if data.sof_table:
        cols = [
            "Outcome",
            "Studies",
            "Participants",
            "Effect (95% CI)",
            "Certainty",
            "Importance",
        ]
        t = docx.add_table(rows=1 + len(data.sof_table.rows), cols=len(cols))
        t.style = "Light Grid Accent 1"
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = c
            cell.paragraphs[0].runs[0].bold = True
        for i, r in enumerate(data.sof_table.rows, start=1):
            t.cell(i, 0).text = r.outcome_name
            t.cell(i, 1).text = str(r.n_studies)
            t.cell(i, 2).text = str(r.n_participants)
            t.cell(i, 3).text = f"{r.effect_estimate} ({r.confidence_interval})"
            t.cell(i, 4).text = r.certainty.replace("_", " ").upper()
            t.cell(i, 5).text = r.importance.replace("_", " ")
    else:
        docx.add_paragraph(
            "[Operator to complete — assemble the SoF after assessing each outcome]"
        )

    # Per-outcome detail
    if data.assessments:
        docx.add_heading("Per-outcome detail", level=1)
        for assessment in data.assessments:
            docx.add_heading(
                f"{assessment.outcome_name} — certainty: "
                f"{assessment.certainty.replace('_', ' ').upper()}",
                level=2,
            )
            for label, d in (
                ("Risk of bias", assessment.risk_of_bias),
                ("Inconsistency", assessment.inconsistency),
                ("Indirectness", assessment.indirectness),
                ("Imprecision", assessment.imprecision),
                ("Publication bias", assessment.publication_bias),
            ):
                p = docx.add_paragraph()
                p.add_run(label + ": ").bold = True
                p.add_run(_domain_summary(d))
            if assessment.study_design == "observational":
                for label, u in (
                    ("Large effect", assessment.large_effect),
                    ("Dose-response", assessment.dose_response),
                    ("Residual confounding", assessment.residual_confounding),
                ):
                    if u is None:
                        continue
                    p = docx.add_paragraph()
                    p.add_run(label + ": ").bold = True
                    p.add_run(_domain_summary(u))

    # PRISMA
    if data.prisma_checklist:
        docx.add_heading("PRISMA 2020 reporting checklist", level=1)
        items_by_id = {it.item_id: it for it in data.prisma_checklist.items}
        cols = ["Section", "#", "Item", "Reported", "Location"]
        t = docx.add_table(rows=1 + len(PRISMA_2020_ITEMS), cols=len(cols))
        t.style = "Light Grid Accent 1"
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = c
            cell.paragraphs[0].runs[0].bold = True
        for i, (section, item_id, item_text) in enumerate(
            PRISMA_2020_ITEMS, start=1
        ):
            captured = items_by_id.get(item_id)
            reported = (
                captured.reported.replace("_", " ").title()
                if captured
                else "Not reported"
            )
            location = (captured.location if captured else "") or "—"
            t.cell(i, 0).text = section
            t.cell(i, 1).text = item_id
            t.cell(i, 2).text = item_text
            t.cell(i, 3).text = reported
            t.cell(i, 4).text = location

    out = io.BytesIO()
    docx.save(out)
    return out.getvalue()


__all__ = [
    "GradeReportData",
    "assemble_report_data",
    "build_docx",
    "build_pdf",
]
