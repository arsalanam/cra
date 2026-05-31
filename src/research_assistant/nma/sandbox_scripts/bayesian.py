"""Bayesian network meta-analysis — sandbox-side.

PyMC MCMC sampler with a normal-likelihood arm-based model. Gracefully
skips with explicit error when PyMC is not pinned in the sandbox image.

Reads ``/home/sandbox/input/data.json`` (same shape as frequentist.py).
Writes ``/home/sandbox/output/nma-league-bayesian.json``.

The Bayesian path is documented as a deploy-time gate: rebuild the
sandbox image with PyMC + ArviZ pinned to activate. The script writes
a clear `skip_reason` when those imports fail so the host surface can
fall back to the frequentist backend without crashing.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _write_skip(reason: str, effect_measure: str = "OR") -> None:
    (_OUTPUT_DIR / "nma-league-bayesian.json").write_text(
        json.dumps(
            {
                "fitted": False,
                "backend": "bayesian",
                "effect_measure": effect_measure,
                "skip_reason": reason,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    effect_measure = str(data.get("effect_measure", "OR"))

    try:
        import numpy as np
        import pymc as pm
    except ImportError as e:
        _write_skip(
            (
                f"Bayesian backend requires PyMC pinned in the sandbox image — "
                f"rebuild with `pymc>=5` to activate. Import failure: {e}."
            ),
            effect_measure=effect_measure,
        )
        return

    studies = data.get("studies") or []
    interventions = data.get("interventions") or []
    if len(interventions) < 3 or len(studies) < 2:
        _write_skip(
            "NMA requires ≥3 interventions and ≥2 studies.",
            effect_measure=effect_measure,
        )
        return

    # Build arm-level data table: per (study, intervention) → (n, events) or (mean, sd).
    index_of = {name: i for i, name in enumerate(interventions)}
    n_int = len(interventions)
    log_scale = effect_measure in ("OR", "RR", "HR")

    contrasts_per_study: list[dict] = []
    for s in studies:
        arms_raw = s.get("arms") or []
        arms = [a for a in arms_raw if a.get("intervention") in index_of]
        if len(arms) < 2:
            continue
        contrasts_per_study.append({"arms": arms})
    if not contrasts_per_study:
        _write_skip("No usable studies.", effect_measure=effect_measure)
        return

    # Simple arm-based normal-likelihood model:
    #   log_or_ij ~ Normal(d_i - d_j, σ²_ij)
    # where d_0 = 0 (reference) and d_1..d_{K-1} are intervention effects.
    # Random-effects τ shared across all contrasts.
    log_effects: list[float] = []
    log_vars: list[float] = []
    arm_a_idx: list[int] = []
    arm_b_idx: list[int] = []

    import math

    for s in contrasts_per_study:
        arms = s["arms"]
        baseline = arms[0]
        b_idx = index_of[baseline["intervention"]]
        for arm in arms[1:]:
            a_idx = index_of[arm["intervention"]]
            if log_scale:
                e_a = (arm.get("events") or 0) + 0.5
                f_a = (arm.get("n") or 0) - (arm.get("events") or 0) + 0.5
                e_b = (baseline.get("events") or 0) + 0.5
                f_b = (baseline.get("n") or 0) - (baseline.get("events") or 0) + 0.5
                if (arm.get("n") or 0) <= 0 or (baseline.get("n") or 0) <= 0:
                    continue
                try:
                    log_eff = math.log((e_a * f_b) / (e_b * f_a))
                except (ValueError, ZeroDivisionError):
                    continue
                var = 1 / e_a + 1 / f_a + 1 / e_b + 1 / f_b
            else:
                m_a, sd_a, n_a = arm.get("mean"), arm.get("sd"), arm.get("n") or 0
                m_b, sd_b, n_b = baseline.get("mean"), baseline.get("sd"), baseline.get("n") or 0
                if None in (m_a, sd_a, m_b, sd_b) or n_a <= 0 or n_b <= 0:
                    continue
                log_eff = float(m_a - m_b)
                var = sd_a**2 / n_a + sd_b**2 / n_b
            log_effects.append(log_eff)
            log_vars.append(var)
            arm_a_idx.append(a_idx)
            arm_b_idx.append(b_idx)

    if not log_effects:
        _write_skip("No valid contrasts.", effect_measure=effect_measure)
        return

    y = np.array(log_effects)
    se = np.sqrt(log_vars)
    arm_a_arr = np.array(arm_a_idx)
    arm_b_arr = np.array(arm_b_idx)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pm.Model() as _:
                d = pm.Normal("d", mu=0.0, sigma=2.0, shape=n_int - 1)
                tau = pm.HalfNormal("tau", sigma=1.0)
                d_full = pm.math.concatenate([np.zeros(1), d])
                mu = d_full[arm_a_arr] - d_full[arm_b_arr]
                pm.Normal(
                    "y_obs",
                    mu=mu,
                    sigma=pm.math.sqrt(se**2 + tau**2),
                    observed=y,
                )
                trace = pm.sample(
                    draws=500,
                    tune=500,
                    chains=2,
                    progressbar=False,
                    random_seed=0,
                )
    except Exception as e:
        _write_skip(
            f"Bayesian sampler failed: {type(e).__name__}: {e}",
            effect_measure=effect_measure,
        )
        return

    posterior = trace.posterior["d"].stack(sample=("chain", "draw")).values  # (K-1, N)
    n_samples = posterior.shape[1]
    full_post = np.vstack([np.zeros((1, n_samples)), posterior])  # (K, N)
    reference = interventions[0]
    league_rows = []
    for i, row_name in enumerate(interventions):
        for j, col_name in enumerate(interventions):
            if i == j:
                continue
            diff_samples = full_post[i] - full_post[j]
            eff_samples = np.exp(diff_samples) if log_scale else diff_samples
            league_rows.append(
                {
                    "row_intervention": row_name,
                    "col_intervention": col_name,
                    "effect": float(np.median(eff_samples)),
                    "ci_lower": float(np.quantile(eff_samples, 0.025)),
                    "ci_upper": float(np.quantile(eff_samples, 0.975)),
                    "n_direct_trials": 0,  # not enumerated this slice
                    "n_indirect_paths": 0,
                }
            )

    # SUCRA from posterior rankings.
    ranks = np.argsort(np.argsort(-full_post, axis=0), axis=0) + 1
    mean_ranks = ranks.mean(axis=1)
    sucra_values = ((n_int - mean_ranks) / max(n_int - 1, 1)).tolist()
    sucra_table = [
        {
            "intervention": name,
            "sucra": float(sucra_values[i]),
            "mean_rank": float(mean_ranks[i]),
            "rank": int(np.argsort(np.argsort(-np.array(sucra_values)))[i] + 1),
        }
        for i, name in enumerate(interventions)
    ]

    out = {
        "fitted": True,
        "backend": "bayesian",
        "effect_measure": effect_measure,
        "reference": reference,
        "league_table": league_rows,
        "sucra": sucra_table,
        "n_samples": int(n_samples),
        "sampler": "PyMC NUTS, 2 chains, 500 tune + 500 draws",
    }
    (_OUTPUT_DIR / "nma-league-bayesian.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print(
        f"[nma_bayesian] backend=bayesian effect={effect_measure} "
        f"interventions={n_int} samples={n_samples}"
    )


if __name__ == "__main__":
    main()
