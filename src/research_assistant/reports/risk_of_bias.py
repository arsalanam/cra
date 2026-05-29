"""Risk-of-bias report — assemble a thread's most-recent RobSummary into a
downloadable PDF or DOCX with embedded stacked-bar plot, per-study
assessments, narrative, and sensitivity recommendations.

Pipeline mirrors `reports/meta_analysis.py`:

    Message rows ─▶ assemble_report_data() ─▶ RobReportData
                                                    │
                                                    ├─▶ build_pdf()  ─▶ bytes
                                                    └─▶ build_docx() ─▶ bytes

The RobSummary carries the full per-study assessments through from the
upstream RobAssessments turn, so we only need the most recent rob_summary
in the thread.
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
from docx.shared import Inches, Pt
from pydantic import ValidationError
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import (
    Image as RLImage,
)

from research_assistant.domain.risk_of_bias import (
    DomainDistribution,
    RobSummary,
    RobTool,
    StudyRobAssessment,
)
from research_assistant.persistence.models import Message
from research_assistant.reports._shared_styles import (
    BORDER,
    DOCX_MUTED,
    DOCX_NAVY,
    DOCX_TEAL,
    LIGHT,
    NAVY,
    docx_set_heading_color,
    make_page_decorations,
    make_pdf_styles,
)

logger = logging.getLogger(__name__)


# RoB judgment colour map (PDF + DOCX share these). Matches the UI pill
# colours so reports look familiar to anyone who's seen the cards.
_JUDGMENT_COLORS = {
    "low": colors.HexColor("#2A9D8F"),
    "some_concerns": colors.HexColor("#D9A84A"),
    "high": colors.HexColor("#C53030"),
    "no_information": colors.HexColor("#6B7280"),
}


# ── Data assembly ────────────────────────────────────────────────────────


@dataclass
class RobReportData:
    """Everything the renderers need, pre-shaped from one thread."""

    thread_id: str
    research_question: str
    generated_at: datetime
    tool: RobTool
    assessments: list[StudyRobAssessment]
    domain_distribution: list[DomainDistribution]
    summary_plot_image: str | None
    narrative: str
    sensitivity_recommendations: list[str]
    high_rob_pmids: list[str]
    is_final: bool


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> RobReportData | None:
    """Pull the latest RobSummary from the thread.

    Returns None if no rob_summary turn has been emitted yet.
    """
    research_question = ""
    summary: RobSummary | None = None

    for msg in messages:
        if msg.role == "user" and not research_question and msg.input_text:
            research_question = msg.input_text.strip()
            continue
        if msg.role != "assistant" or not msg.final_answer:
            continue
        if _peek_kind(msg.final_answer) != "rob_summary":
            continue
        try:
            summary = RobSummary.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed RobSummary in message %s", msg.id)

    if summary is None:
        return None

    return RobReportData(
        thread_id=thread_id,
        research_question=research_question or "(Research question not captured.)",
        generated_at=datetime.now(UTC),
        tool=summary.tool,
        assessments=list(summary.assessments),
        domain_distribution=list(summary.domain_distribution),
        summary_plot_image=summary.summary_plot_image,
        narrative=summary.narrative,
        sensitivity_recommendations=list(summary.sensitivity_recommendations),
        high_rob_pmids=list(summary.high_rob_pmids),
        is_final=summary.is_final,
    )


def _peek_kind(final_answer_json: str) -> str | None:
    try:
        obj = json.loads(final_answer_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(obj, dict):
        kind = obj.get("kind")
        return str(kind) if isinstance(kind, str) else None
    return None


# ── Image resolution (same policy as meta_analysis) ──────────────────────


def _resolve_image_path(url_path: str | None, images_dir: Path) -> Path | None:
    if not url_path or not url_path.startswith("/images/"):
        return None
    candidate = images_dir / url_path.removeprefix("/images/")
    return candidate if candidate.is_file() else None


def _format_judgment(judgment: str) -> str:
    """Make `some_concerns` → `Some concerns` etc. for readable rendering."""
    return judgment.replace("_", " ").capitalize()


# ── PDF builder ──────────────────────────────────────────────────────────


def _distribution_pdf_table(
    distribution: list[DomainDistribution], styles: dict[str, ParagraphStyle]
) -> Table:
    headers = ["Domain", "Low", "Some concerns", "High", "No info"]
    data: list[list[Any]] = [[Paragraph(h, styles["CellHead"]) for h in headers]]
    for d in distribution:
        data.append(
            [
                Paragraph(d.domain, styles["Cell"]),
                Paragraph(str(d.low), styles["Cell"]),
                Paragraph(str(d.some_concerns), styles["Cell"]),
                Paragraph(str(d.high), styles["Cell"]),
                Paragraph(str(d.no_information), styles["Cell"]),
            ]
        )
    t = Table(
        data,
        colWidths=[3.4 * inch, 0.85 * inch, 1.2 * inch, 0.65 * inch, 0.7 * inch],
        repeatRows=1,
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def _assessment_pdf_block(
    s: StudyRobAssessment, styles: dict[str, ParagraphStyle]
) -> list[Any]:
    block: list[Any] = []
    overall_color = _JUDGMENT_COLORS.get(s.overall_judgment, colors.black)
    overall_hex = overall_color.hexval() if hasattr(overall_color, "hexval") else "#000000"
    block.append(
        Paragraph(
            f"PMID {s.pmid} — {s.title}", styles["H3"]
        )
    )
    block.append(
        Paragraph(
            f"<b>Design:</b> {s.study_design or '—'}  ·  "
            f'<b>Overall:</b> <font color="{overall_hex}">'
            f"{_format_judgment(s.overall_judgment)}</font>",
            styles["Body"],
        )
    )
    block.append(Paragraph(s.overall_rationale or "—", styles["Quote"]))

    # Per-domain rows table
    if s.domains:
        rows: list[list[Any]] = [
            [
                Paragraph("Domain", styles["CellHead"]),
                Paragraph("Judgment", styles["CellHead"]),
                Paragraph("Justification", styles["CellHead"]),
            ]
        ]
        for d in s.domains:
            jcolor = _JUDGMENT_COLORS.get(d.judgment, colors.black)
            jhex = jcolor.hexval() if hasattr(jcolor, "hexval") else "#000000"
            quote = (
                f'<br/><i>"{d.quote}"</i>' if getattr(d, "quote", None) else ""
            )
            rows.append(
                [
                    Paragraph(d.domain, styles["Cell"]),
                    Paragraph(
                        f'<font color="{jhex}"><b>{_format_judgment(d.judgment)}</b></font>',
                        styles["Cell"],
                    ),
                    Paragraph((d.justification or "—") + quote, styles["Cell"]),
                ]
            )
        t = Table(
            rows,
            colWidths=[1.9 * inch, 1.0 * inch, 3.9 * inch],
            repeatRows=1,
        )
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                    ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        block.append(t)
    return block


def build_pdf(data: RobReportData, images_dir: Path) -> bytes:
    """Render a polished RoB report. Returns the PDF bytes."""
    buf = io.BytesIO()
    styles = make_pdf_styles()
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Risk-of-bias assessment — {data.tool}",
        author="Clinical Research Assistant",
    )
    w, h = LETTER
    doc.addPageTemplates(
        [
            PageTemplate(
                id="content",
                frames=[
                    Frame(
                        0.6 * inch,
                        0.7 * inch,
                        w - 1.2 * inch,
                        h - 1.5 * inch,
                        id="content",
                        showBoundary=0,
                    )
                ],
                onPage=make_page_decorations("Risk-of-bias report"),
            )
        ]
    )

    story: list[Any] = []
    finality = "FINAL" if data.is_final else "DRAFT"
    story.append(
        Paragraph(
            f"RISK-OF-BIAS  ·  {data.tool}  ·  {finality}  ·  "
            f"GENERATED {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
            styles["Eyebrow"],
        )
    )
    story.append(
        Paragraph(
            f"Risk-of-bias assessment — {data.tool}",
            styles["Title"],
        )
    )

    story.append(Paragraph("Research question", styles["H2"]))
    story.append(Paragraph(data.research_question, styles["Quote"]))

    # ── Summary plot ─────────────────────────────────────────────────────
    local_image = _resolve_image_path(data.summary_plot_image, images_dir)
    story.append(Paragraph("Domain summary", styles["H2"]))
    if local_image is not None:
        try:
            from PIL import Image as PILImage

            with PILImage.open(local_image) as im:
                iw, ih = im.size
            max_w = 6.4 * inch
            draw_w = max_w
            draw_h = max_w * (ih / iw) if iw else 4.0 * inch
            story.append(RLImage(str(local_image), width=draw_w, height=draw_h))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to embed RoB summary plot %s: %s", local_image, exc)
            story.append(
                Paragraph(
                    "<i>Summary plot could not be embedded.</i>", styles["Body"]
                )
            )
    elif data.summary_plot_image:
        story.append(
            Paragraph(
                "<i>Summary plot file unavailable on this server.</i>",
                styles["Body"],
            )
        )
    if data.domain_distribution:
        story.append(_distribution_pdf_table(data.domain_distribution, styles))

    # ── Narrative ────────────────────────────────────────────────────────
    story.append(Paragraph("Narrative interpretation", styles["H2"]))
    for para in [p.strip() for p in (data.narrative or "").split("\n\n") if p.strip()]:
        story.append(Paragraph(para, styles["Body"]))

    # ── Per-study assessments ────────────────────────────────────────────
    if data.assessments:
        story.append(Paragraph("Per-study assessments", styles["H2"]))
        for s in data.assessments:
            block = _assessment_pdf_block(s, styles)
            block.append(Spacer(1, 0.06 * inch))
            story.append(KeepTogether(block))

    # ── Sensitivity recommendations + high-RoB list ──────────────────────
    if data.sensitivity_recommendations:
        story.append(Paragraph("Sensitivity recommendations", styles["H2"]))
        for rec in data.sensitivity_recommendations:
            story.append(Paragraph(f"• {rec}", styles["Body"]))

    if data.high_rob_pmids:
        story.append(Paragraph("Studies with overall = high", styles["H3"]))
        story.append(
            Paragraph(
                ", ".join(f"PMID {p}" for p in data.high_rob_pmids),
                styles["Body"],
            )
        )

    if not data.is_final:
        story.append(Spacer(1, 0.1 * inch))
        story.append(
            Paragraph(
                "<i>Draft — not finalised by the reviewer.</i>", styles["Het"]
            )
        )

    doc.build(story)
    return buf.getvalue()


# ── DOCX builder ─────────────────────────────────────────────────────────


# Same RGB colour map as PDF, but for python-docx (which uses RGBColor).
from docx.shared import RGBColor as _RGB  # noqa: E402

_DOCX_JUDGMENT_COLORS = {
    "low": _RGB(0x2A, 0x9D, 0x8F),
    "some_concerns": _RGB(0xD9, 0xA8, 0x4A),
    "high": _RGB(0xC5, 0x30, 0x30),
    "no_information": _RGB(0x6B, 0x72, 0x80),
}


def _docx_colored_run(p: Any, text: str, judgment: str) -> None:
    run = p.add_run(text)
    run.bold = True
    color = _DOCX_JUDGMENT_COLORS.get(judgment)
    if color is not None:
        run.font.color.rgb = color


def build_docx(data: RobReportData, images_dir: Path) -> bytes:
    """Render a polished RoB report. Returns the DOCX bytes."""
    doc = Document()

    eyebrow = doc.add_paragraph()
    finality = "FINAL" if data.is_final else "DRAFT"
    eyebrow_run = eyebrow.add_run(
        f"Risk-of-bias  ·  {data.tool}  ·  {finality}  ·  "
        f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    eyebrow_run.italic = True
    eyebrow_run.font.size = Pt(9)
    eyebrow_run.font.color.rgb = DOCX_MUTED

    title = doc.add_heading(f"Risk-of-bias assessment — {data.tool}", level=0)
    docx_set_heading_color(title, DOCX_NAVY)

    h = doc.add_heading("Research question", level=1)
    docx_set_heading_color(h, DOCX_TEAL)
    p = doc.add_paragraph(data.research_question)
    for run in p.runs:
        run.italic = True

    # Domain summary (plot + table)
    h = doc.add_heading("Domain summary", level=1)
    docx_set_heading_color(h, DOCX_TEAL)
    local_image = _resolve_image_path(data.summary_plot_image, images_dir)
    if local_image is not None:
        try:
            doc.add_picture(str(local_image), width=Inches(6.4))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to embed RoB summary plot %s: %s", local_image, exc)
            err = doc.add_paragraph("Summary plot could not be embedded.")
            for run in err.runs:
                run.italic = True
    elif data.summary_plot_image:
        note = doc.add_paragraph("Summary plot file unavailable on this server.")
        for run in note.runs:
            run.italic = True

    if data.domain_distribution:
        t = doc.add_table(rows=1, cols=5)
        t.style = "Light Grid Accent 1"
        hdr = t.rows[0].cells
        for i, label in enumerate(["Domain", "Low", "Some concerns", "High", "No info"]):
            hdr[i].text = label
            for run in hdr[i].paragraphs[0].runs:
                run.bold = True
        for d in data.domain_distribution:
            row = t.add_row().cells
            row[0].text = d.domain
            row[1].text = str(d.low)
            row[2].text = str(d.some_concerns)
            row[3].text = str(d.high)
            row[4].text = str(d.no_information)

    # Narrative
    h = doc.add_heading("Narrative interpretation", level=1)
    docx_set_heading_color(h, DOCX_TEAL)
    for para in [p.strip() for p in (data.narrative or "").split("\n\n") if p.strip()]:
        doc.add_paragraph(para)

    # Per-study assessments
    if data.assessments:
        h = doc.add_heading("Per-study assessments", level=1)
        docx_set_heading_color(h, DOCX_TEAL)
        for s in data.assessments:
            sub = doc.add_heading(f"PMID {s.pmid} — {s.title}", level=2)
            docx_set_heading_color(sub, DOCX_NAVY)
            meta = doc.add_paragraph()
            meta.add_run("Design: ").bold = True
            meta.add_run(f"{s.study_design or '—'}  ·  ")
            meta.add_run("Overall: ").bold = True
            _docx_colored_run(meta, _format_judgment(s.overall_judgment), s.overall_judgment)
            r = doc.add_paragraph(s.overall_rationale or "—")
            for run in r.runs:
                run.italic = True
            if s.domains:
                t = doc.add_table(rows=1, cols=3)
                t.style = "Light Grid Accent 1"
                hdr = t.rows[0].cells
                for i, label in enumerate(["Domain", "Judgment", "Justification"]):
                    hdr[i].text = label
                    for run in hdr[i].paragraphs[0].runs:
                        run.bold = True
                for dom in s.domains:
                    row = t.add_row().cells
                    row[0].text = dom.domain
                    jp = row[1].paragraphs[0]
                    _docx_colored_run(jp, _format_judgment(dom.judgment), dom.judgment)
                    just_text = dom.justification or "—"
                    if dom.quote:
                        just_text = f'{just_text}\n"{dom.quote}"'
                    row[2].text = just_text

    # Sensitivity recommendations
    if data.sensitivity_recommendations:
        h = doc.add_heading("Sensitivity recommendations", level=1)
        docx_set_heading_color(h, DOCX_TEAL)
        for rec in data.sensitivity_recommendations:
            doc.add_paragraph(rec, style="List Bullet")

    if data.high_rob_pmids:
        sub = doc.add_heading("Studies with overall = high", level=2)
        docx_set_heading_color(sub, DOCX_NAVY)
        doc.add_paragraph(", ".join(f"PMID {p}" for p in data.high_rob_pmids))

    if not data.is_final:
        p = doc.add_paragraph("Draft — not finalised by the reviewer.")
        for run in p.runs:
            run.italic = True
            run.font.color.rgb = DOCX_MUTED

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# `BORDER` is currently unused at module level for risk_of_bias (table border
# is applied directly via 0.4/_BORDER inside TableStyle). Re-export to keep
# import-time symbol parity with the other report modules.
_ = BORDER
