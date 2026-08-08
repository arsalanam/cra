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
import itertools
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

# Superiority is the default; non-inferiority and equivalence are the two
# extra hypotheses supported for `two_proportions` and `two_means`. Both
# are inherently one-sided at alpha and driven by a `margin` rather than a
# point effect (see `_hypothesis_z_and_effect`).
Hypothesis = Literal["superiority", "non_inferiority", "equivalence"]


# Shared citation appended to the base formula reference for the
# non-inferiority / equivalence variants.
_NI_EQUIV_REFERENCE = (
    " Non-inferiority / equivalence margin per Chow S-C, Shao J, Wang H, "
    "Lokhnygina Y. Sample Size Calculations in Clinical Research, 3rd ed. "
    "CRC Press, 2018, §§4.2-5.3."
)


def _formula_label(base: str, hypothesis: str) -> str:
    """Suffix the base formula name with the hypothesis when it isn't the
    default superiority test."""
    if hypothesis == "non_inferiority":
        return f"{base} — non-inferiority (one-sided, margin)"
    if hypothesis == "equivalence":
        return f"{base} — equivalence (TOST, margin)"
    return base


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


def _hypothesis_z_and_effect(
    *,
    hypothesis: str,
    alpha: float,
    power: float,
    raw_effect: float,
    margin: float | None,
    one_sided: bool,
) -> tuple[float, float, float]:
    """Resolve (z_alpha, z_beta, denominator_effect) for a hypothesis.

    - **superiority** — the classic test: the denominator effect is the
      raw difference (intervention − control) and z's come from `_z`
      (two-sided unless `one_sided`).
    - **non_inferiority** — one-sided at `alpha`; the denominator effect is
      `margin − |raw_effect|`, where `raw_effect` is the *assumed true*
      difference (often 0). z_beta = Φ⁻¹(power).
    - **equivalence** — two one-sided tests (TOST); one-sided at `alpha`
      but z_beta = Φ⁻¹(1 − (1−power)/2) (the β/2 split), denominator
      effect `margin − |raw_effect|`.

    Non-inferiority / equivalence margins follow Chow, Shao, Wang &
    Lokhnygina, *Sample Size Calculations in Clinical Research*, 3rd ed.
    (CRC, 2018), §§4.2–5.3.
    """
    if hypothesis == "superiority":
        z_a, z_b = _z(alpha, power, one_sided)
        return z_a, z_b, raw_effect
    if margin is None or margin <= 0:
        raise ValueError(f"{hypothesis!r} requires a positive `margin`.")
    effect = margin - abs(raw_effect)
    if effect <= 0:
        raise ValueError(
            "Infeasible design: the assumed true difference must be strictly "
            "inside the margin (|effect| < margin) for a non-inferiority / "
            "equivalence test."
        )
    # Both NI and equivalence test one-sided at alpha.
    z_a = float(norm.ppf(1.0 - alpha))
    if hypothesis == "non_inferiority":
        z_b = float(norm.ppf(power))
    elif hypothesis == "equivalence":
        z_b = float(norm.ppf(1.0 - (1.0 - power) / 2.0))
    else:
        raise ValueError(
            f"Unknown hypothesis {hypothesis!r}. Use superiority | non_inferiority | equivalence."
        )
    return z_a, z_b, effect


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
    hypothesis: str = "superiority",
    margin: float | None = None,
) -> dict[str, Any]:
    """Closed-form Z-test sample size for two independent proportions.

    Per Fleiss (Statistical Methods for Rates and Proportions, 3e, §4.3),
    using the variance under the alternative. Without continuity
    correction — adequate for N≥30 per arm, which any trial-grade design
    will clear.

    `hypothesis` selects the test:
      • "superiority" (default) — detect the p_intervention − p_control gap.
      • "non_inferiority" / "equivalence" — supply `margin` (>0). Here
        p_control / p_intervention are the *assumed true* rates (equal is
        the usual assumption); the design powers against the margin, not
        the gap. See `_hypothesis_z_and_effect`.
    """
    if not (0 < p_control < 1) or not (0 < p_intervention < 1):
        raise ValueError("p_control and p_intervention must be in (0, 1).")
    if hypothesis == "superiority" and p_control == p_intervention:
        raise ValueError("p_control and p_intervention cannot be equal — no detectable effect.")
    if allocation_ratio <= 0:
        raise ValueError("allocation_ratio must be positive.")

    raw_effect = p_intervention - p_control
    z_a, z_b, effect = _hypothesis_z_and_effect(
        hypothesis=hypothesis,
        alpha=alpha,
        power=power,
        raw_effect=raw_effect,
        margin=margin,
        one_sided=one_sided,
    )
    k = allocation_ratio  # intervention-to-control
    p1 = p_control
    p2 = p_intervention
    var = p1 * (1 - p1) / 1.0 + p2 * (1 - p2) / k
    n_control = (z_a + z_b) ** 2 * var / effect**2
    n_intervention = n_control * k

    out: dict[str, Any] = {
        "outcome_type": "two_proportions",
        "hypothesis": hypothesis,
        "n_per_arm_control": _inflate_for_dropout(n_control, dropout_rate),
        "n_per_arm_intervention": _inflate_for_dropout(n_intervention, dropout_rate),
        "events_required": None,
        "formula_name": _formula_label("Two-proportions Z-test", hypothesis),
        "formula_reference": (
            "Fleiss JL, Levin B, Paik MC. Statistical Methods for Rates and "
            "Proportions, 3rd ed. Wiley, 2003. §4.3."
            + (_NI_EQUIV_REFERENCE if hypothesis != "superiority" else "")
        ),
        "inputs": {
            "p_control": p_control,
            "p_intervention": p_intervention,
            "alpha": alpha,
            "power": power,
            "allocation_ratio": allocation_ratio,
            "dropout_rate": dropout_rate,
            "one_sided": one_sided if hypothesis == "superiority" else True,
            "hypothesis": hypothesis,
            "margin": margin,
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
    hypothesis: str = "superiority",
    margin: float | None = None,
) -> dict[str, Any]:
    """Two-sample t-test sample size with a common SD assumption.

    Solves the closed-form Z approximation then iterates once with the
    exact t-distribution to tighten the rounding for small N (typical
    statsmodels behaviour). Cohen's d standardised effect is reported in
    the inputs for transparency.

    `hypothesis` selects the test (see `sample_size_two_proportions`); for
    "non_inferiority" / "equivalence" supply `margin` (>0) on the mean
    scale and treat mean_control / mean_intervention as the assumed true
    means (equal is the usual assumption).
    """
    if standard_deviation <= 0:
        raise ValueError("standard_deviation must be positive.")
    if hypothesis == "superiority" and mean_control == mean_intervention:
        raise ValueError(
            "mean_control and mean_intervention cannot be equal — no detectable effect."
        )
    if allocation_ratio <= 0:
        raise ValueError("allocation_ratio must be positive.")

    raw_diff = mean_intervention - mean_control
    d = raw_diff / standard_deviation
    z_a, z_b, effect = _hypothesis_z_and_effect(
        hypothesis=hypothesis,
        alpha=alpha,
        power=power,
        raw_effect=raw_diff,
        margin=margin,
        one_sided=one_sided,
    )
    # Standardised denominator effect (Cohen's d on the effect that the
    # test actually powers against — the raw gap for superiority, the
    # margin-adjusted distance for NI / equivalence).
    d_effect = effect / standard_deviation
    k = allocation_ratio
    # Closed-form Z approximation (Cohen 1988, eqn 2.4.1).
    n_control_z = (z_a + z_b) ** 2 * (1.0 + 1.0 / k) / d_effect**2
    # One Newton-style refinement using the exact t critical values. For
    # NI / equivalence the test is one-sided at alpha; equivalence keeps
    # the Normal z_beta (the β/2 split has no simple t analogue) and only
    # refines the alpha critical value.
    ni_equiv = hypothesis != "superiority"
    n_control = n_control_z
    for _ in range(8):  # converges in ~3 iterations; cap is just safety
        df = n_control * (1.0 + k) - 2
        if df <= 1:
            break
        t_a_q = alpha if (one_sided or ni_equiv) else alpha / 2.0
        t_a = float(t.ppf(1.0 - t_a_q, df))
        t_b = float(t.ppf(power, df)) if hypothesis != "equivalence" else z_b
        n_new = (t_a + t_b) ** 2 * (1.0 + 1.0 / k) / d_effect**2
        if abs(n_new - n_control) < 0.1:
            n_control = n_new
            break
        n_control = n_new
    n_intervention = n_control * k

    out: dict[str, Any] = {
        "outcome_type": "two_means",
        "hypothesis": hypothesis,
        "n_per_arm_control": _inflate_for_dropout(n_control, dropout_rate),
        "n_per_arm_intervention": _inflate_for_dropout(n_intervention, dropout_rate),
        "events_required": None,
        "formula_name": _formula_label("Two-sample t-test (Cohen, common SD)", hypothesis),
        "formula_reference": (
            "Cohen J. Statistical Power Analysis for the Behavioral Sciences, "
            "2nd ed. Erlbaum, 1988. §2.4." + (_NI_EQUIV_REFERENCE if ni_equiv else "")
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
            "one_sided": one_sided if hypothesis == "superiority" else True,
            "hypothesis": hypothesis,
            "margin": margin,
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


def sample_size_sensitivity(
    *,
    outcome_type: str,
    base_params: dict[str, Any],
    vary: dict[str, list[Any]],
    max_combinations: int = 100,
) -> dict[str, Any]:
    """Sample-size grid over combinations of varied design parameters.

    `base_params` are the fixed inputs for `outcome_type` (same shape as
    the sample_size tool's `params`); `vary` maps a parameter name to the
    list of values to sweep (e.g. {"alpha": [0.05, 0.025], "power": [0.8,
    0.9], "dropout_rate": [0.0, 0.1, 0.2]}). Every element of the Cartesian
    product is evaluated through the SAME closed-form dispatcher as a
    single-point run, so the grid is exactly consistent with the scalar
    tool — reviewers (IRB / sponsor) get the α/β/dropout/effect
    sensitivity table they routinely ask for without a re-run cycle.

    Returns {outcome_type, varied, rows, truncated}. Each row carries the
    swept `params` plus n_per_arm_control / n_per_arm_intervention /
    n_total / events_required — or an `error` string when that cell is
    infeasible (e.g. a non-inferiority effect outside the margin), so one
    bad combination never sinks the whole grid. `truncated` is True when
    the product exceeded `max_combinations` (the tail is dropped, not
    silently — callers should surface it).
    """
    if not vary:
        raise ValueError("`vary` must map at least one parameter to a non-empty list.")
    keys = sorted(vary)
    if any(not vary[k] for k in keys):
        raise ValueError("every `vary` entry must be a non-empty list of values.")
    combos = list(itertools.product(*(vary[k] for k in keys)))
    truncated = len(combos) > max_combinations
    combos = combos[:max_combinations]

    rows: list[dict[str, Any]] = []
    for combo in combos:
        overrides = dict(zip(keys, combo, strict=True))
        row: dict[str, Any] = {"params": overrides}
        try:
            result = _dispatch(outcome_type, {**base_params, **overrides})
        except KeyError as e:
            row["error"] = f"Missing required parameter: {e}"
            rows.append(row)
            continue
        except ValueError as e:
            row["error"] = str(e)
            rows.append(row)
            continue
        if "error" in result:
            row["error"] = result["error"]
        else:
            row["n_per_arm_control"] = result["n_per_arm_control"]
            row["n_per_arm_intervention"] = result["n_per_arm_intervention"]
            row["n_total"] = result["n_total"]
            row["events_required"] = result.get("events_required")
        rows.append(row)

    return {
        "outcome_type": outcome_type,
        "varied": keys,
        "rows": rows,
        "truncated": truncated,
    }


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
            hypothesis=str(params.get("hypothesis", "superiority")),
            margin=(None if params.get("margin") is None else float(params["margin"])),
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
            hypothesis=str(params.get("hypothesis", "superiority")),
            margin=(None if params.get("margin") is None else float(params["margin"])),
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

        Non-inferiority / equivalence (two_proportions + two_means only):
          set `hypothesis` to "non_inferiority" or "equivalence" and
          supply `margin` (>0, on the outcome's natural scale). The
          control / intervention values are then the ASSUMED TRUE effect
          (usually equal — the design powers against the margin). Both
          are one-sided at alpha; equivalence uses the TOST β/2 split.

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

    @agent.tool
    async def sample_size_grid(
        ctx: RunContext[AgentDeps],
        outcome_type: OutcomeType,
        base_params: dict[str, Any],
        vary: dict[str, list[Any]],
    ) -> str:
        """
        Sensitivity table: sample size across combinations of design inputs.

        Same `outcome_type` + `base_params` as the `sample_size` tool, plus
        `vary` — a map of parameter name → list of values to sweep. The
        Cartesian product is evaluated (capped at 100 cells) with the same
        closed forms, so the grid is exactly consistent with a single-point
        run. Use this for the α / β / dropout / effect sensitivity table
        that IRBs and sponsors ask for.

        Example vary: {"alpha": [0.05, 0.025], "power": [0.8, 0.9],
        "dropout_rate": [0.0, 0.1, 0.2]}.

        Returns JSON {outcome_type, varied, rows[], truncated}; a cell that
        is infeasible carries an `error` instead of counts.
        """

        async def _impl() -> str:
            try:
                result = await asyncio.to_thread(
                    lambda: sample_size_sensitivity(
                        outcome_type=outcome_type,
                        base_params=base_params,
                        vary=vary,
                    )
                )
            except ValueError as e:
                return json.dumps({"error": str(e)})
            return json.dumps(result, ensure_ascii=False)

        return await emit_run(
            ctx,
            tool="sample_size_grid",
            icon="🧮",
            args={"outcome_type": outcome_type, "varied": sorted(vary)},
            description=f"Sample-size sensitivity grid for {outcome_type}",
            impl=_impl,
        )


__all__ = [
    "Hypothesis",
    "OutcomeType",
    "register",
    "sample_size_paired",
    "sample_size_sensitivity",
    "sample_size_time_to_event",
    "sample_size_two_means",
    "sample_size_two_proportions",
]
