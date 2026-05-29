"""Shared PDF + DOCX styling for downloadable reports.

Every report (meta-analysis, sr_protocol, risk_of_bias) reuses the same
NAVY/TEAL/ACCENT palette + heading styles + page decorations so the docs
feel like they come from the same product. Per-report modules import these
constants and helpers rather than redefining them, which:

  - guarantees brand consistency across reports;
  - keeps each report module focused on its content/layout, not boilerplate;
  - makes a future palette tweak a one-file change.
"""

from __future__ import annotations

from typing import Any

from docx.shared import RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch

# ── Brand palette (PDF — reportlab Color objects) ────────────────────────

NAVY = colors.HexColor("#0F2A47")
TEAL = colors.HexColor("#1E6E8C")
ACCENT = colors.HexColor("#2A9D8F")
SLATE = colors.HexColor("#2D3748")
MUTED = colors.HexColor("#6B7280")
LIGHT = colors.HexColor("#F7FAFC")
BORDER = colors.HexColor("#E2E8F0")
AMBER = colors.HexColor("#D9A84A")  # for [USER INPUT NEEDED] placeholders

# ── Brand palette (DOCX — python-docx RGB objects) ───────────────────────

DOCX_NAVY = RGBColor(0x0F, 0x2A, 0x47)
DOCX_TEAL = RGBColor(0x1E, 0x6E, 0x8C)
DOCX_ACCENT = RGBColor(0x2A, 0x9D, 0x8F)
DOCX_MUTED = RGBColor(0x6B, 0x72, 0x80)
DOCX_AMBER = RGBColor(0xD9, 0xA8, 0x4A)


# ── PDF paragraph styles ─────────────────────────────────────────────────


def make_pdf_styles() -> dict[str, ParagraphStyle]:
    """Return the shared ParagraphStyle dictionary all reports use."""
    base = getSampleStyleSheet()
    return {
        "Title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "Eyebrow": ParagraphStyle(
            "Eyebrow",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10,
            textColor=ACCENT,
            spaceAfter=4,
        ),
        "H2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=TEAL,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "H3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=NAVY,
            spaceBefore=8,
            spaceAfter=2,
        ),
        "Body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=SLATE,
            spaceAfter=4,
        ),
        "Quote": ParagraphStyle(
            "Quote",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=10.5,
            leading=15,
            textColor=NAVY,
            leftIndent=10,
            spaceAfter=6,
        ),
        "Stat": ParagraphStyle(
            "Stat",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=14,
            textColor=NAVY,
            spaceAfter=2,
        ),
        "Het": ParagraphStyle(
            "Het",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=MUTED,
            spaceAfter=4,
        ),
        "Cell": ParagraphStyle(
            "Cell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            textColor=SLATE,
        ),
        "CellHead": ParagraphStyle(
            "CellHead",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=colors.white,
        ),
        "Amber": ParagraphStyle(
            "Amber",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=9.5,
            leading=12,
            textColor=AMBER,
            spaceAfter=4,
        ),
    }


# ── PDF page decorations ─────────────────────────────────────────────────


def make_page_decorations(footer_label: str) -> Any:
    """Return an onPage callback for ReportLab that draws the standard CRA
    header (accent rule + label) and the standard footer (page number + the
    given doc-specific footer label, e.g. 'Meta-analysis report').
    """

    def _onpage(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        w, _h = LETTER
        h = LETTER[1]
        # Top accent rule + brand label
        canvas.setFillColor(ACCENT)
        canvas.rect(0.6 * inch, h - 0.6 * inch, 0.7 * inch, 0.05 * inch, fill=1, stroke=0)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(1.4 * inch, h - 0.6 * inch + 0.01 * inch, "CLINICAL RESEARCH ASSISTANT")
        # Footer: doc-specific label on the left, page number on the right
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(0.6 * inch, 0.45 * inch, footer_label)
        canvas.drawRightString(w - 0.6 * inch, 0.45 * inch, f"Page {doc.page}")
        canvas.restoreState()

    return _onpage


# ── DOCX helpers ─────────────────────────────────────────────────────────


def docx_set_heading_color(paragraph: Any, color: RGBColor) -> None:
    """Re-color every run inside a python-docx heading paragraph.

    Default heading styles render in blue/black; CRA reports use the brand
    palette (NAVY for top-level, TEAL for sections), so each heading needs
    an explicit color pass after `doc.add_heading(...)`.
    """
    for run in paragraph.runs:
        run.font.color.rgb = color
