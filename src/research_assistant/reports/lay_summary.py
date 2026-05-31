"""Lay-summary report — assemble persisted lay_summary state into a
patient-facing PDF / DOCX.

Surfaces:
  • Reading-grade badge (target vs host-computed actual) in the cover
    band — green if within target+1, amber if over.
  • Language + region tag so the operator can tell at a glance which
    locale a draft is for.
  • The 5 plain-language sections, the one-line strapline, and the
    optional glossary as a sidebar list.
  • Source provenance footer (PMIDs for evidence, derived_from ids for
    results, "Recruitment material" for recruitment).
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
from docx.shared import Pt
from pydantic import ValidationError
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
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

from research_assistant.domain.lay_summary import (
    EvidenceIntake,
    LaySummaryDocument,
    LaySummaryDraft,
    RecruitmentIntake,
    ResultsIntake,
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

_LANGUAGE_LABELS = {
    "en": "English",
    "es": "Spanish (Español)",
    "fr": "French (Français)",
    "de": "German (Deutsch)",
}

_SOURCE_LABELS = {
    "recruitment_protocol": "Recruitment material (protocol synopsis)",
    "evidence_meta_analysis": "Evidence summary (meta-analysis)",
    "results_trial": "Return-of-results (trial)",
}


@dataclass
class LaySummaryReportData:
    thread_id: str
    title: str
    generated_at: datetime
    intake: RecruitmentIntake | EvidenceIntake | ResultsIntake | None
    draft: LaySummaryDraft | None
    document: LaySummaryDocument | None


def _peek_kind(s: str) -> str | None:
    try:
        kind = json.loads(s).get("kind")
    except json.JSONDecodeError:
        return None
    return str(kind) if kind is not None else None


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> LaySummaryReportData | None:
    intake: RecruitmentIntake | EvidenceIntake | ResultsIntake | None = None
    draft: LaySummaryDraft | None = None
    document: LaySummaryDocument | None = None

    for msg in messages:
        if msg.role != "assistant" or not msg.final_answer:
            continue
        kind = _peek_kind(msg.final_answer)
        try:
            if kind == "recruitment_intake":
                intake = RecruitmentIntake.model_validate_json(msg.final_answer)
            elif kind == "evidence_intake":
                intake = EvidenceIntake.model_validate_json(msg.final_answer)
            elif kind == "results_intake":
                intake = ResultsIntake.model_validate_json(msg.final_answer)
            elif kind == "lay_summary_draft":
                draft = LaySummaryDraft.model_validate_json(msg.final_answer)
            elif kind == "lay_summary_document":
                document = LaySummaryDocument.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed %s turn in message %s", kind, msg.id)

    if not any((intake, draft, document)):
        return None

    title = "Plain-language summary"
    if document is not None:
        title = f"Plain-language summary — {document.draft.title}"
    elif draft is not None:
        title = f"Plain-language summary — {draft.title}"

    return LaySummaryReportData(
        thread_id=thread_id,
        title=title,
        generated_at=datetime.now(UTC),
        intake=intake,
        draft=document.draft if document else draft,
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
    t = Table(table_rows, colWidths=[2.0 * inch, 4.8 * inch])
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


# ── PDF builder ─────────────────────────────────────────────────────────


def build_pdf(data: LaySummaryReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="PLAIN-LANGUAGE SUMMARY")
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.7 * inch,
        title=data.title,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decor)])

    flow: list[Any] = []
    flow.append(Paragraph("PLAIN-LANGUAGE SUMMARY", styles["Eyebrow"]))
    flow.append(Paragraph(data.title, styles["Title"]))

    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    if data.document is not None:
        audience = data.document.audience
        lang = _LANGUAGE_LABELS.get(audience.language, audience.language)
        region = f" ({audience.region})" if audience.region else ""
        rl_actual = data.document.grade_actual
        rl_target = audience.target_grade
        source_label = _SOURCE_LABELS.get(data.document.source_kind, data.document.source_kind)
        within = data.document.grade_within_target
        badge = (
            '<font color="#1f6f3f">✓ within target+1</font>'
            if within
            else (
                f'<font color="#b95900">⚠ above target by {rl_actual - rl_target:.1f} grades</font>'
            )
        )
        flow.append(
            Paragraph(
                f"Generated {ts} · {source_label} · Language: {lang}{region} · "
                f"Reading level: grade {rl_actual:.1f} (target {rl_target}) — {badge}",
                styles["Body"],
            )
        )
        flow.append(
            Paragraph(
                f"<i>Audience: {audience.population_descriptor}. "
                f"Rewrite passes: {data.document.readability_attempts}.</i>",
                styles["Body"],
            )
        )

    flow.append(Spacer(1, 12))

    if data.draft is not None:
        flow.append(
            Paragraph(
                f"<b>{data.draft.one_line_summary}</b>",
                styles["Body"],
            )
        )
        flow.append(Spacer(1, 10))
        for section in data.draft.sections:
            flow.append(Paragraph(section.heading, styles["H3"]))
            flow.append(Paragraph(section.body, styles["Body"]))
            flow.append(Spacer(1, 8))

        if data.draft.plain_language_glossary:
            flow.append(Spacer(1, 6))
            flow.append(Paragraph("Plain-language glossary", styles["H3"]))
            glossary_rows = [
                (entry.get("term", ""), entry.get("gloss", ""))
                for entry in data.draft.plain_language_glossary
                if entry.get("term")
            ]
            if glossary_rows:
                flow.append(_kv_table(glossary_rows, styles))

    if data.document is not None and data.document.citations:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("Sources", styles["H3"]))
        flow.append(
            Paragraph(
                ", ".join(data.document.citations),
                styles["Body"],
            )
        )

    if data.document is not None:
        flow.append(Spacer(1, 10))
        finalized = "Finalised" if data.document.is_final else "Draft — not yet finalised"
        flow.append(
            Paragraph(
                f"<i>Status: {finalized}. "
                "This is a patient-facing plain-language summary — it is "
                "not medical advice. It describes what the study or "
                "evidence covers; it does not recommend any specific "
                "course of action. Discuss treatment decisions with your "
                "clinician.</i>",
                styles["Body"],
            )
        )

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ────────────────────────────────────────────────────────


def build_docx(data: LaySummaryReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    docx = Document()
    docx.core_properties.title = data.title
    docx.add_heading(data.title, level=0)

    header = docx.add_paragraph(f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if header.runs:
        header.runs[0].font.color.rgb = DOCX_MUTED

    if data.document is not None:
        audience = data.document.audience
        lang = _LANGUAGE_LABELS.get(audience.language, audience.language)
        region = f" ({audience.region})" if audience.region else ""
        source_label = _SOURCE_LABELS.get(data.document.source_kind, data.document.source_kind)
        within_marker = "✓" if data.document.grade_within_target else "⚠"
        meta = docx.add_paragraph(
            f"{source_label}  ·  Language: {lang}{region}  ·  "
            f"Reading level: grade {data.document.grade_actual:.1f} "
            f"(target {audience.target_grade}) {within_marker}  ·  "
            f"Rewrite passes: {data.document.readability_attempts}"
        )
        if meta.runs:
            meta.runs[0].font.size = Pt(10)
            meta.runs[0].italic = True

    if data.draft is not None:
        strapline = docx.add_paragraph(data.draft.one_line_summary)
        if strapline.runs:
            strapline.runs[0].bold = True

        for section in data.draft.sections:
            docx.add_heading(section.heading, level=2)
            docx.add_paragraph(section.body)

        if data.draft.plain_language_glossary:
            docx.add_heading("Plain-language glossary", level=2)
            for entry in data.draft.plain_language_glossary:
                term = entry.get("term", "")
                gloss = entry.get("gloss", "")
                if term:
                    p = docx.add_paragraph()
                    p.add_run(f"{term} — ").bold = True
                    p.add_run(gloss)

    if data.document is not None and data.document.citations:
        docx.add_heading("Sources", level=2)
        docx.add_paragraph(", ".join(data.document.citations))

    if data.document is not None:
        finalized = "Finalised" if data.document.is_final else "Draft — not yet finalised"
        footer = docx.add_paragraph(
            f"Status: {finalized}. This is a patient-facing plain-language "
            "summary — it is not medical advice. Discuss treatment "
            "decisions with your clinician."
        )
        if footer.runs:
            footer.runs[0].italic = True
            footer.runs[0].font.size = Pt(9)

    out = io.BytesIO()
    docx.save(out)
    return out.getvalue()


__all__ = [
    "LaySummaryReportData",
    "assemble_report_data",
    "build_docx",
    "build_pdf",
]
