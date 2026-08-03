"""Randomisation algorithms — simple / block / stratified / Pocock-Simon.

All algorithms are pure functions:
  • deterministic given the seed,
  • DB-agnostic (no model imports),
  • return either a flat list[arm] (simple/block), a per-stratum
    map (stratified), or — for minimisation — the chosen arm + the
    updated running-counts state.

The repository wraps these with persistence + the audit trail.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import TypedDict

# A canonical stratum-key (factor name → value) plus the assembled
# label the repository persists.
StratumKey = tuple[tuple[str, str], ...]


class MinimisationState(TypedDict):
    """Running per-arm × per-factor × per-value counts.

    Persisted as `RandomizationSchedule.minimisation_state_json`. The
    Pocock-Simon update returns a fresh state on every allocation; the
    repository overwrites the row each time.
    """

    counts: dict[str, dict[str, dict[str, int]]]  # arm → factor → value → count


# ── Helpers ─────────────────────────────────────────────────────────────


def _validated_arms(arms: Sequence[str]) -> list[str]:
    if not arms:
        raise ValueError("At least one arm is required.")
    if len(set(arms)) != len(arms):
        raise ValueError(f"Arm labels must be unique; got {list(arms)!r}.")
    return list(arms)


def _validated_ratio(arms: Sequence[str], ratio: Sequence[int] | None) -> list[int]:
    if ratio is None:
        return [1] * len(arms)
    if len(ratio) != len(arms):
        raise ValueError(f"Ratio length {len(ratio)} != arm count {len(arms)}: {list(ratio)!r}.")
    if any(r <= 0 for r in ratio):
        raise ValueError(f"All ratio entries must be positive; got {list(ratio)!r}.")
    return list(ratio)


def _expand_ratio_to_arms(arms: Sequence[str], ratio: Sequence[int]) -> list[str]:
    """For ratio [2,1] with arms ['A','B'], emit ['A','A','B']."""
    out: list[str] = []
    for arm, r in zip(arms, ratio, strict=True):
        out.extend([arm] * r)
    return out


def canonical_stratum_label(factor_values: Mapping[str, str]) -> str:
    """Stable, regulator-readable label assembled from factor pairs.

    Order is alphabetical by factor name so the same stratum hashes
    to the same label regardless of input ordering.
    """
    parts = [f"{k}={v}" for k, v in sorted(factor_values.items())]
    return "|".join(parts)


# ── Simple randomisation ────────────────────────────────────────────────


def generate_simple(
    n: int,
    arms: Sequence[str],
    *,
    ratio: Sequence[int] | None = None,
    seed: int,
) -> list[str]:
    """N independent allocations from `arms` with the given `ratio`.

    Useful for very small trials where block-imbalance risk is small
    relative to the cost of stratification. Most prospective trials
    should prefer `generate_permuted_block`.
    """
    arms_l = _validated_arms(arms)
    ratio_l = _validated_ratio(arms_l, ratio)
    if n < 0:
        raise ValueError(f"n must be >= 0; got {n}.")
    rng = random.Random(seed)  # noqa: S311 — seeded reproducible treatment allocation, not cryptographic
    weighted_arms = _expand_ratio_to_arms(arms_l, ratio_l)
    return [rng.choice(weighted_arms) for _ in range(n)]


# ── Permuted-block randomisation ────────────────────────────────────────


def _generate_block(
    arms: Sequence[str], ratio: Sequence[int], block_size: int, rng: random.Random
) -> list[str]:
    """One balanced block of size `block_size`. Block size must be a
    positive multiple of sum(ratio)."""
    ratio_sum = sum(ratio)
    if block_size % ratio_sum != 0:
        raise ValueError(
            f"Block size {block_size} not divisible by ratio sum {ratio_sum} "
            f"(arms={list(arms)!r}, ratio={list(ratio)!r})."
        )
    repeats = block_size // ratio_sum
    block: list[str] = []
    for arm, r in zip(arms, ratio, strict=True):
        block.extend([arm] * (r * repeats))
    rng.shuffle(block)
    return block


def generate_permuted_block(
    n: int,
    arms: Sequence[str],
    *,
    ratio: Sequence[int] | None = None,
    block_sizes: Sequence[int] | None = None,
    seed: int,
) -> list[str]:
    """N allocations across blocks drawn uniformly at random from
    `block_sizes`. Each block is balanced; the running list may end
    mid-block which is the standard concession.

    Default block sizes: [2 × ratio_sum, 4 × ratio_sum, 6 × ratio_sum].
    """
    arms_l = _validated_arms(arms)
    ratio_l = _validated_ratio(arms_l, ratio)
    ratio_sum = sum(ratio_l)
    block_sizes_l: list[int] = (
        list(block_sizes)
        if block_sizes
        else [
            2 * ratio_sum,
            4 * ratio_sum,
            6 * ratio_sum,
        ]
    )
    if not block_sizes_l:
        raise ValueError("At least one block size is required.")
    for bs in block_sizes_l:
        if bs % ratio_sum != 0:
            raise ValueError(f"Block size {bs} not divisible by ratio sum {ratio_sum}.")
    if n < 0:
        raise ValueError(f"n must be >= 0; got {n}.")

    rng = random.Random(seed)  # noqa: S311 — seeded reproducible treatment allocation, not cryptographic
    out: list[str] = []
    while len(out) < n:
        bs = rng.choice(block_sizes_l)
        out.extend(_generate_block(arms_l, ratio_l, bs, rng))
    return out[:n]


# ── Stratified permuted-block randomisation ─────────────────────────────


def generate_stratified_permuted_block(
    expected_per_stratum: Mapping[str, int],
    arms: Sequence[str],
    *,
    ratio: Sequence[int] | None = None,
    block_sizes: Sequence[int] | None = None,
    seed: int,
) -> dict[str, list[str]]:
    """One permuted-block sequence per stratum.

    Each stratum gets its own RNG seeded from `seed XOR hash(stratum_label)`
    so the sequence is deterministic AND independent across strata.

    Customer passes `expected_per_stratum`: a {stratum_label: expected_N}
    map. The repository at allocation time picks the next entry from the
    matching stratum.
    """
    arms_l = _validated_arms(arms)
    ratio_l = _validated_ratio(arms_l, ratio)
    out: dict[str, list[str]] = {}
    for stratum, n in expected_per_stratum.items():
        # XOR with a stable hash so each stratum draws a deterministic but
        # independent sub-sequence.
        sub_seed = (seed ^ (hash(stratum) & 0xFFFF_FFFF)) & 0xFFFF_FFFF
        out[stratum] = generate_permuted_block(
            n, arms_l, ratio=ratio_l, block_sizes=block_sizes, seed=sub_seed
        )
    return out


# ── Pocock-Simon minimisation ───────────────────────────────────────────


def _empty_state(arms: Sequence[str], factors: Sequence[str]) -> MinimisationState:
    return MinimisationState(counts={a: {f: {} for f in factors} for a in arms})


def _imbalance_for_arm(
    state: MinimisationState,
    arm: str,
    factor_values: Mapping[str, str],
    weights: Mapping[str, float],
) -> float:
    """Sum of weighted absolute counts for the candidate arm.

    Pocock-Simon classic: for each factor f with the subject's value v,
    add weight_f × (count[arm][f][v] + 1) and pick the arm with the
    smallest resulting weighted-sum. We compute the *post-allocation*
    count (i.e. +1) so the comparison is "what would the imbalance be
    if this arm were chosen?".
    """
    total = 0.0
    for f, v in factor_values.items():
        w = weights.get(f, 1.0)
        current = state["counts"][arm][f].get(v, 0)
        total += w * (current + 1)
    return total


def pocock_simon_choose_arm(
    state: MinimisationState | None,
    factor_values: Mapping[str, str],
    arms: Sequence[str],
    *,
    weights: Mapping[str, float] | None = None,
    seed: int,
    probabilistic_p: float = 1.0,
) -> tuple[str, MinimisationState]:
    """Run one Pocock-Simon allocation step.

    Returns ``(chosen_arm, new_state)``. The caller persists
    ``new_state`` back to the schedule.

    `probabilistic_p` ∈ (0,1] is the probability of picking the
    minimising arm; default 1.0 = deterministic minimisation. Some
    real trials set p<1 to add randomness — the spec covers this for
    completeness even though most platform-MVP deploys want
    deterministic.
    """
    arms_l = _validated_arms(arms)
    factors = list(factor_values.keys())
    w = dict(weights) if weights else {f: 1.0 for f in factors}
    if not 0.0 < probabilistic_p <= 1.0:
        raise ValueError(f"probabilistic_p must be in (0, 1]; got {probabilistic_p}.")

    if state is None:
        state = _empty_state(arms_l, factors)
    else:
        # Ensure all arms+factors exist (deploys may have added new
        # factors after schedule creation — defensive).
        for a in arms_l:
            state["counts"].setdefault(a, {})
            for f in factors:
                state["counts"][a].setdefault(f, {})

    # Rank arms by the post-allocation weighted-sum.
    scores: list[tuple[str, float]] = [
        (a, _imbalance_for_arm(state, a, factor_values, w)) for a in arms_l
    ]
    min_score = min(s for _, s in scores)
    minimisers = [a for a, s in scores if s == min_score]

    rng = random.Random(seed)  # noqa: S311 — seeded reproducible treatment allocation, not cryptographic
    if probabilistic_p < 1.0 and rng.random() > probabilistic_p:
        # Fall back to a uniform random arm.
        chosen = rng.choice(arms_l)
    else:
        chosen = rng.choice(minimisers) if len(minimisers) > 1 else minimisers[0]

    # Update the new state with the allocation.
    new_counts: dict[str, dict[str, dict[str, int]]] = {
        a: {f: dict(v) for f, v in state["counts"][a].items()} for a in state["counts"]
    }
    for f, v in factor_values.items():
        new_counts[chosen].setdefault(f, {})
        new_counts[chosen][f][v] = new_counts[chosen][f].get(v, 0) + 1
    return chosen, MinimisationState(counts=new_counts)
