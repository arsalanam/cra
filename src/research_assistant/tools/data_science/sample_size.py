"""sample_size tool — closed-form N calculator for prospective trials.

Four outcome families, all parameterised by power, alpha, allocation ratio,
and an optional drop-out inflation:

  • two_proportions  — Z-test on two independent proportions
  • two_means        — two-sample t-test on means with a common SD
  • time_to_event    — Schoenfeld events formula + S(t) → N inflation
  • paired           — within-subject mean difference (paired t-test)

Implemented directly on top of `scipy.stats.norm` / `scipy.stats.t` rather
than statsmodels so the tool runs in the agent process (the sandbox image
has statsmodels; the host venv only carries scipy). All formulas are
textbook; references live in the returned result so the SAP can quote
them verbatim.

The tool returns a structured dict shaped to match
`domain.sap.SampleSizeResult` so the SAP specialist's STEP 2 can ingest
it without translation. Used both standalone by any specialist that
registers `DATA_SCIENCE_TOOLS` and as the workhorse inside the SAP
workflow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Literal

from pydantic_ai import Agent, RunContext
from scipy.stats import norm, t  # type: ignore[import-untyped]

from ...agent.deps import AgentDeps
from .._emit import emit_run

logger = logging.getLogger(__name__)


OutcomeType = Literal["two_proportions", "two_means", "time_to_event", "paired"]


def _inflate_for_dropout(n: float, dropout_rate: float) -> int:
    """Inflate a per-arm N by the anticipated attrition rate."""
    if dropout_rate <= 0:
        return math.ceil(n)
    return math.ceil(n / (1.0 - dropout_rate))


def _z(alpha: float, power: float, one_sided: bool) -> tuple[float, float]:
    """Return (z_alpha, z_beta) for the requested test."""
    alpha_for_quantile = alpha if one_sided else alpha / 2.0
    z_a = float(norm.ppf(1.0 - alpha_for_quantile))
    z_b = float(norm.ppf(power))
    return z_a, z_b


# ── Two proportions ──────────────────────────────────────────────────────


def sample_size_two_proportions(
    *,
    p_control: float,
    p_intervention: float,
    alpha: float = 0.05,
    power: float = 0.80,
    allocation_ratio: float = 1.0,
    dropout_rate: float = 0.0,
    one_sided: bool = False,
) -> dict[str, Any]:
    """Closed-form Z-test sample size for two independent proportions.

    Per Fleiss (Statistical Methods for Rates and Proportions, 3e, §4.3),
    using the variance under the alternative. Without continuity
    correction — adequate for N≥30 per arm, which any trial-grade design
    will clear.
    """
    if not (0 < p_control < 1) or not (0 < p_intervention < 1):
        raise ValueError("p_control and p_intervention must be in (0, 1).")
    if p_control == p_intervention:
        raise ValueError("p_control and p_intervention cannot be equal — no detectable effect.")
    if allocation_ratio <= 0:
        raise ValueError("allocation_ratio must be positive.")

    z_a, z_b = _z(alpha, power, one_sided)
    k = allocation_ratio  # intervention-to-control
    p1 = p_control
    p2 = p_intervention
    var = p1 * (1 - p1) / 1.0 + p2 * (1 - p2) / k
    n_control = (z_a + z_b) ** 2 * var / (p1 - p2) ** 2
    n_intervention = n_control * k

    out: dict[str, Any] = {
        "outcome_type": "two_proportions",
        "n_per_arm_control": _inflate_for_dropout(n_control, dropout_rate),
        "n_per_arm_intervention": _inflate_for_dropout(n_intervention, dropout_rate),
        "events_required": None,
        "formula_name": "Two-proportions Z-test (uncorrected, variance under H1)",
        "formula_reference": (
            "Fleiss JL, Levin B, Paik MC. Statistical Methods for Rates and "
            "Proportions, 3rd ed. Wiley, 2003. §4.3."
        ),
        "inputs": {
            "p_control": p_control,
            "p_intervention": p_intervention,
            "alpha": alpha,
            "power": power,
            "allocation_ratio": allocation_ratio,
            "dropout_rate": dropout_rate,
            "one_sided": one_sided,
        },
    }
    out["n_total"] = out["n_per_arm_control"] + out["n_per_arm_intervention"]
    return out


# ── Two means (continuous) ───────────────────────────────────────────────


def sample_size_two_means(
    *,
    mean_control: float,
    mean_intervention: float,
    standard_deviation: float,
    alpha: float = 0.05,
    power: float = 0.80,
    allocation_ratio: float = 1.0,
    dropout_rate: float = 0.0,
    one_sided: bool = False,
) -> dict[str, Any]:
    """Two-sample t-test sample size with a common SD assumption.

    Solves the closed-form Z approximation then iterates once with the
    exact t-distribution to tighten the rounding for small N (typical
    statsmodels behaviour). Cohen's d standardised effect is reported in
    the inputs for transparency.
    """
    if standard_deviation <= 0:
        raise ValueError("standard_deviation must be positive.")
    if mean_control == mean_intervention:
        raise ValueError(
            "mean_control and mean_intervention cannot be equal — no detectable effect."
        )
    if allocation_ratio <= 0:
        raise ValueError("allocation_ratio must be positive.")

    d = (mean_intervention - mean_control) / standard_deviation
    z_a, z_b = _z(alpha, power, one_sided)
    k = allocation_ratio
    # Closed-form Z approximation (Cohen 1988, eqn 2.4.1).
    n_control_z = (z_a + z_b) ** 2 * (1.0 + 1.0 / k) / d**2
    # One Newton-style refinement using the exact t critical values.
    n_control = n_control_z
    for _ in range(8):  # converges in ~3 iterations; cap is just safety
        df = n_control * (1.0 + k) - 2
        if df <= 1:
            break
        t_a_q = alpha if one_sided else alpha / 2.0
        t_a = float(t.ppf(1.0 - t_a_q, df))
        t_b = float(t.ppf(power, df))
        n_new = (t_a + t_b) ** 2 * (1.0 + 1.0 / k) / d**2
        if abs(n_new - n_control) < 0.1:
            n_control = n_new
            break
        n_control = n_new
    n_intervention = n_control * k

    out: dict[str, Any] = {
        "outcome_type": "two_means",
        "n_per_arm_control": _inflate_for_dropout(n_control, dropout_rate),
        "n_per_arm_intervention": _inflate_for_dropout(n_intervention, dropout_rate),
        "events_required": None,
        "formula_name": "Two-sample t-test (Cohen, common SD)",
        "formula_reference": (
            "Cohen J. Statistical Power Analysis for the Behavioral Sciences, "
            "2nd ed. Erlbaum, 1988. §2.4."
        ),
        "inputs": {
            "mean_control": mean_control,
            "mean_intervention": mean_intervention,
            "standard_deviation": standard_deviation,
            "cohens_d": d,
            "alpha": alpha,
            "power": power,
            "allocation_ratio": allocation_ratio,
            "dropout_rate": dropout_rate,
            "one_sided": one_sided,
        },
    }
    out["n_total"] = out["n_per_arm_control"] + out["n_per_arm_intervention"]
    return out


# ── Time-to-event (Schoenfeld) ───────────────────────────────────────────


def sample_size_time_to_event(
    *,
    hazard_ratio: float,
    control_survival_at_horizon: float,
    alpha: float = 0.05,
    power: float = 0.80,
    allocation_ratio: float = 1.0,
    dropout_rate: float = 0.0,
    one_sided: bool = False,
) -> dict[str, Any]:
    """Schoenfeld events + Freedman N for time-to-event endpoints.

    Two-step: (1) total events required to detect the HR with the given
    power, (2) N inflated by 1/(1 - average control-arm event probability)
    so the trial accrues enough events.

    `control_survival_at_horizon` is S(t*) on the control arm — typically
    1 minus the expected event rate by the planned analysis time. The
    intervention-arm event probability is approximated as
    1 - S_c(t*)^HR (Collett 2015, §10.5) which is exact under PH.
    """
    if hazard_ratio <= 0 or hazard_ratio == 1.0:
        raise ValueError("hazard_ratio must be positive and ≠ 1.")
    if not (0 < control_survival_at_horizon < 1):
        raise ValueError("control_survival_at_horizon must be in (0, 1).")

    z_a, z_b = _z(alpha, power, one_sided)
    k = allocation_ratio
    p_intervention_weight = k / (1.0 + k)
    p_control_weight = 1.0 / (1.0 + k)
    # Schoenfeld 1981 events formula
    events = (z_a + z_b) ** 2 / (
        p_intervention_weight * p_control_weight * math.log(hazard_ratio) ** 2
    )

    # Probability a participant experiences the event by the horizon, by arm.
    p_event_control = 1.0 - control_survival_at_horizon
    p_event_intervention = 1.0 - control_survival_at_horizon**hazard_ratio
    average_event_prob = (
        p_control_weight * p_event_control + p_intervention_weight * p_event_intervention
    )
    n_total_raw = events / average_event_prob

    n_control_raw = n_total_raw * p_control_weight
    n_intervention_raw = n_total_raw * p_intervention_weight

    out: dict[str, Any] = {
        "outcome_type": "time_to_event",
        "n_per_arm_control": _inflate_for_dropout(n_control_raw, dropout_rate),
        "n_per_arm_intervention": _inflate_for_dropout(n_intervention_raw, dropout_rate),
        "events_required": math.ceil(events),
        "formula_name": "Schoenfeld events + Freedman N (proportional hazards)",
        "formula_reference": (
            "Schoenfeld DA. Sample-size formula for the proportional-hazards "
            "regression model. Biometrics 1983;39:499-503. Collett D, Modelling "
            "Survival Data in Medical Research, 3rd ed. CRC, 2015, §10.5."
        ),
        "inputs": {
            "hazard_ratio": hazard_ratio,
            "control_survival_at_horizon": control_survival_at_horizon,
            "alpha": alpha,
            "power": power,
            "allocation_ratio": allocation_ratio,
            "dropout_rate": dropout_rate,
            "one_sided": one_sided,
            "average_event_prob": average_event_prob,
        },
    }
    out["n_total"] = out["n_per_arm_control"] + out["n_per_arm_intervention"]
    return out


# ── Paired (within-subject) ──────────────────────────────────────────────


def sample_size_paired(
    *,
    mean_difference: float,
    sd_of_difference: float,
    alpha: float = 0.05,
    power: float = 0.80,
    dropout_rate: float = 0.0,
    one_sided: bool = False,
) -> dict[str, Any]:
    """Paired t-test sample size (the standard within-subject design).

    Same closed-form Z approximation + t refinement as `two_means`, but
    on a single N (no allocation ratio — every subject contributes to
    both arms).
    """
    if sd_of_difference <= 0:
        raise ValueError("sd_of_difference must be positive.")
    if mean_difference == 0:
        raise ValueError("mean_difference cannot be 0 — no detectable effect.")

    d = mean_difference / sd_of_difference
    z_a, z_b = _z(alpha, power, one_sided)
    n_raw = (z_a + z_b) ** 2 / d**2
    # Refine with the exact t critical (paired uses df = n - 1).
    n = n_raw
    for _ in range(8):
        df = n - 1
        if df <= 1:
            break
        t_a_q = alpha if one_sided else alpha / 2.0
        t_a = float(t.ppf(1.0 - t_a_q, df))
        t_b = float(t.ppf(power, df))
        n_new = (t_a + t_b) ** 2 / d**2
        if abs(n_new - n) < 0.1:
            n = n_new
            break
        n = n_new

    n_inflated = _inflate_for_dropout(n, dropout_rate)
    out: dict[str, Any] = {
        "outcome_type": "paired",
        "n_per_arm_control": n_inflated,  # both "arms" are the same subjects
        "n_per_arm_intervention": n_inflated,
        "n_total": n_inflated,
        "events_required": None,
        "formula_name": "Paired t-test (within-subject mean difference)",
        "formula_reference": (
            "Cohen J. Statistical Power Analysis for the Behavioral Sciences, "
            "2nd ed. Erlbaum, 1988. §2.5."
        ),
        "inputs": {
            "mean_difference": mean_difference,
            "sd_of_difference": sd_of_difference,
            "cohens_d_z": d,
            "alpha": alpha,
            "power": power,
            "dropout_rate": dropout_rate,
            "one_sided": one_sided,
        },
    }
    return out


# ── Agent tool registration ──────────────────────────────────────────────


def _dispatch(outcome_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch on outcome_type to the right closed-form helper."""
    if outcome_type == "two_proportions":
        return sample_size_two_proportions(
            p_control=float(params["p_control"]),
            p_intervention=float(params["p_intervention"]),
            alpha=float(params.get("alpha", 0.05)),
            power=float(params.get("power", 0.80)),
            allocation_ratio=float(params.get("allocation_ratio", 1.0)),
            dropout_rate=float(params.get("dropout_rate", 0.0)),
            one_sided=bool(params.get("one_sided", False)),
        )
    if outcome_type == "two_means":
        return sample_size_two_means(
            mean_control=float(params["mean_control"]),
            mean_intervention=float(params["mean_intervention"]),
            standard_deviation=float(params["standard_deviation"]),
            alpha=float(params.get("alpha", 0.05)),
            power=float(params.get("power", 0.80)),
            allocation_ratio=float(params.get("allocation_ratio", 1.0)),
            dropout_rate=float(params.get("dropout_rate", 0.0)),
            one_sided=bool(params.get("one_sided", False)),
        )
    if outcome_type == "time_to_event":
        return sample_size_time_to_event(
            hazard_ratio=float(params["hazard_ratio"]),
            control_survival_at_horizon=float(params["control_survival_at_horizon"]),
            alpha=float(params.get("alpha", 0.05)),
            power=float(params.get("power", 0.80)),
            allocation_ratio=float(params.get("allocation_ratio", 1.0)),
            dropout_rate=float(params.get("dropout_rate", 0.0)),
            one_sided=bool(params.get("one_sided", False)),
        )
    if outcome_type == "paired":
        return sample_size_paired(
            mean_difference=float(params["mean_difference"]),
            sd_of_difference=float(params["sd_of_difference"]),
            alpha=float(params.get("alpha", 0.05)),
            power=float(params.get("power", 0.80)),
            dropout_rate=float(params.get("dropout_rate", 0.0)),
            one_sided=bool(params.get("one_sided", False)),
        )
    return {
        "error": (
            f"Unknown outcome_type {outcome_type!r}. Use one of: "
            f"two_proportions | two_means | time_to_event | paired."
        )
    }


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def sample_size(
        ctx: RunContext[AgentDeps],
        outcome_type: OutcomeType,
        params: dict[str, Any],
    ) -> str:
        """
        Compute the required sample size for a prospective trial.

        outcome_type:
          • "two_proportions" — needs `p_control`, `p_intervention`.
          • "two_means"       — needs `mean_control`, `mean_intervention`,
                                `standard_deviation`.
          • "time_to_event"   — needs `hazard_ratio`,
                                `control_survival_at_horizon`.
          • "paired"          — needs `mean_difference`, `sd_of_difference`.

        Common optional params (defaults in parens):
          alpha (0.05), power (0.80), allocation_ratio (1.0),
          dropout_rate (0.0), one_sided (False). `allocation_ratio` does
          not apply to paired.

        Returns JSON shaped like `domain.sap.SampleSizeResult` so the SAP
        specialist's STEP 2 can ingest it without translation.
        """

        async def _impl() -> str:
            try:
                # scipy.stats.* calls are CPU-bound but cheap (microseconds);
                # run on a worker thread to keep the event loop unblocked.
                result = await asyncio.to_thread(_dispatch, outcome_type, params)
            except KeyError as e:
                return json.dumps({"error": f"Missing required parameter: {e}"})
            except ValueError as e:
                return json.dumps({"error": str(e)})
            return json.dumps(result, ensure_ascii=False)

        return await emit_run(
            ctx,
            tool="sample_size",
            icon="🧮",
            args={"outcome_type": outcome_type},
            description=f"Computing sample size for {outcome_type}",
            impl=_impl,
        )


__all__ = [
    "OutcomeType",
    "register",
    "sample_size_paired",
    "sample_size_time_to_event",
    "sample_size_two_means",
    "sample_size_two_proportions",
]
