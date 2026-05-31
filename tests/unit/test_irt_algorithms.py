"""IRT — simple / block / stratified / Pocock-Simon minimisation."""

from __future__ import annotations

from collections import Counter

import pytest

from research_assistant.randomization.algorithms import (
    canonical_stratum_label,
    generate_permuted_block,
    generate_simple,
    generate_stratified_permuted_block,
    pocock_simon_choose_arm,
)

# ── Helpers ─────────────────────────────────────────────────────────────


def test_canonical_stratum_label_is_alphabetical_by_factor() -> None:
    assert canonical_stratum_label({"sex": "F", "site_id": "S01"}) == "sex=F|site_id=S01"
    assert canonical_stratum_label({"site_id": "S01", "sex": "F"}) == "sex=F|site_id=S01"


# ── Simple ──────────────────────────────────────────────────────────────


def test_generate_simple_is_deterministic_given_seed() -> None:
    a = generate_simple(20, ["A", "B"], seed=42)
    b = generate_simple(20, ["A", "B"], seed=42)
    assert a == b


def test_generate_simple_respects_ratio() -> None:
    """With a 3:1 ratio and a large N, the empirical ratio should
    approximate 3:1 (the random draw won't be exactly 3:1 every time,
    but well within the expected variance)."""
    seq = generate_simple(400, ["A", "B"], ratio=[3, 1], seed=7)
    counts = Counter(seq)
    # Empirically, 100×3/4 = 300 for A; allow ±50 wiggle.
    assert 250 < counts["A"] < 350
    assert 50 < counts["B"] < 150


def test_generate_simple_rejects_invalid_arms() -> None:
    with pytest.raises(ValueError, match="unique"):
        generate_simple(10, ["A", "A"], seed=1)
    with pytest.raises(ValueError, match="At least one"):
        generate_simple(10, [], seed=1)


def test_generate_simple_zero_n_returns_empty() -> None:
    assert generate_simple(0, ["A", "B"], seed=1) == []


# ── Permuted-block ──────────────────────────────────────────────────────


def test_permuted_block_is_balanced_within_each_block() -> None:
    """Block of 4 with arms [A,B] should always contain exactly 2 A + 2 B."""
    seq = generate_permuted_block(40, ["A", "B"], block_sizes=[4], seed=1)
    for start in range(0, 40, 4):
        block = seq[start : start + 4]
        assert Counter(block) == Counter({"A": 2, "B": 2})


def test_permuted_block_supports_ratio() -> None:
    """Block of 6 with [A,B] and ratio [2,1] gives 4 A + 2 B per block."""
    seq = generate_permuted_block(24, ["A", "B"], ratio=[2, 1], block_sizes=[6], seed=2)
    for start in range(0, 24, 6):
        block = seq[start : start + 6]
        assert Counter(block) == Counter({"A": 4, "B": 2})


def test_permuted_block_emits_exactly_n_allocations() -> None:
    seq = generate_permuted_block(17, ["A", "B"], block_sizes=[4, 6], seed=3)
    assert len(seq) == 17


def test_permuted_block_rejects_block_size_not_divisible_by_ratio_sum() -> None:
    with pytest.raises(ValueError, match="not divisible"):
        generate_permuted_block(8, ["A", "B"], ratio=[1, 1], block_sizes=[5], seed=1)


def test_permuted_block_is_deterministic() -> None:
    a = generate_permuted_block(20, ["A", "B"], block_sizes=[4, 6], seed=99)
    b = generate_permuted_block(20, ["A", "B"], block_sizes=[4, 6], seed=99)
    assert a == b


# ── Stratified permuted-block ───────────────────────────────────────────


def test_stratified_permuted_block_emits_one_sequence_per_stratum() -> None:
    expected = {"sex=F|site_id=S01": 8, "sex=M|site_id=S01": 4}
    out = generate_stratified_permuted_block(expected, ["A", "B"], block_sizes=[4], seed=10)
    assert set(out.keys()) == set(expected.keys())
    assert len(out["sex=F|site_id=S01"]) == 8
    assert len(out["sex=M|site_id=S01"]) == 4


