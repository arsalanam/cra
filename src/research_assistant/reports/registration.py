"""Registration report — assemble persisted registration_drafter state
into a PDF / DOCX the operator pastes into CT.gov PRS + EU CTR (CTIS).

Pipeline mirrors the other report modules:

    Message rows ─▶ assemble_report_data() ─▶ RegistrationReportData
                                                  │
                                                  ├─▶ build_pdf()  ─▶ bytes
                                                  └─▶ build_docx() ─▶ bytes

Renders the CT.gov + EU CTR drafts side-by-side so the operator sees
the cross-registry field mapping at a glance.
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

from research_assistant.domain.registration import (
    CoreFields,
    CtGovDraft,
    EuCtrDraft,
    RegistrationDocument,
    RegistrationIntake,
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
class RegistrationReportData:
    thread_id: str
    title: str
    generated_at: datetime
    intake: RegistrationIntake | None
    core: CoreFields | None
    ctgov: CtGovDraft | None
    euctr: EuCtrDraft | None
    document: RegistrationDocument | None


def _peek_kind(s: str) -> str | None:
    try:
        kind = json.loads(s).get("kind")
    except json.JSONDecodeError:
        return None
    return str(kind) if kind is not None else None


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> RegistrationReportData | None:
    intake: RegistrationIntake | None = None
    core: CoreFields | None = None
    ctgov: CtGovDraft | None = None
    euctr: EuCtrDraft | None = None
    document: RegistrationDocument | None = None

    for msg in messages:
        if msg.role != "assistant" or not msg.final_answer:
            continue
        kind = _peek_kind(msg.final_answer)
        try:
            if kind == "registration_intake":
                intake = RegistrationIntake.model_validate_json(msg.final_answer)
            elif kind == "core_fields":
                core = CoreFields.model_validate_json(msg.final_answer)
            elif kind == "ctgov_draft":
                ctgov = CtGovDraft.model_validate_json(msg.final_answer)
            elif kind == "euctr_draft":
                euctr = EuCtrDraft.model_validate_json(msg.final_answer)
            elif kind == "registration_document":
                document = RegistrationDocument.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed %s turn in message %s", kind, msg.id)

    if not any((intake, core, ctgov, euctr, document)):
        return None

    base_intake = document.intake if document else intake
    title = "Trial registration draft"
    if base_intake and base_intake.brief_title:
        title = f"Registration draft — {base_intake.brief_title[:100]}"

    return RegistrationReportData(
        thread_id=thread_id,
        title=title,
        generated_at=datetime.now(UTC),
        intake=document.intake if document else intake,
        core=document.core if document else core,
        ctgov=document.ctgov if document else ctgov,
        euctr=document.euctr if document else euctr,
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
    t = Table(table_rows, colWidths=[2.2 * inch, 4.6 * inch])
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


def _intake_kv(intake: RegistrationIntake) -> list[tuple[str, str]]:
    return [
        ("Brief title", intake.brief_title),
        ("Official title", intake.official_title),
        ("Study type", intake.study_type.replace("_", " ")),
        ("Primary purpose", intake.primary_purpose.replace("_", " ")),
        ("Phase", intake.phase.replace("_", " ")),
        ("Lead sponsor", f"{intake.lead_sponsor.name} ({intake.lead_sponsor.sponsor_type})"),
        ("Conditions", ", ".join(c.name for c in intake.conditions)),
        ("Brief summary", intake.brief_summary),
    ]


def _ctgov_kv(d: CtGovDraft) -> list[tuple[str, str]]:
    return [
        ("Brief title", d.brief_title),
        ("Official title", d.official_title),
        ("Sponsor protocol id", d.org_study_id or "—"),
        ("Study type", d.study_type.replace("_", " ")),
        ("Primary purpose", d.primary_purpose.replace("_", " ")),
        ("Phase", d.phase.replace("_", " ")),
        ("Allocation", d.allocation.replace("_", " ")),
        ("Intervention model", d.intervention_model.replace("_", " ")),
        ("Masking", d.masking.replace("_", " ")),
        ("Target enrollment", f"{d.target_enrollment} ({d.enrollment_type})"),
        ("Conditions", ", ".join(c.name for c in d.conditions)),
        ("Lead sponsor", f"{d.lead_sponsor.name} ({d.lead_sponsor.sponsor_type})"),
        ("Overall official", d.overall_official_name),
        ("Central contact email", d.central_contact_email),
    ]


def _euctr_kv(d: EuCtrDraft) -> list[tuple[str, str]]:
    return [
        ("Full title", d.full_title),
        ("Public title", d.public_title),
        ("Sponsor protocol code", d.sponsor_protocol_code or "—"),
        ("EU member states", ", ".join(d.iso_basket_codes) or "—"),
        ("Trial type", d.trial_type.replace("_", " ")),
        ("Therapeutic area", d.therapeutic_area),
        ("Phase", d.phase.replace("_", " ")),
        ("Allocation", d.allocation.replace("_", " ")),
        ("Intervention model", d.intervention_model.replace("_", " ")),
        ("Masking", d.masking.replace("_", " ")),
        ("Enrollment EU / global", f"{d.target_enrollment_eu} / {d.target_enrollment_global}"),
        ("Sponsor", f"{d.sponsor.name} ({d.sponsor.sponsor_type})"),
        ("Conditions", ", ".join(c.name for c in d.conditions)),
        ("Sponsor contact email", d.sponsor_contact_email),
        ("Clinical-trial QP email", d.clinical_trial_qp_email),
    ]


def _outcomes_list(items: list[Any], label: str) -> str:
    if not items:
        return f"({label}: none specified)"
    return "<br/>".join(
        f"<b>{o.measure}</b> — {o.description} <i>(at {o.time_frame})</i>"
        for o in items
    )


def _arms_list(items: list[Any]) -> str:
    if not items:
        return "—"
    return "<br/>".join(
        f"<b>{a.label}</b> ({a.role.replace('_', ' ')}): {a.description}"
        for a in items
    )


# ── PDF builder ─────────────────────────────────────────────────────────


def build_pdf(data: RegistrationReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="TRIAL REGISTRATION DRAFT")
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
    flow.append(Paragraph("TRIAL REGISTRATION DRAFT", styles["Eyebrow"]))
    flow.append(Paragraph(data.title, styles["Title"]))
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    flow.append(Paragraph(f"Generated {ts}", styles["Body"]))
    flow.append(Spacer(1, 10))
    flow.append(
        Paragraph(
            "<i>Paste these field values into ClinicalTrials.gov PRS and "
            "EU CTR (CTIS). NCT IDs and CTIS trial numbers are assigned "
            "by the registries on submission — never invented here. "
            "Fields marked [SPONSOR INPUT] must be completed by the "
            "sponsor before submission.</i>",
            styles["Body"],
        )
    )

    if data.intake:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("1. Registration intake", styles["H2"]))
        flow.append(_kv_table(_intake_kv(data.intake), styles))

    if data.core:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("2. Core fields", styles["H2"]))
        flow.append(
            _kv_table(
                [
                    ("Allocation", data.core.allocation.replace("_", " ")),
                    (
                        "Intervention model",
                        data.core.intervention_model.replace("_", " "),
                    ),
                    ("Masking", data.core.masking.replace("_", " ")),
                    (
                        "Target enrollment",
                        f"{data.core.target_enrollment} ({data.core.enrollment_type})",
                    ),
                    ("Arms", _arms_list(data.core.arms)),
                    (
                        "Primary outcomes",
                        _outcomes_list(data.core.primary_outcomes, "primary"),
                    ),
                    (
                        "Secondary outcomes",
                        _outcomes_list(data.core.secondary_outcomes, "secondary"),
                    ),
                    (
                        "Inclusion criteria",
                        "; ".join(data.core.eligibility.inclusion_criteria),
                    ),
                    (
                        "Exclusion criteria",
                        "; ".join(data.core.eligibility.exclusion_criteria),
                    ),
                    (
                        "Age range",
                        f"{data.core.eligibility.minimum_age} – "
                        f"{data.core.eligibility.maximum_age}",
                    ),
                ],
                styles,
            )
        )

    if data.ctgov:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("3a. ClinicalTrials.gov PRS draft", styles["H2"]))
        flow.append(_kv_table(_ctgov_kv(data.ctgov), styles))

    if data.euctr:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("3b. EU CTR (CTIS) draft", styles["H2"]))
        flow.append(_kv_table(_euctr_kv(data.euctr), styles))

    if data.document and data.document.background_paragraph:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("4. Background", styles["H2"]))
        flow.append(Paragraph(data.document.background_paragraph, styles["Body"]))
        if data.document.references:
            flow.append(Spacer(1, 6))
            flow.append(Paragraph("References", styles["H3"]))
            for ref in data.document.references:
                flow.append(Paragraph(f"• {ref}", styles["Body"]))

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ────────────────────────────────────────────────────────


def build_docx(data: RegistrationReportData, images_dir: Path | None = None) -> bytes:
    del images_dir
    docx = Document()
    docx.core_properties.title = data.title
    docx.add_heading(data.title, level=0)
    docx.add_paragraph(
        f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
    ).runs[0].font.color.rgb = DOCX_MUTED

    docx.add_paragraph(
        "Paste these field values into ClinicalTrials.gov PRS and "
        "EU CTR (CTIS). NCT IDs and CTIS trial numbers are assigned "
        "by the registries on submission. Fields marked "
        "[SPONSOR INPUT] must be completed by the sponsor before submission."
    )

    def _table(rows: list[tuple[str, str]]) -> None:
        table = docx.add_table(rows=len(rows), cols=2)
        table.style = "Light Grid Accent 1"
        for i, (k, v) in enumerate(rows):
            cell_k = table.cell(i, 0)
            cell_k.text = k
            cell_k.paragraphs[0].runs[0].bold = True
            table.cell(i, 1).text = v or "—"
        docx.add_paragraph("")

    if data.intake:
        docx.add_heading("1. Registration intake", level=1)
        _table(_intake_kv(data.intake))

    if data.core:
        docx.add_heading("2. Core fields", level=1)
        _table(
            [
                ("Allocation", data.core.allocation.replace("_", " ")),
                (
                    "Intervention model",
                    data.core.intervention_model.replace("_", " "),
                ),
                ("Masking", data.core.masking.replace("_", " ")),
                (
                    "Target enrollment",
                    f"{data.core.target_enrollment} ({data.core.enrollment_type})",
                ),
                (
                    "Inclusion criteria",
                    "; ".join(data.core.eligibility.inclusion_criteria),
                ),
                (
                    "Exclusion criteria",
                    "; ".join(data.core.eligibility.exclusion_criteria),
                ),
            ]
        )

    if data.ctgov:
        docx.add_heading("3a. ClinicalTrials.gov PRS draft", level=1)
        _table(_ctgov_kv(data.ctgov))

    if data.euctr:
        docx.add_heading("3b. EU CTR (CTIS) draft", level=1)
        _table(_euctr_kv(data.euctr))

    if data.document and data.document.background_paragraph:
        docx.add_heading("4. Background", level=1)
        docx.add_paragraph(data.document.background_paragraph)
        if data.document.references:
            docx.add_heading("References", level=2)
            for ref in data.document.references:
                p = docx.add_paragraph(ref, style="List Bullet")
                p.runs[0].font.size = Pt(10)
                p.runs[0].font.color.rgb = DOCX_NAVY

    out = io.BytesIO()
    docx.save(out)
    return out.getvalue()


__all__ = [
    "RegistrationReportData",
    "assemble_report_data",
    "build_docx",
    "build_pdf",
]
