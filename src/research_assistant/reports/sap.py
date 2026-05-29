"""SAP report — assemble persisted thread state into a manuscript-style
PDF or DOCX a researcher can drop into a protocol, IRB packet, or
regulatory submission.

Pipeline mirrors the other report modules:

    Message rows ─▶ assemble_report_data() ─▶ SapReportData
                                                  │
                                                  ├─▶ build_pdf()  ─▶ bytes
                                                  └─▶ build_docx() ─▶ bytes

Unlike `meta_analysis`, this report has no embedded plots — the assembler
+ builders pass through `images_dir` for API consistency but don't use it.
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
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from research_assistant.domain.sap import (
    AnalysisPlan,
    PicotTable,
    SampleSizeResult,
    SapDocument,
)
from research_assistant.persistence.models import Message
from research_assistant.reports._shared_styles import (
    BORDER,
    DOCX_MUTED,
    DOCX_NAVY,
    LIGHT,
    make_page_decorations,
    make_pdf_styles,
)

logger = logging.getLogger(__name__)


@dataclass
class SapReportData:
    thread_id: str
    title: str
    research_question: str
    generated_at: datetime
    picot: PicotTable | None
    sample_size: SampleSizeResult | None
    analysis_plan: AnalysisPlan | None
    sap_document: SapDocument | None


def _peek_kind(final_answer_json: str) -> str | None:
    try:
        obj = json.loads(final_answer_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(obj, dict):
        kind = obj.get("kind")
        return str(kind) if isinstance(kind, str) else None
    return None


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> SapReportData | None:
    """Walk an ordered message list and pull out the latest SAP state.

    Returns None if the thread never reached a SAP turn. Returns a
    `SapReportData` whose individual fields may be None if specific
    stages weren't reached — the builders render the available sections
    and omit the missing ones.
    """
    research_question = ""
    picot: PicotTable | None = None
    sample_size: SampleSizeResult | None = None
    analysis_plan: AnalysisPlan | None = None
    sap_document: SapDocument | None = None

    for msg in messages:
        if msg.role == "user" and not research_question and msg.input_text:
            research_question = msg.input_text.strip()
            continue
        if msg.role != "assistant" or not msg.final_answer:
            continue
        kind = _peek_kind(msg.final_answer)
        try:
            if kind == "picot":
                picot = PicotTable.model_validate_json(msg.final_answer)
            elif kind == "sample_size":
                sample_size = SampleSizeResult.model_validate_json(msg.final_answer)
            elif kind == "analysis_plan":
                analysis_plan = AnalysisPlan.model_validate_json(msg.final_answer)
            elif kind == "sap_document":
                sap_document = SapDocument.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed %s turn in message %s", kind, msg.id)

    # Need at least one SAP-stage turn to be worth rendering. The SAP
    # document IS the report — if we have it, that's the most complete
    # rendering. Otherwise fall through with what we have.
    if not any((picot, sample_size, analysis_plan, sap_document)):
        return None

    base_picot = sap_document.picot if sap_document else picot
    title = "Statistical Analysis Plan"
    if base_picot and base_picot.intervention and base_picot.population:
        title = f"SAP — {base_picot.intervention} in {base_picot.population}"

    return SapReportData(
        thread_id=thread_id,
        title=title,
        research_question=research_question or "(Research question not captured.)",
        generated_at=datetime.now(UTC),
        picot=base_picot,
        sample_size=sap_document.sample_size if sap_document else sample_size,
        analysis_plan=sap_document.analysis_plan if sap_document else analysis_plan,
        sap_document=sap_document,
    )


# ── PDF builder ──────────────────────────────────────────────────────────


def _picot_pdf_table(p: PicotTable, styles: dict[str, ParagraphStyle]) -> Table:
    rows: list[list[Any]] = [
        [Paragraph("<b>Population</b>", styles["Cell"]), Paragraph(p.population, styles["Cell"])],
        [
            Paragraph("<b>Intervention</b>", styles["Cell"]),
            Paragraph(p.intervention, styles["Cell"]),
        ],
        [Paragraph("<b>Comparator</b>", styles["Cell"]), Paragraph(p.comparator, styles["Cell"])],
        [
            Paragraph("<b>Primary outcome</b>", styles["Cell"]),
            Paragraph(p.primary_outcome, styles["Cell"]),
        ],
        [
            Paragraph("<b>Secondary outcomes</b>", styles["Cell"]),
            Paragraph("; ".join(p.secondary_outcomes) or "—", styles["Cell"]),
        ],
        [Paragraph("<b>Timeframe</b>", styles["Cell"]), Paragraph(p.timeframe, styles["Cell"])],
        [
            Paragraph("<b>Design</b>", styles["Cell"]),
            Paragraph(p.design.replace("_", " "), styles["Cell"]),
        ],
        [
            Paragraph("<b>Hypothesis</b>", styles["Cell"]),
            Paragraph(p.hypothesis_type.replace("_", " "), styles["Cell"]),
        ],
        [
            Paragraph("<b>Outcome type</b>", styles["Cell"]),
            Paragraph(p.outcome_type.replace("_", " "), styles["Cell"]),
        ],
    ]
    t = Table(rows, colWidths=[1.6 * inch, 5.2 * inch])
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


def _samplesize_pdf_table(s: SampleSizeResult, styles: dict[str, ParagraphStyle]) -> Table:
    rows: list[list[Any]] = [
        [Paragraph("<b>Formula</b>", styles["Cell"]), Paragraph(s.formula_name, styles["Cell"])],
        [
            Paragraph("<b>n per arm (control)</b>", styles["Cell"]),
            Paragraph(f"{s.n_per_arm_control}", styles["Cell"]),
        ],
        [
            Paragraph("<b>n per arm (intervention)</b>", styles["Cell"]),
            Paragraph(f"{s.n_per_arm_intervention}", styles["Cell"]),
        ],
        [
            Paragraph("<b>Total enrolment</b>", styles["Cell"]),
            Paragraph(f"{s.n_total}", styles["Cell"]),
        ],
    ]
    if s.events_required is not None:
        rows.append(
            [
                Paragraph("<b>Events required</b>", styles["Cell"]),
                Paragraph(f"{s.events_required}", styles["Cell"]),
            ]
        )
    rows.append(
        [
            Paragraph("<b>Reference</b>", styles["Cell"]),
            Paragraph(s.formula_reference, styles["Cell"]),
        ]
    )
    t = Table(rows, colWidths=[1.8 * inch, 5.0 * inch])
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


def build_pdf(data: SapReportData, images_dir: Path) -> bytes:
    """Render the SAP as a manuscript-style PDF.

    `images_dir` is unused (the SAP carries no embedded figures) but kept
    for signature parity with the other report builders so the dispatch
    map in `web/threads.py` is uniform.
    """
    del images_dir  # signature-parity only
    buf = io.BytesIO()
    styles = make_pdf_styles()
    page_decor = make_page_decorations(footer_label="STATISTICAL ANALYSIS PLAN")

    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.7 * inch,
        title=data.title,
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="main",
    )
    doc.addPageTemplates([PageTemplate(id="cover", frames=[frame], onPage=page_decor)])

    flow: list[Any] = []
    flow.append(Paragraph("STATISTICAL ANALYSIS PLAN", styles["Eyebrow"]))
    flow.append(Paragraph(data.title, styles["Title"]))
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    flow.append(
        Paragraph(
            f"Generated {ts}  ·  thread {data.thread_id[:8]}",
            styles["Body"],
        )
    )
    flow.append(Spacer(1, 12))
    flow.append(Paragraph("Research question", styles["H2"]))
    flow.append(Paragraph(data.research_question, styles["Body"]))

    if data.picot is not None:
        flow.append(Spacer(1, 10))
        flow.append(Paragraph("Trial design (PICOT)", styles["H2"]))
        flow.append(_picot_pdf_table(data.picot, styles))

    if data.sample_size is not None:
        flow.append(Spacer(1, 10))
        flow.append(
            KeepTogether(
                [
                    Paragraph("Sample-size derivation", styles["H2"]),
                    _samplesize_pdf_table(data.sample_size, styles),
                ]
            )
        )
        if data.sample_size.caveats:
            for cav in data.sample_size.caveats:
                flow.append(Paragraph(f"⚠ {cav}", styles["Body"]))

    if data.analysis_plan is not None:
        ap = data.analysis_plan
        flow.append(Spacer(1, 10))
        flow.append(Paragraph("Analysis plan (ICH E9)", styles["H2"]))
        flow.append(
            Paragraph(f"<b>Populations:</b> {' / '.join(ap.populations_used)}", styles["Body"])
        )
        flow.append(Paragraph(f"<b>Primary test:</b> {ap.primary_test_name}", styles["Body"]))
        flow.append(Paragraph(ap.primary_analysis_description, styles["Body"]))
        flow.append(
            Paragraph(
                f"<b>Multiplicity strategy:</b> {ap.multiplicity_strategy.replace('_', ' ')}",
                styles["Body"],
            )
        )
        flow.append(
            Paragraph(
                f"<b>Missing data:</b> {ap.missing_data_strategy.replace('_', ' ')}", styles["Body"]
            )
        )
        if ap.interim_analyses:
            flow.append(Paragraph("<b>Interim analyses</b>", styles["H3"]))
            for ia in ap.interim_analyses:
                flow.append(
                    Paragraph(f"At {ia.at_fraction:.0%} information — {ia.rule}", styles["Body"])
                )
        if ap.sensitivity_analyses:
            flow.append(Paragraph("<b>Sensitivity analyses</b>", styles["H3"]))
            for s in ap.sensitivity_analyses:
                flow.append(Paragraph(f"• {s}", styles["Body"]))
        if ap.subgroup_analyses:
            flow.append(Paragraph("<b>Subgroup analyses</b>", styles["H3"]))
            for s in ap.subgroup_analyses:
                flow.append(Paragraph(f"• {s}", styles["Body"]))
        if ap.safety_monitoring:
            flow.append(Paragraph("<b>Safety monitoring</b>", styles["H3"]))
            flow.append(Paragraph(ap.safety_monitoring, styles["Body"]))

    if data.sap_document is not None:
        sd = data.sap_document
        if sd.background:
            flow.append(Spacer(1, 10))
            flow.append(Paragraph("Background", styles["H2"]))
            flow.append(Paragraph(sd.background, styles["Body"]))
        if sd.references:
            flow.append(Spacer(1, 10))
            flow.append(Paragraph("References", styles["H2"]))
            for c in sd.references:
                meta_bits = [c.authors, c.journal, str(c.year) if c.year else None]
                meta = ", ".join(b for b in meta_bits if b)
                tail = ""
                if c.pmid:
                    tail += f" PMID {c.pmid}"
                if c.doi:
                    tail += f" doi:{c.doi}"
                if c.url:
                    tail += f" — {c.url}"
                flow.append(Paragraph(f"[{c.n}] {c.title}  <i>{meta}</i>{tail}", styles["Body"]))

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ─────────────────────────────────────────────────────────


def build_docx(data: SapReportData, images_dir: Path) -> bytes:
    del images_dir  # signature-parity only
    docx = Document()
    docx.add_paragraph().add_run("STATISTICAL ANALYSIS PLAN").bold = True
    title = docx.add_paragraph()
    run = title.add_run(data.title)
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = DOCX_NAVY
    meta = docx.add_paragraph()
    meta_run = meta.add_run(
        f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ·  "
        f"thread {data.thread_id[:8]}"
    )
    meta_run.font.color.rgb = DOCX_MUTED

    docx.add_heading("Research question", level=2)
    docx.add_paragraph(data.research_question)

    if data.picot is not None:
        p = data.picot
        docx.add_heading("Trial design (PICOT)", level=2)
        rows = [
            ("Population", p.population),
            ("Intervention", p.intervention),
            ("Comparator", p.comparator),
            ("Primary outcome", p.primary_outcome),
            ("Secondary outcomes", "; ".join(p.secondary_outcomes) or "—"),
            ("Timeframe", p.timeframe),
            ("Design", p.design.replace("_", " ")),
            ("Hypothesis", p.hypothesis_type.replace("_", " ")),
            ("Outcome type", p.outcome_type.replace("_", " ")),
        ]
        tbl = docx.add_table(rows=len(rows), cols=2)
        tbl.style = "Light Grid Accent 1"
        for i, (k, v) in enumerate(rows):
            tbl.cell(i, 0).text = k
            tbl.cell(i, 1).text = v

    if data.sample_size is not None:
        s = data.sample_size
        docx.add_heading("Sample-size derivation", level=2)
        rows = [
            ("Formula", s.formula_name),
            ("n per arm (control)", str(s.n_per_arm_control)),
            ("n per arm (intervention)", str(s.n_per_arm_intervention)),
            ("Total enrolment", str(s.n_total)),
        ]
        if s.events_required is not None:
            rows.append(("Events required", str(s.events_required)))
        rows.append(("Reference", s.formula_reference))
        tbl = docx.add_table(rows=len(rows), cols=2)
        tbl.style = "Light Grid Accent 1"
        for i, (k, v) in enumerate(rows):
            tbl.cell(i, 0).text = k
            tbl.cell(i, 1).text = v
        for cav in s.caveats:
            caveat_p = docx.add_paragraph()
            caveat_p.add_run(f"⚠ {cav}").font.color.rgb = DOCX_MUTED

    if data.analysis_plan is not None:
        ap = data.analysis_plan
        docx.add_heading("Analysis plan (ICH E9)", level=2)
        docx.add_paragraph().add_run(f"Populations: {' / '.join(ap.populations_used)}")
        docx.add_paragraph().add_run(f"Primary test: {ap.primary_test_name}")
        docx.add_paragraph(ap.primary_analysis_description)
        docx.add_paragraph().add_run(
            f"Multiplicity strategy: {ap.multiplicity_strategy.replace('_', ' ')}"
        )
        docx.add_paragraph().add_run(f"Missing data: {ap.missing_data_strategy.replace('_', ' ')}")
        if ap.interim_analyses:
            docx.add_heading("Interim analyses", level=3)
            for ia in ap.interim_analyses:
                docx.add_paragraph(
                    f"At {ia.at_fraction:.0%} information — {ia.rule}", style="List Bullet"
                )
        if ap.sensitivity_analyses:
            docx.add_heading("Sensitivity analyses", level=3)
            for sa in ap.sensitivity_analyses:
                docx.add_paragraph(sa, style="List Bullet")
        if ap.subgroup_analyses:
            docx.add_heading("Subgroup analyses", level=3)
            for sg in ap.subgroup_analyses:
                docx.add_paragraph(sg, style="List Bullet")
        if ap.safety_monitoring:
            docx.add_heading("Safety monitoring", level=3)
            docx.add_paragraph(ap.safety_monitoring)

    if data.sap_document is not None:
        sd = data.sap_document
        if sd.background:
            docx.add_heading("Background", level=2)
            docx.add_paragraph(sd.background)
        if sd.references:
            docx.add_heading("References", level=2)
            for c in sd.references:
                meta_bits = [c.authors, c.journal, str(c.year) if c.year else None]
                meta_text = ", ".join(b for b in meta_bits if b)
                tail = ""
                if c.pmid:
                    tail += f" PMID {c.pmid}"
                if c.doi:
                    tail += f" doi:{c.doi}"
                if c.url:
                    tail += f" — {c.url}"
                docx.add_paragraph(f"[{c.n}] {c.title}. {meta_text}{tail}", style="List Bullet")

    buf = io.BytesIO()
    docx.save(buf)
    return buf.getvalue()


__all__ = [
    "SapReportData",
    "assemble_report_data",
    "build_docx",
    "build_pdf",
]
