"""FDA 3500A IND safety report drafter (top-6 #4).

The 3500A is the FDA's MedWatch mandatory-reporting form for adverse
drug experiences (21 CFR §314.80). This module renders a DRAFT — the
sponsor's regulatory affairs team still reviews + submits via FDA
gateway / paper. The draft fills every field the platform can capture
from the AE record + study metadata; any field that needs sponsor /
investigator input the platform doesn't track is left blank with a
"[SPONSOR INPUT]" marker rather than fabricated.

Pipeline mirrors the other report modules:

    AdverseEvent row + Subject + Deployment ─▶ assemble_3500a_data()
                                                       │
                                                       ├─▶ build_pdf()  ─▶ bytes
                                                       └─▶ build_docx() ─▶ bytes

PHI minimisation: only the `subject_code` is included — never the
participant's name or direct identifiers. The clinical-data store
already enforces this (Subject has subject_code, not name); the report
just inherits that posture.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Pt
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

from research_assistant.reports._shared_styles import (
    BORDER,
    DOCX_MUTED,
    DOCX_NAVY,
    LIGHT,
    make_page_decorations,
    make_pdf_styles,
)

logger = logging.getLogger(__name__)


SPONSOR_INPUT = "[SPONSOR INPUT REQUIRED]"


@dataclass
class Sae3500aData:
    """The 3500A field surface the platform fills + the ones it can't."""

    ae_id: str
    generated_at: datetime

    # ── A. Patient information (PHI-minimised) ───────────────────────────
    subject_code: str
    age_at_event: str  # often unknown if DOB isn't captured — string for blanks
    sex: str

    # ── B. Adverse event / product problem ───────────────────────────────
    aex_term: str
    meddra_pt: str | None
    start_date: datetime
    end_date: datetime | None
    severity_grade: int
    outcome: str
    is_serious: bool
    serious_reasons: list[str]
    narrative: str

    # ── C. Suspect product(s) ────────────────────────────────────────────
    product_name: str
    deployment_name: str
    research_study_id: str

    # ── D. Reporter information ──────────────────────────────────────────
    reporter_sub: str | None
    reporter_role: str = "Investigator (per platform record)"

    # ── Sponsor-supplied fields (left blank with a placeholder) ──────────
    sponsor_name: str = field(default=SPONSOR_INPUT)
    ind_number: str = field(default=SPONSOR_INPUT)
    nda_or_bla_number: str = field(default=SPONSOR_INPUT)
    investigator_name: str = field(default=SPONSOR_INPUT)
    investigator_address: str = field(default=SPONSOR_INPUT)


def _peek_serious_reasons(json_str: str) -> list[str]:
    import json as _json

    try:
        v = _json.loads(json_str or "[]")
    except (_json.JSONDecodeError, TypeError):
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def assemble_3500a_data(
    *,
    ae: object,
    subject: object,
    deployment: object,
    research_study_name: str | None = None,
) -> Sae3500aData:
    """Project the AE + subject + deployment rows into the 3500A field set.

    Pure dataclass output so the PDF + DOCX builders are trivially
    unit-testable. The endpoint glue lives in `web/edc.py` (or wherever
    the report endpoint ends up wired).
    """
    # Tolerate missing attrs — different deployment models / mock objects
    # come through during testing. Use Any so the call sites stay readable
    # without ceremony casts.
    def g(obj: object, attr: str, default: Any = "") -> Any:
        return getattr(obj, attr, default)

    sex = str(g(subject, "sex") or "")
    age = str(g(subject, "age_at_event") or "")
    subj_code = str(g(subject, "subject_code") or "")
    product = research_study_name or str(g(deployment, "name") or SPONSOR_INPUT)
    severity_raw = g(ae, "severity_grade", 0)
    try:
        severity = int(severity_raw or 0)
    except (TypeError, ValueError):
        severity = 0
    meddra_raw = g(ae, "meddra_pt", None)
    reporter_raw = g(ae, "recorded_by", None)

    return Sae3500aData(
        ae_id=str(g(ae, "id")),
        generated_at=datetime.now(UTC),
        subject_code=subj_code,
        age_at_event=age or "(not captured)",
        sex=sex or "(not captured)",
        aex_term=str(g(ae, "term_text")),
        meddra_pt=str(meddra_raw) if meddra_raw else None,
        start_date=g(ae, "start_date", datetime.now(UTC)),
        end_date=g(ae, "end_date", None) or None,
        severity_grade=severity,
        outcome=str(g(ae, "outcome")),
        is_serious=bool(g(ae, "is_serious", False)),
        serious_reasons=_peek_serious_reasons(
            str(g(ae, "serious_reasons_json", "[]"))
        ),
        narrative=str(g(ae, "narrative") or "(narrative not provided)"),
        product_name=product,
        deployment_name=str(g(deployment, "name") or SPONSOR_INPUT),
        research_study_id=str(g(deployment, "research_study_id") or SPONSOR_INPUT),
        reporter_sub=str(reporter_raw) if reporter_raw else None,
    )


