"""GRADE chip table — host-side SVG generator.

Renders a visual SoF: rows = outcomes, columns = the five downgrade
domains + (for observational studies) three upgrade domains + a final
computed-certainty column. Each cell is a coloured chip mirroring the
GRADE-pro / Cochrane convention:

  Downgrade ratings:
    "none"           → green   (no concern)
    "serious"        → amber   (one downgrade step)
    "very_serious"   → red     (two downgrade steps)

  Upgrade ratings (observational only):
    "none"           → gray    (no upgrade)
    "moderate"       → light blue (+1)
    "large"          → dark blue  (+2)

  Certainty column (computed):
    "high"     → green
    "moderate" → yellow
    "low"      → orange
    "very_low" → red

Built as a hand-rolled SVG (same posture as the PRISMA flow diagram)
so it renders without the sandbox. The caller (grade_drafter
specialist or grade.py report) stitches the SVG bytes into the
GradeDocument's `chip_table_svg` field.
"""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from research_assistant.domain.grade import OutcomeAssessment


# ── Colour palette ──────────────────────────────────────────────────────


_DOWNGRADE_FILL: dict[str, str] = {
    "none": "#d4edda",  # green
    "serious": "#fff3cd",  # amber
    "very_serious": "#f8d7da",  # red
}
_DOWNGRADE_TEXT: dict[str, str] = {
    "none": "#155724",
    "serious": "#856404",
    "very_serious": "#721c24",
}
_DOWNGRADE_LABEL: dict[str, str] = {
    "none": "—",
    "serious": "−1",
    "very_serious": "−2",
}

_UPGRADE_FILL: dict[str, str] = {
    "none": "#e9ecef",  # gray
    "moderate": "#cfe2ff",  # light blue
    "large": "#9ec5fe",  # dark blue
}
_UPGRADE_TEXT: dict[str, str] = {
    "none": "#495057",
    "moderate": "#084298",
    "large": "#052c65",
}
_UPGRADE_LABEL: dict[str, str] = {
    "none": "—",
    "moderate": "+1",
    "large": "+2",
}

_CERTAINTY_FILL: dict[str, str] = {
    "high": "#d4edda",
    "moderate": "#fff3cd",
    "low": "#ffe0b3",
    "very_low": "#f8d7da",
}
_CERTAINTY_TEXT: dict[str, str] = {
    "high": "#155724",
    "moderate": "#856404",
    "low": "#7a3e00",
    "very_low": "#721c24",
}


# ── Layout constants ────────────────────────────────────────────────────


_OUTCOME_COL_WIDTH = 220
_CHIP_WIDTH = 88
_CHIP_HEIGHT = 38
_CHIP_PADDING = 6
_HEADER_HEIGHT = 60
_PADDING = 12

_DOWNGRADE_HEADERS = (
    "Risk of bias",
    "Inconsistency",
    "Indirectness",
    "Imprecision",
    "Pub. bias",
)
_UPGRADE_HEADERS = (
    "Large effect",
    "Dose-response",
    "Residual conf.",
)


def _chip_rect(
    x: float,
    y: float,
    fill: str,
    text_color: str,
    label: str,
    *,
    title: str,
) -> str:
    rx = 6
    cx = x + _CHIP_WIDTH / 2
    cy = y + _CHIP_HEIGHT / 2 + 4
    safe_title = escape(title)
    safe_label = escape(label)
    return (
        f"<g><title>{safe_title}</title>"
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{_CHIP_WIDTH}" '
        f'height="{_CHIP_HEIGHT}" rx="{rx}" ry="{rx}" '
        f'fill="{fill}" stroke="#cccccc" stroke-width="0.5"/>'
        f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
        f'font-family="Helvetica,Arial,sans-serif" font-size="13" '
        f'font-weight="bold" fill="{text_color}">{safe_label}</text>'
        f"</g>"
    )


def _header_cell(x: float, y: float, label: str) -> str:
    safe_label = escape(label)
    cx = x + _CHIP_WIDTH / 2
    cy = y + 22
    return (
        f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
        f'font-family="Helvetica,Arial,sans-serif" font-size="11" '
        f'font-weight="bold" fill="#2D3748">{safe_label}</text>'
    )


