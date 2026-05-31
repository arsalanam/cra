"""IPD MA report — side-by-side one-stage + two-stage + subgroup forest + PDF/DOCX."""

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

from research_assistant.domain.ipd import (
    IpdDocument,
    IpdMainResults,
    IpdPooledEffect,
    IpdSubgroupResults,
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


@dataclass
class IpdReportData:
    thread_id: str
    title: str
    generated_at: datetime
    document: IpdDocument | None
    main_results: IpdMainResults | None
    subgroup_results: list[IpdSubgroupResults]


def _peek_kind(s: str) -> str | None:
    try:
        return str(json.loads(s).get("kind"))
    except json.JSONDecodeError:
        return None


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> IpdReportData | None:
    main_results: IpdMainResults | None = None
    subgroup_results: list[IpdSubgroupResults] = []
    document: IpdDocument | None = None
    for msg in messages:
        if msg.role != "assistant" or not msg.final_answer:
            continue
        kind = _peek_kind(msg.final_answer)
        try:
            if kind == "ipd_main_results":
                main_results = IpdMainResults.model_validate_json(msg.final_answer)
            elif kind == "ipd_subgroup_results":
                subgroup_results.append(IpdSubgroupResults.model_validate_json(msg.final_answer))
            elif kind == "ipd_document":
                document = IpdDocument.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed %s in message %s", kind, msg.id)
    if document is not None:
        main_results = document.main_results
        subgroup_results = list(document.subgroup_results)
    if main_results is None and document is None:
        return None
    title = (
        f"IPD MA — {document.intake.primary_endpoint[:60]}"
        if document
        else "Individual patient data meta-analysis"
    )
    return IpdReportData(
        thread_id=thread_id,
        title=title,
        generated_at=datetime.now(UTC),
        document=document,
        main_results=main_results,
        subgroup_results=subgroup_results,
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _fmt_ci(value: float | None, lo: float | None, hi: float | None) -> str:
    if value is None or lo is None or hi is None:
        return "—"
    return f"{value:.3f} ({lo:.3f}–{hi:.3f})"


def _fmt_p(p: float | None) -> str:
    if p is None:
        return "—"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _fmt_i2(i2: float | None) -> str:
    return "—" if i2 is None else f"{i2:.1f}%"


def _pooled_row(label: str, pe: IpdPooledEffect, styles: dict[str, ParagraphStyle]) -> list[Any]:
    return [
        Paragraph(label, styles["Cell"]),
        Paragraph(
            _fmt_ci(pe.effect, pe.ci_lower, pe.ci_upper),
            styles["Cell"],
        ),
        Paragraph(_fmt_p(pe.p_value), styles["Cell"]),
        Paragraph(str(pe.n_trials), styles["Cell"]),
        Paragraph(str(pe.n_subjects), styles["Cell"]),
        Paragraph(_fmt_i2(pe.i_squared), styles["Cell"]),
        Paragraph(
            f"{pe.tau_squared:.3f}" if pe.tau_squared is not None else "—",
            styles["Cell"],
        ),
        Paragraph(pe.method, styles["Cell"]),
    ]


def _main_pdf(results: IpdMainResults, styles: dict[str, ParagraphStyle]) -> Table:
    headers = [
        "Stage",
        "Effect (95% CI)",
        "p",
        "N trials",
        "N subjects",
        "I²",
        "τ²",
        "Method",
    ]
    header_row = [Paragraph(f"<b>{h}</b>", styles["Cell"]) for h in headers]
    body: list[list[Any]] = [
        header_row,
        _pooled_row("One-stage", results.one_stage, styles),
        _pooled_row("Two-stage", results.two_stage, styles),
    ]
    t = Table(
        body,
        colWidths=[0.85, 1.7, 0.65, 0.7, 0.8, 0.6, 0.6, 2.5],
        repeatRows=1,
    )
    t._argW = [w * inch for w in t._argW]
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def _per_trial_pdf(results: IpdMainResults, styles: dict[str, ParagraphStyle]) -> Table:
    headers = ["Trial", "N", "Effect (95% CI)", "SE"]
    body: list[list[Any]] = [[Paragraph(f"<b>{h}</b>", styles["Cell"]) for h in headers]]
    for t in results.per_trial:
        body.append(
            [
                Paragraph(t.trial_id, styles["Cell"]),
                Paragraph(str(t.n_subjects), styles["Cell"]),
                Paragraph(
                    _fmt_ci(t.effect, t.ci_lower, t.ci_upper),
                    styles["Cell"],
                ),
                Paragraph(f"{t.se:.3f}", styles["Cell"]),
            ]
        )
    tbl = Table(body, colWidths=[2.0, 0.6, 1.7, 0.7], repeatRows=1)
    tbl._argW = [w * inch for w in tbl._argW]
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return tbl


def _subgroup_pdf(sg: IpdSubgroupResults, styles: dict[str, ParagraphStyle]) -> Table:
    headers = ["Level", "N trials", "N subjects", "Effect (95% CI)"]
    body: list[list[Any]] = [[Paragraph(f"<b>{h}</b>", styles["Cell"]) for h in headers]]
    for lvl in sg.levels:
        if lvl.effect is not None:
            eff = _fmt_ci(lvl.effect, lvl.ci_lower, lvl.ci_upper)
        else:
            eff = lvl.skip_reason or "—"
        body.append(
            [
                Paragraph(lvl.level_label, styles["Cell"]),
                Paragraph(str(lvl.n_trials), styles["Cell"]),
                Paragraph(str(lvl.n_subjects), styles["Cell"]),
                Paragraph(eff, styles["Cell"]),
            ]
        )
    tbl = Table(body, colWidths=[1.6, 0.8, 1.0, 1.9], repeatRows=1)
    tbl._argW = [w * inch for w in tbl._argW]
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return tbl


# ── PDF builder ─────────────────────────────────────────────────────────


def build_pdf(data: IpdReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    if data.main_results is None:
        return b""
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="IPD meta-analysis")
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

    main = data.main_results
    flow: list[Any] = []
    flow.append(Paragraph("INDIVIDUAL PATIENT DATA META-ANALYSIS", styles["Eyebrow"]))
    flow.append(Paragraph(data.title, styles["Title"]))
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    flow.append(Paragraph(f"Generated {ts}", styles["Body"]))
    flow.append(Spacer(1, 8))
    flow.append(
        Paragraph(
            f"<i>Effect measure: <b>{main.effect_measure}</b>. "
            f"Trials pooled: <b>{main.one_stage.n_trials}</b>. "
            f"Subjects: <b>{main.one_stage.n_subjects}</b>.</i>",
            styles["Body"],
        )
    )

    if data.document and data.document.intake:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("Research question + endpoint", styles["H2"]))
        flow.append(
            Paragraph(
                f"<b>Question:</b> {data.document.intake.research_question}<br/>"
                f"<b>Primary endpoint:</b> {data.document.intake.primary_endpoint}",
                styles["Body"],
            )
        )

    flow.append(Spacer(1, 14))
    flow.append(Paragraph("Main results — one-stage vs two-stage", styles["H2"]))
    flow.append(
        Paragraph(
            "<i>One-stage = single multilevel model. Two-stage = per-trial "
            "estimates + DerSimonian-Laird pool. Large discrepancy between "
            "the two is a model-misspecification signal.</i>",
            styles["Body"],
        )
    )
    flow.append(_main_pdf(main, styles))
    if main.discrepancy_note:
        flow.append(Spacer(1, 6))
        flow.append(
            Paragraph(
                f"<b>Discrepancy:</b> {main.discrepancy_note}",
                styles["Body"],
            )
        )

    flow.append(Spacer(1, 14))
    flow.append(Paragraph("Per-trial estimates", styles["H2"]))
    flow.append(_per_trial_pdf(main, styles))

    for sg in data.subgroup_results:
        flow.append(Spacer(1, 14))
        flow.append(
            Paragraph(
                f"Subgroup × treatment — {sg.subgroup_variable}",
                styles["H2"],
            )
        )
        if sg.interaction_p_value is not None:
            flow.append(
                Paragraph(
                    f"<b>Interaction p-value:</b> {_fmt_p(sg.interaction_p_value)}",
                    styles["Body"],
                )
            )
        flow.append(_subgroup_pdf(sg, styles))
        if sg.notes:
            flow.append(Paragraph(f"<i>{sg.notes}</i>", styles["Body"]))

    if data.document:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Interpretation", styles["H2"]))
        flow.append(
            Paragraph(
                f"<b>Direction:</b> {data.document.direction.replace('_', ' ')}",
                styles["Body"],
            )
        )
        flow.append(Paragraph(data.document.interpretation, styles["Body"]))
        if data.document.caveats:
            flow.append(Spacer(1, 8))
            flow.append(Paragraph("Caveats", styles["H2"]))
            for c in data.document.caveats:
                flow.append(Paragraph(f"• {c}", styles["Body"]))
        if data.document.studies_included:
            flow.append(Spacer(1, 8))
            flow.append(
                Paragraph(
                    f"<i>Trials included: {', '.join(data.document.studies_included[:30])}"
                    f"{' …' if len(data.document.studies_included) > 30 else ''}</i>",
                    styles["Body"],
                )
            )

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ────────────────────────────────────────────────────────


def build_docx(data: IpdReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    if data.main_results is None:
        return b""
    docx = Document()
    docx.core_properties.title = data.title
    docx.add_heading(data.title, level=0)
    runs = docx.add_paragraph(f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}").runs
    if runs:
        runs[0].font.color.rgb = DOCX_MUTED

    main = data.main_results
    p = docx.add_paragraph()
    p.add_run("Effect measure: ").bold = True
    p.add_run(main.effect_measure)
    p2 = docx.add_paragraph()
    p2.add_run("Trials: ").bold = True
    p2.add_run(f"{main.one_stage.n_trials} trials, {main.one_stage.n_subjects} subjects")

    if data.document and data.document.intake:
        docx.add_heading("Research question + endpoint", level=1)
        docx.add_paragraph(
            f"{data.document.intake.research_question}\n"
            f"Primary endpoint: {data.document.intake.primary_endpoint}"
        )

    docx.add_heading("Main results — one-stage vs two-stage", level=1)
    cols = [
        "Stage",
        "Effect (95% CI)",
        "p",
        "N trials",
        "N subjects",
        "I²",
        "τ²",
        "Method",
    ]
    t = docx.add_table(rows=3, cols=len(cols))
    t.style = "Light Grid Accent 1"
    for j, c in enumerate(cols):
        cell = t.cell(0, j)
        cell.text = c
        cell.paragraphs[0].runs[0].bold = True
    for row_idx, (label, pe) in enumerate(
        (("One-stage", main.one_stage), ("Two-stage", main.two_stage)), start=1
    ):
        t.cell(row_idx, 0).text = label
        t.cell(row_idx, 1).text = _fmt_ci(pe.effect, pe.ci_lower, pe.ci_upper)
        t.cell(row_idx, 2).text = _fmt_p(pe.p_value)
        t.cell(row_idx, 3).text = str(pe.n_trials)
        t.cell(row_idx, 4).text = str(pe.n_subjects)
        t.cell(row_idx, 5).text = _fmt_i2(pe.i_squared)
        t.cell(row_idx, 6).text = f"{pe.tau_squared:.3f}" if pe.tau_squared is not None else "—"
        t.cell(row_idx, 7).text = pe.method
    if main.discrepancy_note:
        p3 = docx.add_paragraph()
        p3.add_run("Discrepancy: ").bold = True
        p3.add_run(main.discrepancy_note)

    docx.add_heading("Per-trial estimates", level=1)
    cols = ["Trial", "N", "Effect (95% CI)", "SE"]
    tt = docx.add_table(rows=1 + len(main.per_trial), cols=len(cols))
    tt.style = "Light Grid Accent 1"
    for j, c in enumerate(cols):
        cell = tt.cell(0, j)
        cell.text = c
        cell.paragraphs[0].runs[0].bold = True
    for i, t_row in enumerate(main.per_trial, start=1):
        tt.cell(i, 0).text = t_row.trial_id
        tt.cell(i, 1).text = str(t_row.n_subjects)
        tt.cell(i, 2).text = _fmt_ci(t_row.effect, t_row.ci_lower, t_row.ci_upper)
        tt.cell(i, 3).text = f"{t_row.se:.3f}"

    for sg in data.subgroup_results:
        docx.add_heading(f"Subgroup × treatment — {sg.subgroup_variable}", level=1)
        if sg.interaction_p_value is not None:
            p4 = docx.add_paragraph()
            p4.add_run("Interaction p-value: ").bold = True
            p4.add_run(_fmt_p(sg.interaction_p_value))
        sg_cols = ["Level", "N trials", "N subjects", "Effect (95% CI)"]
        st = docx.add_table(rows=1 + len(sg.levels), cols=len(sg_cols))
        st.style = "Light Grid Accent 1"
        for j, c in enumerate(sg_cols):
            cell = st.cell(0, j)
            cell.text = c
            cell.paragraphs[0].runs[0].bold = True
        for i, lvl in enumerate(sg.levels, start=1):
            st.cell(i, 0).text = lvl.level_label
            st.cell(i, 1).text = str(lvl.n_trials)
            st.cell(i, 2).text = str(lvl.n_subjects)
            if lvl.effect is not None:
                st.cell(i, 3).text = _fmt_ci(lvl.effect, lvl.ci_lower, lvl.ci_upper)
            else:
                st.cell(i, 3).text = lvl.skip_reason or "—"

    if data.document:
        docx.add_heading("Interpretation", level=1)
        p5 = docx.add_paragraph()
        p5.add_run("Direction: ").bold = True
        p5.add_run(data.document.direction.replace("_", " "))
        docx.add_paragraph(data.document.interpretation)
        if data.document.caveats:
            docx.add_heading("Caveats", level=1)
            for c in data.document.caveats:
                docx.add_paragraph(c, style="List Bullet")

    out = io.BytesIO()
    docx.save(out)
    return out.getvalue()


__all__ = ["IpdReportData", "assemble_report_data", "build_docx", "build_pdf"]
