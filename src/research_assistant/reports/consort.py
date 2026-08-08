"""CONSORT 2010 enrolment flow-diagram PDF, built from the recruitment funnel.

The screening log already computes the four canonical funnel counts
(screened → eligible → consented → enrolled) plus a per-reason
screen-failure breakdown (`ClinicalRepository.recruitment_funnel`). This
renders the *Enrolment* portion of the CONSORT 2010 flow diagram from those
counts, so the operator no longer has to paste numbers into the trial-stats
specialist to get a compliant figure.

Scope is deliberately the enrolment block only (Assessed → Excluded →
Enrolled). The downstream allocation / follow-up / analysis blocks need
arm-level accounting the screening log doesn't hold; those stay with the
trial-stats specialist.

    recruitment_funnel(...) ─▶ build_consort_flow_pdf(...) ─▶ bytes
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
)

from ..persistence.clinical.recruitment_terminology import CONSORT_EXCLUSION_REASONS
from ._shared_styles import (
    BORDER,
    MUTED,
    NAVY,
    SLATE,
    make_page_decorations,
    make_pdf_styles,
)

# Diagram geometry (points). The drawing is sized to the document frame
# width; heights are computed from the number of exclusion-reason lines.
_DIAGRAM_W = 6.6 * inch
_MAIN_BOX_W = 3.0 * inch
_EXCL_BOX_W = 3.1 * inch
_LINE_H = 12.0
_PAD = 8.0
_TITLE_FS = 10.0
_LINE_FS = 8.0


def _humanise_reason(code: str) -> str:
    """A short human label for an exclusion-reason code (falls back to the
    de-underscored code when it isn't in the canonical codebook)."""
    if code in CONSORT_EXCLUSION_REASONS:
        # Use the code itself, humanised — the codebook value is a full
        # sentence, too long for a diagram line.
        return code.replace("_", " ")
    return code.replace("_", " ")


def _box(
    x: float,
    y_top: float,
    w: float,
    title: str,
    lines: list[str],
    *,
    fill: Any,
) -> tuple[list[Any], float]:
    """Return (shapes, height) for a bordered box whose top-left is (x, y_top).

    `title` is bold; `lines` are rendered below it at the smaller size. The
    box height is derived from the line count so callers can chain boxes.
    """
    n = len(lines)
    height = _PAD + _TITLE_FS + (n * _LINE_H) + _PAD
    shapes: list[Any] = [
        Rect(
            x,
            y_top - height,
            w,
            height,
            strokeColor=BORDER,
            strokeWidth=1.0,
            fillColor=fill,
        ),
        String(
            x + _PAD,
            y_top - _PAD - _TITLE_FS + 2,
            title,
            fontName="Helvetica-Bold",
            fontSize=_TITLE_FS,
            fillColor=NAVY,
        ),
    ]
    ly = y_top - _PAD - _TITLE_FS - _LINE_H + 2
    for line in lines:
        shapes.append(
            String(
                x + _PAD,
                ly,
                line,
                fontName="Helvetica",
                fontSize=_LINE_FS,
                fillColor=SLATE,
            )
        )
        ly -= _LINE_H
    return shapes, height


def _arrow(x: float, y0: float, y1: float) -> list[Any]:
    """A downward vertical arrow from y0 (top) to y1 (bottom) at column x."""
    head = 4.0
    return [
        Line(x, y0, x, y1, strokeColor=MUTED, strokeWidth=1.2),
        Polygon(
            points=[x - head, y1 + head, x + head, y1 + head, x, y1],
            fillColor=MUTED,
            strokeColor=MUTED,
        ),
    ]


def _elbow_arrow(x0: float, y: float, x1: float) -> list[Any]:
    """A horizontal arrow from (x0, y) rightwards to (x1, y)."""
    head = 4.0
    return [
        Line(x0, y, x1, y, strokeColor=MUTED, strokeWidth=1.2),
        Polygon(
            points=[x1 - head, y - head, x1 - head, y + head, x1, y],
            fillColor=MUTED,
            strokeColor=MUTED,
        ),
    ]


def _build_drawing(funnel: dict[str, Any]) -> Drawing:
    totals = funnel.get("totals", {})
    screened = int(totals.get("screened", 0))
    eligible = int(totals.get("eligible", 0))
    consented = int(totals.get("consented", 0))
    enrolled = int(totals.get("enrolled", 0))

    # Exclusion arithmetic — clamp at 0 so any out-of-order data anomaly
    # can't produce a negative count on the figure.
    not_eligible = max(0, screened - eligible)
    declined = max(0, eligible - consented)
    other_excl = max(0, consented - enrolled)
    excluded_total = max(0, screened - enrolled)

    reasons: dict[str, int] = funnel.get("screen_failures_by_reason", {}) or {}

    # Assemble the Excluded box lines.
    excl_lines = [f"Not meeting inclusion criteria  (n={not_eligible})"]
    for code, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
        excl_lines.append(f"    - {_humanise_reason(code)}: {count}")
    excl_lines.append(f"Declined to participate  (n={declined})")
    excl_lines.append(f"Other reasons  (n={other_excl})")

    # Column geometry.
    main_x = 0.0
    main_cx = main_x + _MAIN_BOX_W / 2.0
    excl_x = _DIAGRAM_W - _EXCL_BOX_W
    gap = 26.0  # vertical gap between stacked boxes

    shapes: list[Any] = []

    # Compute box heights first (they depend only on line counts) so we can
    # lay the column out top-down.
    assessed_lines: list[str] = []
    _, h_assessed = _box(main_x, 0, _MAIN_BOX_W, "", assessed_lines, fill=None)
    _, h_excl = _box(excl_x, 0, _EXCL_BOX_W, "", excl_lines, fill=None)
    enrolled_lines: list[str] = []
    _, h_enrolled = _box(main_x, 0, _MAIN_BOX_W, "", enrolled_lines, fill=None)

    total_h = h_assessed + gap + max(h_excl, gap + h_enrolled) + gap
    top = total_h

    # Assessed box.
    s, _ = _box(
        main_x,
        top,
        _MAIN_BOX_W,
        f"Assessed for eligibility  (n={screened})",
        [],
        fill=None,
    )
    shapes += s
    assessed_bottom = top - h_assessed

    # Enrolled box at the bottom of the main column.
    enrolled_top = assessed_bottom - gap
    s, _ = _box(
        main_x,
        enrolled_top,
        _MAIN_BOX_W,
        f"Enrolled  (n={enrolled})",
        [],
        fill=None,
    )
    shapes += s

    # Vertical arrow Assessed -> Enrolled.
    shapes += _arrow(main_cx, assessed_bottom, enrolled_top)

    # Excluded box on the right, vertically centred on the arrow.
    mid_y = (assessed_bottom + enrolled_top) / 2.0
    excl_top = mid_y + h_excl / 2.0
    s, _ = _box(
        excl_x,
        excl_top,
        _EXCL_BOX_W,
        f"Excluded  (n={excluded_total})",
        excl_lines,
        fill=None,
    )
    shapes += s
    # Elbow arrow from the vertical line out to the Excluded box.
    shapes += _elbow_arrow(main_cx, mid_y, excl_x)

    drawing = Drawing(_DIAGRAM_W, total_h)
    for shape in shapes:
        drawing.add(shape)
    return drawing


def build_consort_flow_pdf(
    funnel: dict[str, Any],
    *,
    deployment_name: str,
    generated_at: datetime,
) -> bytes:
    """Render the CONSORT enrolment flow diagram for a deployment's funnel."""
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="CONSORT 2010 ENROLMENT FLOW")
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.7 * inch,
        title=f"CONSORT enrolment flow — {deployment_name}",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decor)])

    ts = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    flow: list[Any] = [
        Paragraph("CONSORT 2010 ENROLMENT FLOW", styles["Eyebrow"]),
        Paragraph(deployment_name, styles["Title"]),
        Paragraph(f"Generated {ts}  ·  Enrolment block only", styles["Body"]),
        Spacer(1, 18),
        _build_drawing(funnel),
        Spacer(1, 16),
        Paragraph(
            "<i>Derived from the screening log's recruitment funnel. Counts "
            "are cumulative to the generation time. The allocation, follow-up "
            "and analysis blocks of the CONSORT diagram require arm-level data "
            "held by the trial-statistics workflow and are not drawn here.</i>",
            styles["Body"],
        ),
    ]
    doc.build(flow)
    return buf.getvalue()


__all__ = ["build_consort_flow_pdf"]
