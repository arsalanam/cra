"""sample_size tool — formula correctness vs textbook values.

The four closed-form helpers should round-trip standard textbook
examples within rounding. The Z-vs-t refinement in two_means and paired
means our numbers may differ from pure-Z formulas by ±1 — that's
correct, not a regression.
"""

from __future__ import annotations

import pytest

from research_assistant.tools.data_science.sample_size import (
    sample_size_paired,
    sample_size_time_to_event,
    sample_size_two_means,
    sample_size_two_proportions,
)

# ── Two proportions ─────────────────────────────────────────────────────


def test_two_proportions_textbook_ppi_example() -> None:
    """Fleiss §4.3-shaped example: control 10%, intervention 5%, two-sided
    alpha=0.05, power=0.80, balanced — ~435 per arm. Our variance-under-H1
    formula gives ~432; both within the rounding band of the published
    textbook number.
    """
    r = sample_size_two_proportions(
        p_control=0.10,
        p_intervention=0.05,
        alpha=0.05,
        power=0.80,
        allocation_ratio=1.0,
    )
    assert 420 <= r["n_per_arm_control"] <= 450
    assert r["n_per_arm_control"] == r["n_per_arm_intervention"]  # balanced
    assert r["n_total"] == 2 * r["n_per_arm_control"]
    assert r["events_required"] is None
    assert r["outcome_type"] == "two_proportions"
    assert "Fleiss" in r["formula_reference"]


def test_two_proportions_dropout_inflation_lifts_n() -> None:
    base = sample_size_two_proportions(p_control=0.10, p_intervention=0.05)
    with_dropout = sample_size_two_proportions(
        p_control=0.10, p_intervention=0.05, dropout_rate=0.20
    )
    # 20% dropout should inflate by ~25% (1/(1-0.2) = 1.25).
    assert with_dropout["n_per_arm_control"] > base["n_per_arm_control"]
    ratio = with_dropout["n_per_arm_control"] / base["n_per_arm_control"]
    assert 1.20 <= ratio <= 1.30


def test_two_proportions_rejects_equal_rates() -> None:
    with pytest.raises(ValueError, match="cannot be equal"):
        sample_size_two_proportions(p_control=0.10, p_intervention=0.10)


def test_two_proportions_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="must be in"):
        sample_size_two_proportions(p_control=1.0, p_intervention=0.5)
    with pytest.raises(ValueError, match="must be in"):
        sample_size_two_proportions(p_control=-0.1, p_intervention=0.5)


# ── Two means ───────────────────────────────────────────────────────────


def test_two_means_cohen_medium_effect() -> None:
    """Cohen's d=0.5 (medium effect), two-sided alpha=0.05, power=0.80 —
    n≈64 per arm (Cohen 1988, Table 2.4.1). With the t refinement we may
    land 63–65."""
    r = sample_size_two_means(
        mean_control=0.0,
        mean_intervention=0.5,
        standard_deviation=1.0,
    )
    assert 60 <= r["n_per_arm_control"] <= 68
    assert abs(r["inputs"]["cohens_d"] - 0.5) < 1e-9


def test_two_means_cohen_large_effect() -> None:
    """d=0.8 — N≈26 per arm."""
    r = sample_size_two_means(
        mean_control=0.0,
        mean_intervention=0.8,
        standard_deviation=1.0,
    )
    assert 24 <= r["n_per_arm_control"] <= 30


def test_two_means_rejects_zero_sd() -> None:
    with pytest.raises(ValueError, match="positive"):
        sample_size_two_means(
            mean_control=0.0,
            mean_intervention=1.0,
            standard_deviation=0.0,
        )


# ── Time-to-event ───────────────────────────────────────────────────────


def test_time_to_event_schoenfeld_known_example() -> None:
    """HR=0.7, two-sided alpha=0.05, power=0.80 — Schoenfeld events ≈ 247.
    Collett §10.5 reports 247 for this exact case."""
    r = sample_size_time_to_event(
        hazard_ratio=0.7,
        control_survival_at_horizon=0.5,
    )
    assert 240 <= (r["events_required"] or 0) <= 260
    # Per-arm N must exceed events / (control event prob ≈ 0.5)
    assert r["n_per_arm_control"] > (r["events_required"] or 0) // 2


def test_time_to_event_smaller_hr_needs_fewer_events() -> None:
    """A bigger effect (HR farther from 1) needs fewer events to detect."""
    big_effect = sample_size_time_to_event(
        hazard_ratio=0.5,
        control_survival_at_horizon=0.5,
    )
    small_effect = sample_size_time_to_event(
        hazard_ratio=0.9,
        control_survival_at_horizon=0.5,
    )
    assert big_effect["events_required"] < small_effect["events_required"]


def test_time_to_event_rejects_hr_one() -> None:
    with pytest.raises(ValueError, match="≠ 1"):
        sample_size_time_to_event(
            hazard_ratio=1.0,
            control_survival_at_horizon=0.5,
        )


# ── Paired ──────────────────────────────────────────────────────────────


def test_paired_cohen_medium_effect() -> None:
    """Paired d=0.5 → N≈34 (Cohen 1988 Table 2.5.1). With t refinement we
    may land 33–35."""
    r = sample_size_paired(mean_difference=0.5, sd_of_difference=1.0)
    assert 30 <= r["n_total"] <= 38


def test_paired_rejects_zero_difference() -> None:
    with pytest.raises(ValueError, match="no detectable effect"):
        sample_size_paired(mean_difference=0.0, sd_of_difference=1.0)


# ── Common knobs ────────────────────────────────────────────────────────


def test_power_increase_lifts_n() -> None:
    """Going from 80% to 90% power should require more participants."""
    r80 = sample_size_two_proportions(p_control=0.10, p_intervention=0.05, power=0.80)
    r90 = sample_size_two_proportions(p_control=0.10, p_intervention=0.05, power=0.90)
    assert r90["n_per_arm_control"] > r80["n_per_arm_control"]


def test_alpha_tighten_lifts_n() -> None:
    """Going from alpha=0.05 to 0.01 should require more participants."""
    r05 = sample_size_two_proportions(p_control=0.10, p_intervention=0.05, alpha=0.05)
    r01 = sample_size_two_proportions(p_control=0.10, p_intervention=0.05, alpha=0.01)
    assert r01["n_per_arm_control"] > r05["n_per_arm_control"]


def test_one_sided_vs_two_sided_lowers_n() -> None:
    two_sided = sample_size_two_proportions(p_control=0.10, p_intervention=0.05)
    one_sided = sample_size_two_proportions(p_control=0.10, p_intervention=0.05, one_sided=True)
    assert one_sided["n_per_arm_control"] < two_sided["n_per_arm_control"]


def test_allocation_imbalance_changes_arm_sizes() -> None:
    """2:1 intervention:control means intervention arm is ~2x the control."""
    r = sample_size_two_proportions(
        p_control=0.10,
        p_intervention=0.05,
        allocation_ratio=2.0,
    )
    # Intervention arm should be ~2x the control (within rounding)
    ratio = r["n_per_arm_intervention"] / r["n_per_arm_control"]
    assert 1.8 <= ratio <= 2.2