# ── PDF builder ──────────────────────────────────────────────────────────


def _kv(rows: Iterable[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> Table:
    table_rows = [
        [
            Paragraph(f"<b>{k}</b>", styles["Cell"]),
            Paragraph(v or "—", styles["Cell"]),
        ]
        for k, v in rows
    ]
    t = Table(table_rows, colWidths=[1.9 * inch, 4.9 * inch])
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


def build_pdf(data: Sae3500aData, images_dir: Path) -> bytes:
    """Render the 3500A draft as a manuscript-style PDF.

    `images_dir` is unused (the form has no embedded figures) but kept
    for signature parity with the other report builders.
    """
    del images_dir
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="FDA 3500A IND SAFETY REPORT — DRAFT")
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.7 * inch,
        title=f"FDA 3500A draft — AE {data.ae_id[:8]}",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decor)])

    flow: list[object] = []
    flow.append(Paragraph("FDA 3500A IND SAFETY REPORT — DRAFT", styles["Eyebrow"]))
    flow.append(Paragraph("Adverse Event Report", styles["Title"]))
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    flow.append(
        Paragraph(
            f"Generated {ts}  ·  AE {data.ae_id[:8]}",
            styles["Body"],
        )
    )
    flow.append(Spacer(1, 12))
    flow.append(
        Paragraph(
            f"<i>This draft is generated from the platform's clinical-data store. "
            f"Fields marked {SPONSOR_INPUT!r} must be completed by sponsor "
            f"regulatory affairs before submission. Submission via FDA gateway "
            f"or paper is not handled by this platform.</i>",
            styles["Body"],
        )
    )

    flow.append(Spacer(1, 10))
    flow.append(Paragraph("A. Patient information", styles["H2"]))
    flow.append(
        _kv(
            [
                ("Subject code", data.subject_code or SPONSOR_INPUT),
                ("Age at event", data.age_at_event),
                ("Sex", data.sex),
            ],
            styles,
        )
    )

    flow.append(Spacer(1, 10))
    flow.append(Paragraph("B. Adverse event", styles["H2"]))
    flow.append(
        _kv(
            [
                ("Event term (verbatim)", data.aex_term),
                ("MedDRA Preferred Term", data.meddra_pt or "(not yet coded)"),
                ("Onset date", data.start_date.strftime("%Y-%m-%d")),
                (
                    "Resolution date",
                    data.end_date.strftime("%Y-%m-%d") if data.end_date else "Ongoing",
                ),
                ("CTCAE severity grade", str(data.severity_grade)),
                ("Outcome", data.outcome.replace("_", " ")),
                ("Serious", "Yes" if data.is_serious else "No"),
                (
                    "Serious reason(s)",
                    ", ".join(r.replace("_", " ") for r in data.serious_reasons) or "—",
                ),
            ],
            styles,
        )
    )

    flow.append(Spacer(1, 10))
    flow.append(Paragraph("Narrative", styles["H3"]))
    flow.append(Paragraph(data.narrative, styles["Body"]))

    flow.append(Spacer(1, 10))
    flow.append(Paragraph("C. Suspect product(s)", styles["H2"]))
    flow.append(
        _kv(
            [
                ("Product / intervention", data.product_name),
                ("Deployment", data.deployment_name),
                ("Research study id", data.research_study_id),
                ("Sponsor", data.sponsor_name),
                ("IND number", data.ind_number),
                ("NDA / BLA number", data.nda_or_bla_number),
            ],
            styles,
        )
    )

    flow.append(Spacer(1, 10))
    flow.append(Paragraph("D. Reporter information", styles["H2"]))
    flow.append(
        _kv(
            [
                ("Reporter (platform sub)", data.reporter_sub or SPONSOR_INPUT),
                ("Reporter role", data.reporter_role),
                ("Investigator name", data.investigator_name),
                ("Investigator address", data.investigator_address),
            ],
            styles,
        )
    )

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ─────────────────────────────────────────────────────────