def _outcome_label(x: float, y: float, text: str) -> str:
    cy = y + _CHIP_HEIGHT / 2 + 5
    truncated = text if len(text) <= 32 else text[:30] + "…"
    safe = escape(truncated)
    return (
        f'<text x="{x:.1f}" y="{cy:.1f}" '
        f'font-family="Helvetica,Arial,sans-serif" font-size="12" '
        f'fill="#2D3748">{safe}</text>'
    )


def build_grade_chip_svg(assessments: Iterable[OutcomeAssessment]) -> str:
    """Render the chip table as an SVG string.

    All outcomes share the same column layout. Upgrade columns are
    rendered for every row but read as "—" / "n/a" for RCT/mixed designs
    where upgrades don't apply.
    """
    rows = list(assessments)
    n_rows = len(rows)
    n_downgrades = len(_DOWNGRADE_HEADERS)
    n_upgrades = len(_UPGRADE_HEADERS)
    # Certainty column too.
    total_chip_cols = n_downgrades + n_upgrades + 1
    width = (
        _PADDING + _OUTCOME_COL_WIDTH + total_chip_cols * (_CHIP_WIDTH + _CHIP_PADDING) + _PADDING
    )
    height = _PADDING + _HEADER_HEIGHT + n_rows * (_CHIP_HEIGHT + _CHIP_PADDING) + _PADDING

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}">'
        ),
        "<style>text { dominant-baseline: middle; }</style>",
    ]

    # Header row — column titles.
    header_x = _PADDING + _OUTCOME_COL_WIDTH
    header_y = _PADDING + 4
    for label in _DOWNGRADE_HEADERS:
        parts.append(_header_cell(header_x, header_y, label))
        header_x += _CHIP_WIDTH + _CHIP_PADDING
    for label in _UPGRADE_HEADERS:
        parts.append(_header_cell(header_x, header_y, label))
        header_x += _CHIP_WIDTH + _CHIP_PADDING
    parts.append(_header_cell(header_x, header_y, "Certainty"))

    # Outcome rows
    for i, assessment in enumerate(rows):
        row_y = _PADDING + _HEADER_HEIGHT + i * (_CHIP_HEIGHT + _CHIP_PADDING)
        parts.append(_outcome_label(_PADDING, row_y, assessment.outcome_name))
        x = _PADDING + _OUTCOME_COL_WIDTH

        # Downgrade chips.
        for label, domain in zip(
            _DOWNGRADE_HEADERS,
            (
                assessment.risk_of_bias,
                assessment.inconsistency,
                assessment.indirectness,
                assessment.imprecision,
                assessment.publication_bias,
            ),
            strict=False,
        ):
            chip_label = _DOWNGRADE_LABEL.get(domain.level, "?")
            fill = _DOWNGRADE_FILL.get(domain.level, "#ffffff")
            text_color = _DOWNGRADE_TEXT.get(domain.level, "#000000")
            tooltip = f"{label}: {domain.level} — {domain.rationale}"
            parts.append(_chip_rect(x, row_y, fill, text_color, chip_label, title=tooltip))
            x += _CHIP_WIDTH + _CHIP_PADDING

        # Upgrade chips — observational only.
        for label, upgrade in zip(
            _UPGRADE_HEADERS,
            (
                assessment.large_effect,
                assessment.dose_response,
                assessment.residual_confounding,
            ),
            strict=False,
        ):
            if assessment.study_design != "observational" or upgrade is None:
                chip_label = "—"
                fill = "#f1f3f5"
                text_color = "#6c757d"
                tooltip = f"{label}: not applicable (study design = {assessment.study_design})"
            else:
                chip_label = _UPGRADE_LABEL.get(upgrade.level, "?")
                fill = _UPGRADE_FILL.get(upgrade.level, "#ffffff")
                text_color = _UPGRADE_TEXT.get(upgrade.level, "#000000")
                tooltip = f"{label}: {upgrade.level} — {upgrade.rationale}"
            parts.append(_chip_rect(x, row_y, fill, text_color, chip_label, title=tooltip))
            x += _CHIP_WIDTH + _CHIP_PADDING

        # Certainty chip.
        certainty = assessment.certainty
        fill = _CERTAINTY_FILL.get(certainty, "#ffffff")
        text_color = _CERTAINTY_TEXT.get(certainty, "#000000")
        label = certainty.replace("_", " ").upper()
        parts.append(
            _chip_rect(
                x,
                row_y,
                fill,
                text_color,
                label,
                title=f"Certainty: {certainty}",
            )
        )

    parts.append("</svg>")
    return "".join(parts)


__all__ = ["build_grade_chip_svg"]
