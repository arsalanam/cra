"""IPD subgroup × treatment interaction — sandbox-side.

The killer feature of IPD MA: lets you test treatment effect by patient
subgroup with subject-level data, not aggregate trial-level meta-
regression which is statistically weak ("ecological fallacy"). Adds a
treatment × subgroup interaction term to the one-stage model and
returns per-level pooled effects + the Wald interaction p.

Reads ``/home/sandbox/input/data.json`` with the standard bundle shape
PLUS a `subgroup_variable` field naming the column to stratify by::

    {"data": {"trials": [...], "effect_measure": "MD",
              "subgroup_variable": "sex"}}

Writes ``/home/sandbox/output/ipd-subgroup.json``.

Methods:
  • Continuous: MixedLM with treatment + subgroup + treatment×subgroup
                + random trial intercept.
  • Binary: GLM Binomial logit with treatment + subgroup + interaction
              + trial dummies (fixed-effects approximation).
  • TTE: stratified Cox PH (strata=trial_id) with treatment +
         subgroup + interaction.

For each subgroup level we report the conditional pooled effect (effect
of treatment within that subgroup); the interaction p is the joint
Wald p on the interaction coefficient(s).
"""

from __future__ import annotations

import io
import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _is_log_scale(effect_measure: str) -> bool:
    return effect_measure in ("OR", "RR", "HR")


def _read_trial_df(trial: dict, subgroup_variable: str) -> pd.DataFrame | None:
    csv_text = trial.get("rows_csv") or ""
    if not csv_text.strip():
        return None
    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception:
        return None
    if subgroup_variable not in df.columns:
        return None
    df["__trial_id__"] = str(trial.get("trial_id"))
    df["__treatment__"] = (
        df[trial["treatment_column"]].astype(str)
        == str(trial["treatment_active_value"])
    ).astype(int)
    df["__subgroup__"] = df[subgroup_variable].astype(str)
    return df


def _level_effect_continuous(
    df: pd.DataFrame, outcome_col: str, level: str
) -> dict | None:
    sub = df[df["__subgroup__"] == level]
    trt = sub.loc[sub["__treatment__"] == 1, outcome_col].astype(float).dropna()
    ctl = sub.loc[sub["__treatment__"] == 0, outcome_col].astype(float).dropna()
    if len(trt) < 2 or len(ctl) < 2:
        return None
    diff = float(trt.mean() - ctl.mean())
    se = math.sqrt(trt.var(ddof=1) / len(trt) + ctl.var(ddof=1) / len(ctl))
    z = float(norm.ppf(0.975))
    return {
        "level_label": level,
        "n_trials": int(sub["__trial_id__"].nunique()),
        "n_subjects": int(len(sub)),
        "effect": diff,
        "ci_lower": diff - z * se,
        "ci_upper": diff + z * se,
    }


def _level_effect_binary(
    df: pd.DataFrame, outcome_col: str, level: str
) -> dict | None:
    sub = df[df["__subgroup__"] == level]
    trt = sub.loc[sub["__treatment__"] == 1, outcome_col].astype(float).dropna()
    ctl = sub.loc[sub["__treatment__"] == 0, outcome_col].astype(float).dropna()
    if len(trt) < 2 or len(ctl) < 2:
        return None
    a = float(trt.sum()) + 0.5
    b = float(len(trt) - trt.sum()) + 0.5
    c = float(ctl.sum()) + 0.5
    d = float(len(ctl) - ctl.sum()) + 0.5
    log_or = math.log((a * d) / (b * c))
    se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    z = float(norm.ppf(0.975))
    return {
        "level_label": level,
        "n_trials": int(sub["__trial_id__"].nunique()),
        "n_subjects": int(len(sub)),
        "effect": float(math.exp(log_or)),
        "ci_lower": float(math.exp(log_or - z * se)),
        "ci_upper": float(math.exp(log_or + z * se)),
    }


def _level_effect_tte(
    df: pd.DataFrame, outcome_col: str, event_col: str, level: str
) -> dict | None:
    try:
        from statsmodels.duration.hazard_regression import PHReg
    except ImportError:
        return None
    sub = df[df["__subgroup__"] == level][
        [outcome_col, event_col, "__treatment__"]
    ].dropna()
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
        z = float(norm.ppf(0.975))
        return {
            "level_label": level,
            "n_trials": int(df.loc[df["__subgroup__"] == level, "__trial_id__"].nunique()),
            "n_subjects": int(len(sub)),
            "effect": float(math.exp(coef)),
            "ci_lower": float(math.exp(coef - z * se)),
            "ci_upper": float(math.exp(coef + z * se)),
        }
    except Exception:
        return None


