"""Two-stage IPD meta-analysis — sandbox-side.

Fits per-trial models then pools the trial-level estimates via
DerSimonian-Laird random effects. Same per-trial estimators as
one_stage.py's per-trial functions; the second stage is the classical
inverse-variance + tau² pool.

Reads ``/home/sandbox/input/data.json`` (same shape as one_stage.py).
Writes ``/home/sandbox/output/ipd-two-stage.json``.

The two-stage approach is transparent: each per-trial estimate is
auditable, and the inter-trial heterogeneity (I² + τ²) is computed
the standard way. Recommended as a sanity check alongside one_stage:
large discrepancy = model-misspecification signal.
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2, norm

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _is_log_scale(effect_measure: str) -> bool:
    return effect_measure in ("OR", "RR", "HR")


def _read_trial_df(trial: dict) -> pd.DataFrame | None:
    csv_text = trial.get("rows_csv") or ""
    if not csv_text.strip():
        return None
    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception:
        return None
    df["__trial_id__"] = str(trial.get("trial_id"))
    df["__treatment__"] = (
        df[trial["treatment_column"]].astype(str) == str(trial["treatment_active_value"])
    ).astype(int)
    return df


def _per_trial_continuous(df: pd.DataFrame, trial_id: str, outcome_col: str) -> dict | None:
    trt = df.loc[df["__treatment__"] == 1, outcome_col].astype(float).dropna()
    ctl = df.loc[df["__treatment__"] == 0, outcome_col].astype(float).dropna()
    if len(trt) < 2 or len(ctl) < 2:
        return None
    diff = float(trt.mean() - ctl.mean())
    var = float(trt.var(ddof=1) / len(trt) + ctl.var(ddof=1) / len(ctl))
    return {
        "trial_id": trial_id,
        "n_subjects": int(len(trt) + len(ctl)),
        "log_effect": diff,
        "var": var,
    }


def _per_trial_binary(df: pd.DataFrame, trial_id: str, outcome_col: str) -> dict | None:
    trt = df.loc[df["__treatment__"] == 1, outcome_col].astype(float).dropna()
    ctl = df.loc[df["__treatment__"] == 0, outcome_col].astype(float).dropna()
    if len(trt) < 2 or len(ctl) < 2:
        return None
    a = float(trt.sum()) + 0.5
    b = float(len(trt) - trt.sum()) + 0.5
    c = float(ctl.sum()) + 0.5
    d = float(len(ctl) - ctl.sum()) + 0.5
    log_or = math.log((a * d) / (b * c))
    var = 1 / a + 1 / b + 1 / c + 1 / d
    return {
        "trial_id": trial_id,
        "n_subjects": int(len(trt) + len(ctl)),
        "log_effect": log_or,
        "var": var,
    }


def _per_trial_tte(
    df: pd.DataFrame, trial_id: str, outcome_col: str, event_col: str
) -> dict | None:
    try:
        from statsmodels.duration.hazard_regression import PHReg
    except ImportError:
        return None
    sub = df[[outcome_col, event_col, "__treatment__"]].dropna()
    if sub["__treatment__"].nunique() < 2:
        return None
    n_events = int(sub[event_col].astype(int).sum())
    if n_events < 5:
        return None
    try:
        fit = PHReg(
            sub[outcome_col].astype(float).to_numpy(),
            sub[["__treatment__"]].astype(float).to_numpy(),
            status=sub[event_col].astype(int).to_numpy(),
        ).fit()
        coef = float(fit.params[0])
        se = float(fit.bse[0])
        return {
            "trial_id": trial_id,
            "n_subjects": len(sub),
            "log_effect": coef,
            "var": se**2,
        }
    except Exception:
        return None


def _dl_pool(rows: list[dict]) -> dict:
    """DerSimonian-Laird random-effects pool on log-effects + variances."""
    if len(rows) < 2:
        return {
            "fitted": False,
            "skip_reason": "Need ≥2 per-trial estimates.",
        }
    log_es = np.array([r["log_effect"] for r in rows])
    vars_ = np.array([r["var"] for r in rows])
    w_fixed = 1.0 / vars_
    fixed_pooled_log = float(np.sum(w_fixed * log_es) / np.sum(w_fixed))
    q = float(np.sum(w_fixed * (log_es - fixed_pooled_log) ** 2))
    df = len(log_es) - 1
    c_const = float(np.sum(w_fixed) - np.sum(w_fixed**2) / np.sum(w_fixed))
    tau2 = max(0.0, (q - df) / c_const) if c_const > 0 else 0.0
    w_re = 1.0 / (vars_ + tau2)
    pooled_log = float(np.sum(w_re * log_es) / np.sum(w_re))
    se_pooled = float(math.sqrt(1.0 / np.sum(w_re)))
    z = float(norm.ppf(0.975))
    i_squared = max(0.0, 100.0 * (q - df) / q) if q > 0 else 0.0
    het_p = float(1.0 - chi2.cdf(q, df)) if df > 0 else 1.0
    return {
        "fitted": True,
        "log_effect": pooled_log,
        "se": se_pooled,
        "ci_lower_log": pooled_log - z * se_pooled,
        "ci_upper_log": pooled_log + z * se_pooled,
        "p_value": (
            float(2 * (1 - norm.cdf(abs(pooled_log) / se_pooled))) if se_pooled > 0 else None
        ),
        "i_squared": float(i_squared),
        "tau_squared": float(tau2),
        "heterogeneity_p": het_p,
        "method": "DerSimonian-Laird random-effects (per-trial estimates)",
    }


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    trials = data.get("trials") or []
    effect_measure = str(data.get("effect_measure", "OR"))

    per_trial_raw: list[dict] = []
    for t in trials:
        df = _read_trial_df(t)
        if df is None or df.empty:
            continue
        outcome_col = t.get("outcome_column")
        trial_id = str(t.get("trial_id"))
        if not outcome_col or outcome_col not in df.columns:
            continue
        if effect_measure in ("MD", "SMD"):
            row = _per_trial_continuous(df, trial_id, outcome_col)
        elif effect_measure in ("OR", "RR"):
            row = _per_trial_binary(df, trial_id, outcome_col)
        elif effect_measure == "HR":
            event_col = t.get("event_column")
            if not event_col or event_col not in df.columns:
                row = None
            else:
                row = _per_trial_tte(df, trial_id, outcome_col, event_col)
        else:
            row = None
        if row is not None:
            per_trial_raw.append(row)

    if len(per_trial_raw) < 2:
        (_OUTPUT_DIR / "ipd-two-stage.json").write_text(
            json.dumps(
                {
                    "fitted": False,
                    "skip_reason": "Need ≥2 per-trial estimates.",
                    "n_per_trial_effects": len(per_trial_raw),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    log_scale = _is_log_scale(effect_measure)
    pool = _dl_pool(per_trial_raw)
    out: dict = {
        "effect_measure": effect_measure,
        "n_trials": len(per_trial_raw),
        "n_subjects": int(sum(r["n_subjects"] for r in per_trial_raw)),
        "per_trial": [],
    }
    if pool.get("fitted"):
        out["effect"] = float(math.exp(pool["log_effect"])) if log_scale else pool["log_effect"]
        out["ci_lower"] = (
            float(math.exp(pool["ci_lower_log"])) if log_scale else pool["ci_lower_log"]
        )
        out["ci_upper"] = (
            float(math.exp(pool["ci_upper_log"])) if log_scale else pool["ci_upper_log"]
        )
        out["p_value"] = pool.get("p_value")
        out["i_squared"] = pool.get("i_squared")
        out["tau_squared"] = pool.get("tau_squared")
        out["heterogeneity_p"] = pool.get("heterogeneity_p")
        out["method"] = pool.get("method")
        out["fitted"] = True
    else:
        out.update(pool)
    z = float(norm.ppf(0.975))
    for r in per_trial_raw:
        eff = float(math.exp(r["log_effect"])) if log_scale else float(r["log_effect"])
        se = float(math.sqrt(r["var"]))
        lo = (
            float(math.exp(r["log_effect"] - z * se))
            if log_scale
            else float(r["log_effect"] - z * se)
        )
        hi = (
            float(math.exp(r["log_effect"] + z * se))
            if log_scale
            else float(r["log_effect"] + z * se)
        )
        out["per_trial"].append(
            {
                "trial_id": r["trial_id"],
                "n_subjects": r["n_subjects"],
                "effect": eff,
                "ci_lower": lo,
                "ci_upper": hi,
                "se": se,
            }
        )
    (_OUTPUT_DIR / "ipd-two-stage.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        f"[ipd_two_stage] measure={effect_measure} trials={out['n_trials']} "
        f"subjects={out['n_subjects']} fitted={out.get('fitted', False)}"
    )


# Defensive: silence "unused" warnings in linters that don't recognise
# the numpy/scipy imports as needed (the script is excluded from strict
# linting, but defence in depth).
_ = np


if __name__ == "__main__":
    main()
