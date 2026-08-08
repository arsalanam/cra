"""group_sequential tool — interim-monitoring boundaries for adaptive trials.

Computes the per-look rejection boundaries for a group-sequential design
using the Lan-DeMets alpha-spending approach (O'Brien-Fleming and Pocock
spending functions), plus the sample-size inflation factor over the
equivalent fixed design.

Method. The sequential test statistics form a Brownian-motion process: with
information fractions t_1 < ... < t_K = 1, the "B-value" B(t) = sqrt(t)*Z(t)
has independent Gaussian increments (variance = the fraction increment).
The spending function alpha*(t) fixes how much type-I error is spent by each
look; the incremental spend at look k is alpha*(t_k) - alpha*(t_{k-1}), and
the boundary b_k solves

    P(cross at look k | not yet crossed) = incremental spend,

evaluated by propagating the continuation sub-density forward one increment
at a time (the standard Armitage-McPherson-Rowe / Lan-DeMets recursion). The
same recursion under a drift gives the design's power, from which the
sample-size inflation factor (vs a fixed design) is solved.

Implemented on numpy + scipy.stats.norm so it runs in the agent process (no
sandbox). References: Lan KKG, DeMets DL. Discrete sequential boundaries for
clinical trials. Biometrika 1983;70:659-63. Jennison C, Turnbull BW. Group
Sequential Methods with Applications to Clinical Trials. Chapman & Hall,
2000, ch. 7.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Literal

import numpy as np
from pydantic_ai import Agent, RunContext
from scipy.stats import norm  # type: ignore[import-untyped]

from ...agent.deps import AgentDeps
from .._emit import emit_run

logger = logging.getLogger(__name__)

SpendingFunction = Literal["obrien_fleming", "pocock"]

# Grid resolution for the sub-density propagation. 700 points over the
# B-value range keeps the 2-4 look boundaries within ~0.01 of the published
# Lan-DeMets tables while keeping the O(N^2) convolution fast.
_GRID_POINTS = 700
_SQRT_2PI = float(np.sqrt(2.0 * np.pi))


def _gauss_pdf(z: np.ndarray) -> np.ndarray:
    """Standard-normal pdf on an array — a direct numpy formula, ~10x faster
    than scipy.stats.norm.pdf for the large convolution kernels here."""
    return np.asarray(np.exp(-0.5 * z * z) / _SQRT_2PI)


def _cumulative_spend(kind: str, t: float, alpha: float) -> float:
    """Cumulative one-sided alpha spent by information fraction t in (0, 1]."""
    t = min(max(t, 1e-12), 1.0)
    if kind == "obrien_fleming":
        # 2*(1 - Phi(z_{1-alpha/2} / sqrt(t))): 0 at t->0, alpha at t=1.
        return float(2.0 * (1.0 - norm.cdf(norm.ppf(1.0 - alpha / 2.0) / np.sqrt(t))))
    if kind == "pocock":
        return float(alpha * np.log(1.0 + (np.e - 1.0) * t))
    raise ValueError(f"Unknown spending function {kind!r}; use obrien_fleming | pocock.")


def _grid(hi: float) -> tuple[np.ndarray, float]:
    """Symmetric-ish B-value grid from -6 to `hi` (drift lives on the right)."""
    x = np.linspace(-6.0, hi, _GRID_POINTS)
    return x, float(x[1] - x[0])


def _exit_prob(
    g_prev: np.ndarray | None, x: np.ndarray, dx: float, b: float, v: float, m: float
) -> float:
    """P(cross above b at this look | continuation density g_prev before the
    increment), for an increment ~ N(m, v). g_prev=None means the process
    starts from B=0 (first look)."""
    if g_prev is None:
        return float(1.0 - norm.cdf((b - m) / np.sqrt(v)))
    tail = 1.0 - norm.cdf((b - x - m) / np.sqrt(v))
    return float(np.trapezoid(g_prev * tail, dx=dx))


def _solve_boundary(
    g_prev: np.ndarray | None, x: np.ndarray, dx: float, target: float, v: float, m: float
) -> float:
    """Bisect for the B-value boundary b with exit prob == target (exit is
    monotone decreasing in b)."""
    lo, hi = -2.0, 15.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if _exit_prob(g_prev, x, dx, mid, v, m) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-7:
            break
    return 0.5 * (lo + hi)


def _propagate(
    g_prev: np.ndarray | None, x: np.ndarray, dx: float, v: float, m: float, b: float
) -> np.ndarray:
    """Advance the continuation sub-density one increment (~ N(m, v)) and
    truncate to the continuation region B < b."""
    sd = np.sqrt(v)
    if g_prev is None:
        g_new = _gauss_pdf((x - m) / sd) / sd
    else:
        # g_new(x) = integral g_prev(x') * N(x - x' - m; v) dx'  (a convolution)
        diff = (x[:, None] - x[None, :] - m) / sd
        kernel = _gauss_pdf(diff) / sd
        g_new = (kernel @ g_prev) * dx
    return np.where(x < b, g_new, 0.0)


def _crossing_power(fractions: list[float], b_values: list[float], drift: float) -> float:
    """Total upper-crossing probability across all looks under a B-value
    drift (mean of B(1) == drift)."""
    x, dx = _grid(hi=drift + 6.0)
    g: np.ndarray | None = None
    prev_t = 0.0
    total = 0.0
    for k, t in enumerate(fractions):
        v = t - prev_t
        m = drift * v
        total += _exit_prob(g, x, dx, b_values[k], v, m)
        g = _propagate(g, x, dx, v, m, b_values[k])
        prev_t = t
    return total


def group_sequential_boundaries(
    *,
    n_looks: int,
    alpha: float = 0.025,
    spending: str = "obrien_fleming",
    information_fractions: list[float] | None = None,
    power: float = 0.90,
) -> dict[str, Any]:
    """Group-sequential rejection boundaries + sample-size inflation.

    `alpha` is the ONE-SIDED type-I error (0.025 is the regulatory default
    for a one-sided GSD). `information_fractions` defaults to equally spaced
    looks (k/K); if given it must be strictly increasing within (0, 1] and
    end at 1.0. `power` is used only for the inflation factor.

    Returns {spending, alpha, power, looks: [{look, information_fraction,
    cumulative_alpha_spent, incremental_alpha, z_boundary, nominal_p}],
    max_sample_size_inflation, drift}. `z_boundary` is the standardized
    critical value at that look; `nominal_p` its one-sided p-value.
    """
    if n_looks < 1:
        raise ValueError("n_looks must be >= 1.")
    if not (0.0 < alpha < 0.5):
        raise ValueError("alpha must be in (0, 0.5).")
    if not (0.0 < power < 1.0):
        raise ValueError("power must be in (0, 1).")

    if information_fractions is None:
        fractions = [(k + 1) / n_looks for k in range(n_looks)]
    else:
        fractions = [float(t) for t in information_fractions]
        if len(fractions) != n_looks:
            raise ValueError("information_fractions must have length n_looks.")
        if any(b <= a for a, b in itertools.pairwise(fractions)):
            raise ValueError("information_fractions must be strictly increasing.")
        if not (fractions[0] > 0.0 and abs(fractions[-1] - 1.0) < 1e-9):
            raise ValueError("information_fractions must lie in (0, 1] and end at 1.0.")

    cumulative = [_cumulative_spend(spending, t, alpha) for t in fractions]
    incremental = [cumulative[0]] + [cumulative[k] - cumulative[k - 1] for k in range(1, n_looks)]

    # Boundary recursion under H0 (drift 0).
    x, dx = _grid(hi=6.0)
    b_values: list[float] = []
    g: np.ndarray | None = None
    prev_t = 0.0
    for k, t in enumerate(fractions):
        v = t - prev_t
        b_k = _solve_boundary(g, x, dx, incremental[k], v, 0.0)
        b_values.append(b_k)
        g = _propagate(g, x, dx, v, 0.0, b_k)
        prev_t = t

    looks = []
    for k, t in enumerate(fractions):
        z_k = b_values[k] / np.sqrt(t)
        looks.append(
            {
                "look": k + 1,
                "information_fraction": round(t, 6),
                "cumulative_alpha_spent": round(cumulative[k], 6),
                "incremental_alpha": round(incremental[k], 6),
                "z_boundary": round(float(z_k), 4),
                "nominal_p": round(float(1.0 - norm.cdf(z_k)), 6),
            }
        )

    # Sample-size inflation: find the drift giving the target power under the
    # sequential boundaries, compare to the fixed-design drift.
    lo, hi = 0.0, 12.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if _crossing_power(fractions, b_values, mid) < power:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-5:
            break
    drift = 0.5 * (lo + hi)
    fixed_drift = float(norm.ppf(1.0 - alpha) + norm.ppf(power))
    # A group-sequential design never needs FEWER subjects than the fixed
    # design, so the inflation factor is >= 1 by construction; floor the
    # grid-approximated estimate at 1.0 to avoid a spurious sub-1 readout.
    inflation = max(1.0, (drift / fixed_drift) ** 2)

    return {
        "spending": spending,
        "alpha": alpha,
        "power": power,
        "looks": looks,
        "max_sample_size_inflation": round(inflation, 4),
        "drift": round(drift, 4),
    }


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def group_sequential(
        ctx: RunContext[AgentDeps],
        n_looks: int,
        alpha: float = 0.025,
        spending: SpendingFunction = "obrien_fleming",
        information_fractions: list[float] | None = None,
        power: float = 0.90,
    ) -> str:
        """
        Group-sequential interim-monitoring boundaries for an adaptive trial.

        n_looks: number of analyses (including the final one).
        alpha:   one-sided type-I error (0.025 default).
        spending: "obrien_fleming" (conservative early, near-fixed final) or
                  "pocock" (constant boundary, spends earlier).
        information_fractions: optional custom look times in (0, 1] ending at
                  1.0; defaults to equally spaced (k/K).
        power:   target power, used for the sample-size inflation factor.

        Returns JSON with per-look z-boundaries + nominal p-values, the
        cumulative alpha spent, and the max sample-size inflation over the
        equivalent fixed design.
        """

        async def _impl() -> str:
            try:
                result = await asyncio.to_thread(
                    lambda: group_sequential_boundaries(
                        n_looks=n_looks,
                        alpha=alpha,
                        spending=spending,
                        information_fractions=information_fractions,
                        power=power,
                    )
                )
            except ValueError as e:
                return json.dumps({"error": str(e)})
            return json.dumps(result, ensure_ascii=False)

        return await emit_run(
            ctx,
            tool="group_sequential",
            icon="📉",
            args={"n_looks": n_looks, "spending": spending},
            description=f"Computing {spending} boundaries for {n_looks} looks",
            impl=_impl,
        )


__all__ = ["SpendingFunction", "group_sequential_boundaries", "register"]
