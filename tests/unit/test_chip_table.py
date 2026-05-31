"""GRADE chip-table SVG generator — host-side renderer tests.

The chip table is a hand-rolled SVG (same posture as the PRISMA flow
diagram). These tests pin the SVG output shape: one row per
assessment, downgrade/upgrade/certainty colour mapping, observational
upgrade slots gated by study_design.
"""

from __future__ import annotations

import re

from research_assistant.domain.grade import (
    DowngradeReason,
    OutcomeAssessment,
    UpgradeReason,
)
from research_assistant.visualizations import build_grade_chip_svg


def _assessment(
    name: str = "All-cause mortality",
    design: str = "rct",
    rob: str = "none",
    inconsistency: str = "none",
    indirectness: str = "none",
    imprecision: str = "none",
    pub_bias: str = "none",
    large_effect: str | None = None,
    dose_response: str | None = None,
    residual: str | None = None,
) -> OutcomeAssessment:
    return OutcomeAssessment(
        outcome_name=name,
        study_design=design,  # type: ignore[arg-type]
        n_studies=8,
        n_participants=4250,
        effect_estimate="RR 0.72",
        confidence_interval="0.58 to 0.89",
        risk_of_bias=DowngradeReason(level=rob, rationale="x"),  # type: ignore[arg-type]
        inconsistency=DowngradeReason(level=inconsistency, rationale="x"),  # type: ignore[arg-type]
        indirectness=DowngradeReason(level=indirectness, rationale="x"),  # type: ignore[arg-type]
        imprecision=DowngradeReason(level=imprecision, rationale="x"),  # type: ignore[arg-type]
        publication_bias=DowngradeReason(level=pub_bias, rationale="x"),  # type: ignore[arg-type]
        large_effect=(
            UpgradeReason(level=large_effect, rationale="x")  # type: ignore[arg-type]
            if large_effect
            else None
        ),
        dose_response=(
            UpgradeReason(level=dose_response, rationale="x")  # type: ignore[arg-type]
            if dose_response
            else None
        ),
        residual_confounding=(
            UpgradeReason(level=residual, rationale="x")  # type: ignore[arg-type]
            if residual
            else None
        ),
    )


def test_empty_assessments_emits_a_well_formed_svg_skeleton() -> None:
    svg = build_grade_chip_svg([])
    assert svg.startswith("<?xml")
    assert "<svg " in svg
    assert "</svg>" in svg


def test_rct_assessment_renders_high_certainty_chip() -> None:
    svg = build_grade_chip_svg([_assessment()])
    # The certainty chip carries "HIGH" text when all downgrades are "none".
    assert "HIGH" in svg
    # No "−1" / "−2" labels since all downgrades are "none".
    assert "−1" not in svg
    assert "−2" not in svg


def test_serious_downgrade_renders_amber_chip_and_minus_one() -> None:
    svg = build_grade_chip_svg([_assessment(rob="serious")])
    assert "−1" in svg
    # Moderate certainty after one downgrade.
    assert "MODERATE" in svg


def test_very_serious_downgrades_compound_to_very_low() -> None:
    svg = build_grade_chip_svg(
        [
            _assessment(
                rob="very_serious",
                inconsistency="serious",
                indirectness="serious",
            )
        ]
    )
    assert "−2" in svg
    assert "VERY LOW" in svg


def test_rct_design_renders_upgrade_columns_as_na() -> None:
    """Upgrade columns are observational-only; for RCT they read as
    "n/a" (or "—") and use the gray palette."""
    svg = build_grade_chip_svg([_assessment(design="rct")])
    # The chip label for an n/a cell is "—".
    occurrences = svg.count(">—<")
    # 5 downgrade chips (all 'none' = "—") + 3 upgrade chips (n/a = "—") = 8
    assert occurrences >= 8


def test_observational_with_large_effect_upgrades_to_moderate() -> None:
    svg = build_grade_chip_svg([_assessment(design="observational", large_effect="moderate")])
    # Observational starts at low (2). +1 from moderate upgrade → 3 → moderate.
    assert "MODERATE" in svg
    assert "+1" in svg


def test_observational_with_large_upgrade_can_reach_high() -> None:
    """Observational starts at low (2). A 'large' upgrade (+2) brings
    it to high (4) — clamped at the ceiling."""
    svg = build_grade_chip_svg(
        [
            _assessment(
                design="observational",
                large_effect="large",
                dose_response="moderate",
                residual="moderate",
            )
        ]
    )
    # 2 + 2 + 1 + 1 = 6 → clamped to 4 = high.
    assert "HIGH" in svg


def test_multiple_outcomes_render_multiple_rows() -> None:
    svg = build_grade_chip_svg(
        [
            _assessment(name="Outcome A"),
            _assessment(name="Outcome B", rob="serious"),
            _assessment(name="Outcome C", rob="very_serious"),
        ]
    )
    # Outcome labels in the SVG.
    assert "Outcome A" in svg
    assert "Outcome B" in svg
    assert "Outcome C" in svg


def test_svg_uses_canonical_colour_palette() -> None:
    """Verify the green/amber/red GRADE-pro palette is the one emitted."""
    svg = build_grade_chip_svg(
        [
            _assessment(rob="serious", inconsistency="very_serious"),
        ]
    )
    # Cochrane palette: green #d4edda, amber #fff3cd, red #f8d7da.
    assert "#fff3cd" in svg  # amber for serious
    assert "#f8d7da" in svg  # red for very_serious


def test_long_outcome_name_truncates_in_svg() -> None:
    name = "A very long outcome name that exceeds the chip-column width"
    svg = build_grade_chip_svg([_assessment(name=name)])
    # The full name should not appear verbatim — truncation marker present.
    assert "…" in svg


def test_chip_text_uses_no_unescaped_xml() -> None:
    svg = build_grade_chip_svg([_assessment(name="Outcome <with> special & chars")])
    # Special characters in the outcome name should be XML-escaped.
    assert "&lt;with&gt;" in svg
    assert "&amp;" in svg
    # And the raw tag should NOT appear (which would break the XML).
    assert "<with>" not in re.sub(r"<svg[^>]*>", "", svg)