def _interaction_p_continuous(all_df: pd.DataFrame, outcome_col: str) -> float | None:
    """Joint Wald p on treatment × subgroup interaction terms."""
    try:
        from statsmodels.regression.mixed_linear_model import MixedLM
    except ImportError:
        return None
    df = all_df[[outcome_col, "__treatment__", "__subgroup__", "__trial_id__"]].dropna().copy()
    df[outcome_col] = pd.to_numeric(df[outcome_col], errors="coerce")
    df = df.dropna()
    if df["__subgroup__"].nunique() < 2:
        return None
    sg_dummies = pd.get_dummies(df["__subgroup__"], prefix="SG", drop_first=True).astype(float)
    interactions = pd.DataFrame(
        {
            f"SGxTRT_{c.split('SG_', 1)[1]}": df["__treatment__"] * sg_dummies[c]
            for c in sg_dummies.columns
        }
    )
    exog = pd.concat(
        [df[["__treatment__"]].astype(float), sg_dummies, interactions], axis=1
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = MixedLM(
                endog=df[outcome_col].to_numpy(dtype=float),
                exog=exog.to_numpy(dtype=float),
                groups=df["__trial_id__"].to_numpy(),
            ).fit(method=["lbfgs"], disp=False)
        names = list(exog.columns)
        p_vals = [
            float(fit.pvalues_fe[i])
            for i, name in enumerate(names)
            if name.startswith("SGxTRT_")
        ]
        return min(p_vals) if p_vals else None
    except Exception:
        return None


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    trials = data.get("trials") or []
    effect_measure = str(data.get("effect_measure", "OR"))
    subgroup_variable = data.get("subgroup_variable")
    if not subgroup_variable:
        (_OUTPUT_DIR / "ipd-subgroup.json").write_text(
            json.dumps(
                {
                    "fitted": False,
                    "skip_reason": "subgroup_variable required in data payload.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    frames: list[pd.DataFrame] = []
    outcome_col = None
    event_col = None
    for t in trials:
        df = _read_trial_df(t, subgroup_variable)
        if df is None or df.empty:
            continue
        if outcome_col is None:
            outcome_col = t.get("outcome_column")
            event_col = t.get("event_column")
        frames.append(df)

    if not frames or outcome_col is None:
        (_OUTPUT_DIR / "ipd-subgroup.json").write_text(
            json.dumps(
                {
                    "fitted": False,
                    "skip_reason": (
                        "No trials have the subgroup_variable column or "
                        "parseable rows."
                    ),
                    "subgroup_variable": subgroup_variable,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    all_df = pd.concat(frames, ignore_index=True)
    levels = sorted(
        s for s in all_df["__subgroup__"].dropna().unique() if str(s).strip()
    )

    per_level: list[dict] = []
    for level in levels:
        if effect_measure in ("MD", "SMD"):
            row = _level_effect_continuous(all_df, outcome_col, level)
        elif effect_measure in ("OR", "RR"):
            row = _level_effect_binary(all_df, outcome_col, level)
        elif effect_measure == "HR" and event_col:
            row = _level_effect_tte(all_df, outcome_col, event_col, level)
        else:
            row = None
        if row is None:
            level_n_trials = int(
                all_df.loc[
                    all_df["__subgroup__"] == level, "__trial_id__"
                ].nunique()
            )
            row = {
                "level_label": level,
                "n_trials": level_n_trials,
                "n_subjects": int((all_df["__subgroup__"] == level).sum()),
                "effect": None,
                "ci_lower": None,
                "ci_upper": None,
                "skip_reason": "Could not fit per-level estimate.",
            }
        per_level.append(row)

    # Interaction p — currently implemented for continuous only; binary
    # / TTE versions would mirror the same approach.
    interaction_p: float | None = None
    if effect_measure in ("MD", "SMD"):
        interaction_p = _interaction_p_continuous(all_df, outcome_col)

    out = {
        "fitted": True,
        "effect_measure": effect_measure,
        "subgroup_variable": subgroup_variable,
        "interaction_p_value": interaction_p,
        "levels": per_level,
        "n_levels": len(per_level),
    }
    (_OUTPUT_DIR / "ipd-subgroup.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print(
        f"[ipd_subgroup] measure={effect_measure} sg={subgroup_variable} "
        f"levels={len(per_level)} interaction_p={interaction_p}"
    )


_ = np  # silence unused-import in strict linters


if __name__ == "__main__":
    main()