def _docx_kv_table(docx: Any, rows: list[tuple[str, str]]) -> None:
    tbl = docx.add_table(rows=len(rows), cols=2)
    tbl.style = "Light Grid Accent 1"
    for i, (k, v) in enumerate(rows):
        tbl.cell(i, 0).text = k
        tbl.cell(i, 1).text = v or "—"


def build_docx(data: Sae3500aData, images_dir: Path) -> bytes:
    del images_dir
    docx = Document()
    docx.add_paragraph().add_run("FDA 3500A IND SAFETY REPORT — DRAFT").bold = True
    p = docx.add_paragraph()
    run = p.add_run("Adverse Event Report")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = DOCX_NAVY
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    meta = docx.add_paragraph()
    meta_run = meta.add_run(f"Generated {ts}  ·  AE {data.ae_id[:8]}")
    meta_run.font.color.rgb = DOCX_MUTED
    docx.add_paragraph(
        f"This draft is generated from the platform's clinical-data store. "
        f"Fields marked {SPONSOR_INPUT!r} must be completed by sponsor "
        f"regulatory affairs before submission."
    )

    docx.add_heading("A. Patient information", level=2)
    _docx_kv_table(
        docx,
        [
            ("Subject code", data.subject_code or SPONSOR_INPUT),
            ("Age at event", data.age_at_event),
            ("Sex", data.sex),
        ],
    )

    docx.add_heading("B. Adverse event", level=2)
    _docx_kv_table(
        docx,
        [
            ("Event term (verbatim)", data.aex_term),
            ("MedDRA Preferred Term", data.meddra_pt or "(not yet coded)"),
            ("Onset date", data.start_date.strftime("%Y-%m-%d")),
            (
                "Resolution date",
                data.end_date.strftime("%Y-%m-%d") if data.end_date else "Ongoing",
            ),
            ("CTCAE severity grade", str(data.severity_grade)),
            ("Outcome", data.outcome.replace("_", " ")),
            ("Serious", "Yes" if data.is_serious else "No"),
            (
                "Serious reason(s)",
                ", ".join(r.replace("_", " ") for r in data.serious_reasons) or "—",
            ),
        ],
    )
    docx.add_heading("Narrative", level=3)
    docx.add_paragraph(data.narrative)

    docx.add_heading("C. Suspect product(s)", level=2)
    _docx_kv_table(
        docx,
        [
            ("Product / intervention", data.product_name),
            ("Deployment", data.deployment_name),
            ("Research study id", data.research_study_id),
            ("Sponsor", data.sponsor_name),
            ("IND number", data.ind_number),
            ("NDA / BLA number", data.nda_or_bla_number),
        ],
    )

    docx.add_heading("D. Reporter information", level=2)
    _docx_kv_table(
        docx,
        [
            ("Reporter (platform sub)", data.reporter_sub or SPONSOR_INPUT),
            ("Reporter role", data.reporter_role),
            ("Investigator name", data.investigator_name),
            ("Investigator address", data.investigator_address),
        ],
    )

    buf = io.BytesIO()
    docx.save(buf)
    return buf.getvalue()


__all__ = [
    "SPONSOR_INPUT",
    "Sae3500aData",
    "assemble_3500a_data",
    "build_docx",
    "build_pdf",
]
