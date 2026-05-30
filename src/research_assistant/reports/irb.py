"""IRB report — assemble persisted irb_drafter state into a PDF / DOCX
the operator submits to the IRB / ethics committee.

Carries:
  - Protocol synopsis (1-2 page IRB-triage summary)
  - Informed Consent Form (ICF) with the 9 required-element sections
    per 21 CFR §50.25(a) + computed reading-level grade

The PDF header carries the language tag and the actual vs target
reading-level so the IRB sees both up front.
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

from research_assistant.domain.irb import (
    InformedConsentForm,
    IrbDocument,
    IrbIntake,
    ProtocolSynopsis,
    SynopsisSection,
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


@dataclass
class IrbReportData:
    thread_id: str
    title: str
    generated_at: datetime
    intake: IrbIntake | None
    synopsis: ProtocolSynopsis | None
    icf: InformedConsentForm | None
    document: IrbDocument | None


def _peek_kind(s: str) -> str | None:
    try:
        kind = json.loads(s).get("kind")
    except json.JSONDecodeError:
        return None
    return str(kind) if kind is not None else None


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> IrbReportData | None:
    intake: IrbIntake | None = None
    synopsis: ProtocolSynopsis | None = None
    icf: InformedConsentForm | None = None
    document: IrbDocument | None = None

    for msg in messages:
        if msg.role != "assistant" or not msg.final_answer:
            continue
        kind = _peek_kind(msg.final_answer)
        try:
            if kind == "irb_intake":
                intake = IrbIntake.model_validate_json(msg.final_answer)
            elif kind == "protocol_synopsis":
                synopsis = ProtocolSynopsis.model_validate_json(msg.final_answer)
            elif kind == "informed_consent_form":
                icf = InformedConsentForm.model_validate_json(msg.final_answer)
            elif kind == "irb_document":
                document = IrbDocument.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed %s turn in message %s", kind, msg.id)

    if not any((intake, synopsis, icf, document)):
        return None

    base = document or None
    title = "IRB submission packet"
    if base and base.synopsis:
        title = f"IRB packet — {base.synopsis.title}"
    elif synopsis:
        title = f"IRB packet — {synopsis.title}"

    return IrbReportData(
        thread_id=thread_id,
        title=title,
        generated_at=datetime.now(UTC),
        intake=base.intake if base else intake,
        synopsis=base.synopsis if base else synopsis,
        icf=base.icf if base else icf,
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


def _synopsis_section(s: SynopsisSection) -> tuple[str, str]:
    return (s.heading, s.body)


# ── PDF builder ─────────────────────────────────────────────────────────


def build_pdf(data: IrbReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="IRB SUBMISSION PACKET")
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
    flow.append(Paragraph("IRB SUBMISSION PACKET", styles["Eyebrow"]))
    flow.append(Paragraph(data.title, styles["Title"]))
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    if data.icf:
        lang = _LANGUAGE_LABELS.get(data.icf.language, data.icf.language)
        rl_actual = data.icf.reading_level_grade_actual
        rl_target = data.icf.reading_level_target
        delta_warning = ""
        if rl_actual > rl_target + 1:
            delta_warning = (
                f' <font color="#b95900">⚠ above target by '
                f"{rl_actual - rl_target:.1f} grades</font>"
            )
        flow.append(
            Paragraph(
                f"Generated {ts} · ICF language: {lang} · "
                f"Reading level: grade {rl_actual:.1f} (target {rl_target}){delta_warning}",
                styles["Body"],
            )
        )
    else:
        flow.append(Paragraph(f"Generated {ts}", styles["Body"]))

    flow.append(Spacer(1, 10))
    flow.append(
        Paragraph(
            "<i>This packet contains the protocol synopsis and the "
            "Informed Consent Form (ICF). The ICF covers all 9 "
            "required-element sections per 21 CFR §50.25(a). The "
            "operator submits both to the IRB / ethics committee as "
            "part of the start-up packet.</i>",
            styles["Body"],
        )
    )

    if data.synopsis:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Part 1 — Protocol synopsis", styles["H2"]))
        flow.append(
            _kv_table(
                [
                    ("Title", data.synopsis.title),
                    ("Sponsor", data.synopsis.sponsor),
                    ("Protocol id", data.synopsis.protocol_id or "—"),
                ],
                styles,
            )
        )
        for s in (
            data.synopsis.design_summary,
            data.synopsis.objectives,
            data.synopsis.endpoints,
            data.synopsis.methods,
            data.synopsis.statistical_considerations,
            data.synopsis.eligibility_summary,
            data.synopsis.schedule_summary,
            data.synopsis.risks_and_mitigations,
        ):
            flow.append(Spacer(1, 8))
            flow.append(Paragraph(s.heading, styles["H3"]))
            flow.append(Paragraph(s.body, styles["Body"]))

    if data.icf:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Part 2 — Informed Consent Form", styles["H2"]))
        flow.append(
            _kv_table(
                [
                    ("ICF title", data.icf.title),
                    ("Study name", data.icf.study_name),
                    ("Sponsor", data.icf.sponsor),
                    ("Language", _LANGUAGE_LABELS.get(data.icf.language, data.icf.language)),
                    (
                        "Reading level (target / actual)",
                        f"Grade {data.icf.reading_level_target} / "
                        f"{data.icf.reading_level_grade_actual:.1f}",
                    ),
                ],
                styles,
            )
        )
        for sec in data.icf.sections:
            flow.append(Spacer(1, 8))
            flow.append(Paragraph(f"{sec.heading}", styles["H3"]))
            flow.append(Paragraph(sec.body, styles["Body"]))
        for sec in data.icf.optional_sections:
            flow.append(Spacer(1, 8))
            flow.append(Paragraph(f"(Optional) {sec.heading}", styles["H3"]))
            flow.append(Paragraph(sec.body, styles["Body"]))

        flow.append(Spacer(1, 12))
        flow.append(Paragraph("Acknowledgement", styles["H3"]))
        flow.append(Paragraph(data.icf.signature_block, styles["Body"]))
        flow.append(Spacer(1, 8))
        flow.append(
            _kv_table(
                [
                    ("Participant name", "__________________________________"),
                    ("Participant signature", "__________________________________"),
                    ("Date", "______________"),
                    ("Investigator signature", "__________________________________"),
                    ("Date", "______________"),
                ],
                styles,
            )
        )

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ────────────────────────────────────────────────────────


def build_docx(data: IrbReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    docx = Document()
    docx.core_properties.title = data.title
    docx.add_heading(data.title, level=0)
    header_runs = docx.add_paragraph(
        f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
    ).runs
    if header_runs:
        header_runs[0].font.color.rgb = DOCX_MUTED

    if data.synopsis:
        docx.add_heading("Part 1 — Protocol synopsis", level=1)
        for s in (
            data.synopsis.design_summary,
            data.synopsis.objectives,
            data.synopsis.endpoints,
            data.synopsis.methods,
            data.synopsis.statistical_considerations,
            data.synopsis.eligibility_summary,
            data.synopsis.schedule_summary,
            data.synopsis.risks_and_mitigations,
        ):
            docx.add_heading(s.heading, level=2)
            docx.add_paragraph(s.body)

    if data.icf:
        docx.add_heading("Part 2 — Informed Consent Form", level=1)
        meta = docx.add_paragraph(
            f"Language: {_LANGUAGE_LABELS.get(data.icf.language, data.icf.language)}  ·  "
            f"Reading level (target / actual): "
            f"Grade {data.icf.reading_level_target} / "
            f"{data.icf.reading_level_grade_actual:.1f}"
        )
        if meta.runs:
            meta.runs[0].font.size = Pt(10)
            meta.runs[0].italic = True
        for sec in data.icf.sections:
            docx.add_heading(sec.heading, level=2)
            docx.add_paragraph(sec.body)
        for sec in data.icf.optional_sections:
            docx.add_heading(f"(Optional) {sec.heading}", level=2)
            docx.add_paragraph(sec.body)
        docx.add_heading("Acknowledgement", level=2)
        docx.add_paragraph(data.icf.signature_block)
        docx.add_paragraph("Participant name: __________________________________")
        docx.add_paragraph("Participant signature: __________________________________")
        docx.add_paragraph("Date: ______________")
        docx.add_paragraph("Investigator signature: __________________________________")
        docx.add_paragraph("Date: ______________")

    out = io.BytesIO()
    docx.save(out)
    return out.getvalue()


__all__ = [
    "IrbReportData",
    "assemble_report_data",
    "build_docx",
    "build_pdf",
]