def test_stratified_permuted_block_strata_independent_seeds() -> None:
    """Two strata with the same expected_n should produce different
    sequences (independent sub-seeds), not identical clones."""
    expected = {"f_a": 16, "f_b": 16}
    out = generate_stratified_permuted_block(expected, ["A", "B"], block_sizes=[4], seed=11)
    # Sequences should not be identical (sub-seeds differ).
    assert out["f_a"] != out["f_b"]


def test_stratified_permuted_block_balanced_within_each_block() -> None:
    expected = {"f_a": 8}
    out = generate_stratified_permuted_block(expected, ["A", "B"], block_sizes=[4], seed=12)
    seq = out["f_a"]
    for start in range(0, 8, 4):
        assert Counter(seq[start : start + 4]) == Counter({"A": 2, "B": 2})


# ── Pocock-Simon minimisation ───────────────────────────────────────────


def test_minimisation_first_allocation_uses_random_tiebreak() -> None:
    """Empty state → all arms tied → RNG picks one. With seed=1 the
    chosen arm is deterministic for the test."""
    arm, state = pocock_simon_choose_arm(
        None,
        {"sex": "F", "site_id": "S01"},
        ["A", "B"],
        seed=1,
    )
    assert arm in ("A", "B")
    assert state["counts"][arm]["sex"]["F"] == 1
    assert state["counts"][arm]["site_id"]["S01"] == 1
    other = "B" if arm == "A" else "A"
    assert state["counts"][other]["sex"].get("F", 0) == 0


def test_minimisation_balances_strata_under_imbalance() -> None:
    """Seed an imbalance toward A, then verify the next allocation goes
    to B for the same factor value."""
    initial = {
        "counts": {
            "A": {"sex": {"F": 3}, "site_id": {"S01": 3}},
            "B": {"sex": {"F": 0}, "site_id": {"S01": 0}},
        }
    }
    arm, _new = pocock_simon_choose_arm(
        initial,
        {"sex": "F", "site_id": "S01"},
        ["A", "B"],
        seed=5,
    )
    # B's weighted sum is (0+1)+(0+1)=2; A's is (3+1)+(3+1)=8 → B wins.
    assert arm == "B"


def test_minimisation_weights_can_prefer_one_factor() -> None:
    """Heavy weight on `sex` makes the imbalance there dominate."""
    initial = {
        "counts": {
            "A": {"sex": {"F": 0}, "site_id": {"S01": 5}},
            "B": {"sex": {"F": 4}, "site_id": {"S01": 0}},
        }
    }
    # sex weight=10 → A's sex=F+1 score = 10×1 = 10; B's = 10×5 = 50.
    # site_id weight=1 → A's site_id+1 = 6; B's = 1. So A total = 16, B total = 51 → A wins.
    arm, _new = pocock_simon_choose_arm(
        initial,
        {"sex": "F", "site_id": "S01"},
        ["A", "B"],
        weights={"sex": 10.0, "site_id": 1.0},
        seed=2,
    )
    assert arm == "A"


def test_minimisation_state_is_updated_with_assignment() -> None:
    arm, new_state = pocock_simon_choose_arm(
        None,
        {"region": "NA"},
        ["X", "Y", "Z"],
        seed=3,
    )
    assert new_state["counts"][arm]["region"]["NA"] == 1
    others = [a for a in ("X", "Y", "Z") if a != arm]
    for a in others:
        assert new_state["counts"][a]["region"].get("NA", 0) == 0


def test_minimisation_rejects_invalid_probabilistic_p() -> None:
    with pytest.raises(ValueError, match="probabilistic_p"):
        pocock_simon_choose_arm(None, {"f": "v"}, ["A", "B"], probabilistic_p=0.0, seed=1)


def test_minimisation_handles_new_factor_value_gracefully() -> None:
    """When a subject's factor value hasn't been seen yet, the state
    should treat it as 0 — neither arm prefers the new value."""
    initial = {
        "counts": {
            "A": {"site_id": {"S01": 3}},
            "B": {"site_id": {"S01": 3}},
        }
    }
    arm, _ = pocock_simon_choose_arm(initial, {"site_id": "S99"}, ["A", "B"], seed=4)
    # Tied — but doesn't raise.
    assert arm in ("A", "B")
