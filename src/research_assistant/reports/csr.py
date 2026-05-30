"""CSR (Clinical Study Report, ICH E3) report.

Pipeline mirrors the other report modules:

    Message rows ─▶ assemble_report_data() ─▶ CsrReportData
                                                  │
                                                  ├─▶ build_pdf()  ─▶ bytes
                                                  └─▶ build_docx() ─▶ bytes

Renders the ICH E3 section headers in order, with the four data-driven
sections populated from the agent's structured output and the narrative
sections shown as `[Operator to complete]` placeholders. Every count
in the data sections carries its `derived_from` source artefact id —
the regulator-grade audit anchor.
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

from research_assistant.domain.csr import (
    CsrDataSections,
    CsrDocument,
    CsrIntake,
    CsrSynopsis,
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


# ── ICH E3 section headers (the spine) ─────────────────────────────────


_E3_SECTIONS: list[tuple[str, str]] = [
    ("1", "Synopsis"),
    ("2", "Table of Contents"),
    ("3", "Glossary"),
    ("4", "Ethics"),
    ("5", "Investigators and Study Administrative Structure"),
    ("6", "Introduction"),
    ("7", "Study Objectives"),
    ("8", "Investigational Plan"),
    ("9", "Study Patients"),
    ("10", "Subject Disposition"),
    ("10.1", "Subject Disposition"),
    ("10.2", "Demographics + Baseline Characteristics"),
    ("11", "Efficacy Evaluation"),
    ("12", "Safety Evaluation"),
    ("13", "Discussion"),
    ("14", "Overall Conclusions"),
    ("15", "Tables, Listings, Figures"),
    ("16", "References"),
]


@dataclass
class CsrReportData:
    thread_id: str
    title: str
    generated_at: datetime
    intake: CsrIntake | None
    synopsis: CsrSynopsis | None
    data_sections: CsrDataSections | None
    document: CsrDocument | None


def _peek_kind(s: str) -> str | None:
    try:
        kind = json.loads(s).get("kind")
    except json.JSONDecodeError:
        return None
    return str(kind) if kind is not None else None


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> CsrReportData | None:
    intake: CsrIntake | None = None
    synopsis: CsrSynopsis | None = None
    data_sections: CsrDataSections | None = None
    document: CsrDocument | None = None

    for msg in messages:
        if msg.role != "assistant" or not msg.final_answer:
            continue
        kind = _peek_kind(msg.final_answer)
        try:
            if kind == "csr_intake":
                intake = CsrIntake.model_validate_json(msg.final_answer)
            elif kind == "csr_synopsis":
                synopsis = CsrSynopsis.model_validate_json(msg.final_answer)
            elif kind == "csr_data_sections":
                data_sections = CsrDataSections.model_validate_json(msg.final_answer)
            elif kind == "csr_document":
                document = CsrDocument.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed %s turn in message %s", kind, msg.id)

    if not any((intake, synopsis, data_sections, document)):
        return None

    base = document
    title = "Clinical Study Report"
    if base and base.synopsis:
        title = f"CSR — {base.synopsis.title}"
    elif synopsis:
        title = f"CSR — {synopsis.title}"

    return CsrReportData(
        thread_id=thread_id,
        title=title,
        generated_at=datetime.now(UTC),
        intake=base.intake if base else intake,
        synopsis=base.synopsis if base else synopsis,
        data_sections=base.data_sections if base else data_sections,
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


def _data_table(
    columns: list[str],
    rows: list[list[Any]],
    styles: dict[str, ParagraphStyle],
) -> Table:
    table_rows: list[list[Paragraph]] = [
        [Paragraph(f"<b>{c}</b>", styles["Cell"]) for c in columns]
    ]
    for r in rows:
        table_rows.append([Paragraph(str(v or "—"), styles["Cell"]) for v in r])
    t = Table(table_rows, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


# ── PDF builder ─────────────────────────────────────────────────────────


def build_pdf(data: CsrReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="CLINICAL STUDY REPORT (ICH E3)")
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
    flow.append(Paragraph("CLINICAL STUDY REPORT (ICH E3)", styles["Eyebrow"]))
    flow.append(Paragraph(data.title, styles["Title"]))
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    flow.append(Paragraph(f"Generated {ts}", styles["Body"]))
    flow.append(Spacer(1, 10))
    flow.append(
        Paragraph(
            "<i>Sponsor and investigator information marked [SPONSOR INPUT] "
            "must be completed before submission. Narrative sections "
            "(Introduction / Discussion / Overall Conclusions) show "
            "[Operator to complete] placeholders in this slice — the next "
            "release drafts them. Every count + effect-size in the data "
            "sections carries its source artefact id (TLF or ADaM "
            "row) for the regulator audit trail.</i>",
            styles["Body"],
        )
    )

    if data.intake:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Study identity", styles["H2"]))
        flow.append(
            _kv_table(
                [
                    ("Study id", data.intake.study_id),
                    ("Study name", data.intake.study_name),
                    ("Sponsor", data.intake.sponsor),
                    ("Blinding", data.intake.blinding.replace("_", " ")),
                    ("Lock date", data.intake.lock_date or "[SPONSOR INPUT]"),
                    (
                        "Target jurisdictions",
                        ", ".join(data.intake.target_jurisdictions)
                        or "[SPONSOR INPUT]",
                    ),
                ],
                styles,
            )
        )

    # Section 1 — Synopsis
    flow.append(Spacer(1, 14))
    flow.append(Paragraph("1. Synopsis", styles["H2"]))
    if data.synopsis:
        s = data.synopsis
        flow.append(
            _kv_table(
                [
                    ("Title", s.title),
                    ("Sponsor", s.sponsor),
                    ("Protocol id", s.protocol_id or "[SPONSOR INPUT]"),
                    ("Objectives", s.objectives_text),
                    ("Methodology", s.methodology_text),
                    ("Number planned", str(s.number_planned)),
                    (
                        "Number analysed — Safety / Efficacy",
                        f"{s.number_analysed_safety} / {s.number_analysed_efficacy}",
                    ),
                    ("Primary endpoint", s.primary_endpoint),
                    ("Primary result", s.primary_result_description),
                    (
                        "Result direction",
                        s.primary_result_direction.replace("_", " "),
                    ),
                    ("Source artefact", s.derived_from),
                    ("Safety overview", s.safety_overview),
                    ("Conclusions", s.conclusions),
                ],
                styles,
            )
        )
    else:
        flow.append(Paragraph("[Operator to complete — Synopsis]", styles["Body"]))

    # Sections 2-9 (skeletons)
    for num, name in (
        ("2", "Table of Contents"),
        ("3", "Glossary"),
        ("4", "Ethics"),
        ("5", "Investigators and Study Administrative Structure"),
        ("6", "Introduction"),
        ("7", "Study Objectives"),
        ("8", "Investigational Plan"),
        ("9", "Study Patients"),
    ):
        flow.append(Spacer(1, 12))
        flow.append(Paragraph(f"{num}. {name}", styles["H2"]))
        flow.append(
            Paragraph(f"[Operator to complete — {name}]", styles["Body"])
        )

    # Section 10 — Disposition + Demographics (data-driven)
    ds = data.data_sections
    flow.append(Spacer(1, 14))
    flow.append(Paragraph("10. Study Patients", styles["H2"]))
    flow.append(Paragraph("10.1 Subject Disposition", styles["H3"]))
    if ds:
        flow.append(
            _data_table(
                ["Status", "n", "%"],
                [[r.label, r.n, r.pct] for r in ds.disposition.rows],
                styles,
            )
        )
        flow.append(
            Paragraph(
                f"<i>Source: {ds.disposition.derived_from}</i>",
                styles["Body"],
            )
        )
    else:
        flow.append(
            Paragraph(
                "[Operator to complete — paste from TLF t-disposition]",
                styles["Body"],
            )
        )
    flow.append(Spacer(1, 8))
    flow.append(Paragraph("10.2 Demographics + Baseline Characteristics", styles["H3"]))
    if ds:
        flow.append(
            _data_table(
                ["Characteristic", "Value", "Detail"],
                [
                    [r.characteristic, r.value, r.detail or "—"]
                    for r in ds.demographics.rows
                ],
                styles,
            )
        )
        flow.append(
            Paragraph(
                f"<i>Source: {ds.demographics.derived_from}</i>",
                styles["Body"],
            )
        )
    else:
        flow.append(
            Paragraph(
                "[Operator to complete — paste from TLF t-demographics]",
                styles["Body"],
            )
        )

    # Section 11 — Efficacy
    flow.append(Spacer(1, 14))
    flow.append(Paragraph("11. Efficacy Evaluation", styles["H2"]))
    if ds:
        flow.append(
            _kv_table(
                [
                    ("Primary endpoint", ds.efficacy.primary_endpoint_text),
                    (
                        "Primary endpoint source",
                        ds.efficacy.primary_endpoint_derived_from,
                    ),
                    (
                        "Secondary endpoints",
                        "; ".join(ds.efficacy.secondary_endpoints) or "—",
                    ),
                    (
                        "Secondary sources",
                        "; ".join(ds.efficacy.secondary_endpoints_derived_from)
                        or "—",
                    ),
                    (
                        "Populations analysed",
                        ", ".join(ds.efficacy.populations_analysed) or "—",
                    ),
                ],
                styles,
            )
        )
    else:
        flow.append(
            Paragraph(
                "[Operator to complete — efficacy from ADTTE + TLF references]",
                styles["Body"],
            )
        )

    # Section 12 — Safety
    flow.append(Spacer(1, 14))
    flow.append(Paragraph("12. Safety Evaluation", styles["H2"]))
    if ds:
        sf = ds.safety
        flow.append(
            _kv_table(
                [
                    ("Total AE events", str(sf.total_ae_events)),
                    ("Subjects with ≥1 AE", str(sf.subjects_with_any_ae)),
                    ("Total SAEs", str(sf.total_saes)),
                    ("Deaths", str(sf.deaths)),
                    (
                        "Discontinuations due to AE",
                        str(sf.discontinuations_due_to_ae),
                    ),
                    ("Top AEs", sf.top_aes_text),
                    ("Source artefact", sf.derived_from),
                ],
                styles,
            )
        )
    else:
        flow.append(
            Paragraph(
                "[Operator to complete — paste from TLF t-ae-summary + f-ae-frequency]",
                styles["Body"],
            )
        )

    # Sections 13-16 (skeletons / narrative deferred)
    for num, name, body_attr in (
        ("13", "Discussion", "discussion_text"),
        ("14", "Overall Conclusions", "conclusions_text"),
        (
            "15",
            "Tables, Listings, Figures (bundle)",
            None,
        ),
        ("16", "References", None),
    ):
        flow.append(Spacer(1, 12))
        flow.append(Paragraph(f"{num}. {name}", styles["H2"]))
        if body_attr and data.document:
            flow.append(Paragraph(getattr(data.document, body_attr), styles["Body"]))
        else:
            flow.append(
                Paragraph(
                    (
                        f"[Operator to complete — {name}]"
                        if num != "15"
                        else (
                            "Refer to the CDISC submission bundle "
                            "(sdtm/*, adam/*, tlf/*) shipped alongside this CSR."
                        )
                    ),
                    styles["Body"],
                )
            )

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ────────────────────────────────────────────────────────


def build_docx(data: CsrReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    docx = Document()
    docx.core_properties.title = data.title
    docx.add_heading(data.title, level=0)
    header_runs = docx.add_paragraph(
        f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
    ).runs
    if header_runs:
        header_runs[0].font.color.rgb = DOCX_MUTED

    docx.add_paragraph(
        "Sponsor and investigator information marked [SPONSOR INPUT] must "
        "be completed before submission. Narrative sections "
        "(Introduction / Discussion / Overall Conclusions) show "
        "[Operator to complete] placeholders in this slice."
    )

    if data.intake:
        docx.add_heading("Study identity", level=1)
        rows: list[tuple[str, str]] = [
            ("Study id", data.intake.study_id),
            ("Study name", data.intake.study_name),
            ("Sponsor", data.intake.sponsor),
            ("Blinding", data.intake.blinding.replace("_", " ")),
            ("Lock date", data.intake.lock_date or "[SPONSOR INPUT]"),
            (
                "Target jurisdictions",
                ", ".join(data.intake.target_jurisdictions) or "[SPONSOR INPUT]",
            ),
        ]
        t = docx.add_table(rows=len(rows), cols=2)
        t.style = "Light Grid Accent 1"
        for i, (k, v) in enumerate(rows):
            t.cell(i, 0).text = k
            t.cell(i, 0).paragraphs[0].runs[0].bold = True
            t.cell(i, 1).text = v or "—"
        docx.add_paragraph("")

    # Section 1
    docx.add_heading("1. Synopsis", level=1)
    if data.synopsis:
        for label, value in (
            ("Title", data.synopsis.title),
            ("Objectives", data.synopsis.objectives_text),
            ("Methodology", data.synopsis.methodology_text),
            ("Number planned", str(data.synopsis.number_planned)),
            (
                "Number analysed — Safety / Efficacy",
                f"{data.synopsis.number_analysed_safety} / "
                f"{data.synopsis.number_analysed_efficacy}",
            ),
            ("Primary endpoint", data.synopsis.primary_endpoint),
            ("Primary result", data.synopsis.primary_result_description),
            (
                "Source artefact",
                data.synopsis.derived_from,
            ),
            ("Safety overview", data.synopsis.safety_overview),
            ("Conclusions", data.synopsis.conclusions),
        ):
            p = docx.add_paragraph()
            p.add_run(label + ": ").bold = True
            p.add_run(value)

    # Skeleton sections
    for num, name in (
        ("2", "Table of Contents"),
        ("3", "Glossary"),
        ("4", "Ethics"),
        ("5", "Investigators and Study Administrative Structure"),
        ("6", "Introduction"),
        ("7", "Study Objectives"),
        ("8", "Investigational Plan"),
        ("9", "Study Patients"),
    ):
        docx.add_heading(f"{num}. {name}", level=1)
        docx.add_paragraph(f"[Operator to complete — {name}]")

    # Section 10 — Disposition + Demographics
    ds = data.data_sections
    docx.add_heading("10. Study Patients", level=1)
    docx.add_heading("10.1 Subject Disposition", level=2)
    if ds:
        t = docx.add_table(rows=1 + len(ds.disposition.rows), cols=3)
        t.style = "Light Grid Accent 1"
        for j, h in enumerate(("Status", "n", "%")):
            t.cell(0, j).text = h
            t.cell(0, j).paragraphs[0].runs[0].bold = True
        for i, r in enumerate(ds.disposition.rows, start=1):
            t.cell(i, 0).text = r.label
            t.cell(i, 1).text = str(r.n)
            t.cell(i, 2).text = r.pct
        docx.add_paragraph(f"Source: {ds.disposition.derived_from}").runs[0].italic = True
    else:
        docx.add_paragraph(
            "[Operator to complete — paste from TLF t-disposition]"
        )

    docx.add_heading("10.2 Demographics + Baseline Characteristics", level=2)
    if ds:
        t = docx.add_table(rows=1 + len(ds.demographics.rows), cols=3)
        t.style = "Light Grid Accent 1"
        for j, h in enumerate(("Characteristic", "Value", "Detail")):
            t.cell(0, j).text = h
            t.cell(0, j).paragraphs[0].runs[0].bold = True
        for i, demo_row in enumerate(ds.demographics.rows, start=1):
            t.cell(i, 0).text = demo_row.characteristic
            t.cell(i, 1).text = demo_row.value
            t.cell(i, 2).text = demo_row.detail or "—"
        docx.add_paragraph(f"Source: {ds.demographics.derived_from}").runs[0].italic = True
    else:
        docx.add_paragraph(
            "[Operator to complete — paste from TLF t-demographics]"
        )

    # Section 11 — Efficacy
    docx.add_heading("11. Efficacy Evaluation", level=1)
    if ds:
        p = docx.add_paragraph()
        p.add_run("Primary endpoint: ").bold = True
        p.add_run(ds.efficacy.primary_endpoint_text)
        p = docx.add_paragraph()
        p.add_run("Source: ").bold = True
        p.add_run(ds.efficacy.primary_endpoint_derived_from)
        if ds.efficacy.secondary_endpoints:
            p = docx.add_paragraph()
            p.add_run("Secondary endpoints: ").bold = True
            p.add_run("; ".join(ds.efficacy.secondary_endpoints))
        if ds.efficacy.populations_analysed:
            p = docx.add_paragraph()
            p.add_run("Populations analysed: ").bold = True
            p.add_run(", ".join(ds.efficacy.populations_analysed))
    else:
        docx.add_paragraph(
            "[Operator to complete — efficacy from ADTTE + TLF references]"
        )

    # Section 12 — Safety
    docx.add_heading("12. Safety Evaluation", level=1)
    if ds:
        sf = ds.safety
        for label, value in (
            ("Total AE events", str(sf.total_ae_events)),
            ("Subjects with ≥1 AE", str(sf.subjects_with_any_ae)),
            ("Total SAEs", str(sf.total_saes)),
            ("Deaths", str(sf.deaths)),
            ("Discontinuations due to AE", str(sf.discontinuations_due_to_ae)),
            ("Top AEs", sf.top_aes_text),
            ("Source artefact", sf.derived_from),
        ):
            p = docx.add_paragraph()
            p.add_run(label + ": ").bold = True
            p.add_run(value)
    else:
        docx.add_paragraph(
            "[Operator to complete — paste from TLF t-ae-summary + f-ae-frequency]"
        )

    # Sections 13-16
    for num, name, body in (
        (
            "13",
            "Discussion",
            data.document.discussion_text
            if data.document
            else "[Operator to complete — Discussion]",
        ),
        (
            "14",
            "Overall Conclusions",
            data.document.conclusions_text
            if data.document
            else "[Operator to complete — Overall Conclusions]",
        ),
        (
            "15",
            "Tables, Listings, Figures (bundle)",
            (
                "Refer to the CDISC submission bundle "
                "(sdtm/*, adam/*, tlf/*) shipped alongside this CSR."
            ),
        ),
        ("16", "References", "[Operator to complete — References]"),
    ):
        docx.add_heading(f"{num}. {name}", level=1)
        docx.add_paragraph(body)

    out = io.BytesIO()
    docx.save(out)
    return out.getvalue()


__all__ = [
    "CsrReportData",
    "assemble_report_data",
    "build_docx",
    "build_pdf",
]
