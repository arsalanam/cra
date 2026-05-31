"""One-stage IPD meta-analysis — sandbox-side.

Pools subject-level data across trials in a single multilevel model.
The default for IPD MA because it shares information across trials
(partial pooling) and gives lower-variance estimates than two-stage
when the random-effects assumption holds.

Reads ``/home/sandbox/input/data.json``::

    {
      "data": {
        "trials": [
          {"trial_id": "RCT-1", "treatment_column": "trt",
           "treatment_active_value": "1", "outcome_column": "y",
           "event_column": null, "covariate_columns": ["age", "sex"],
           "rows_csv": "subject_id,trt,y,age,sex\\n1,1,1,55,M\\n..."},
          ...
        ],
        "effect_measure": "OR"
      }
    }

Writes ``/home/sandbox/output/ipd-one-stage.json``::

    {
      "fitted": true,
      "effect": 0.72, "ci_lower": 0.55, "ci_upper": 0.94,
      "p_value": 0.014, "n_trials": 6, "n_subjects": 4250,
      "i_squared": 28.0, "tau_squared": 0.05,
      "method": "MixedLM REML (random trial intercept)",
      "per_trial": [{"trial_id": ..., "effect": ..., "ci_lower": ...,
                      "ci_upper": ..., "se": ..., "n_subjects": ...},
                    ...]
    }

Methods:
  • Continuous (MD, SMD): statsmodels MixedLM, fixed treatment effect,
    random trial intercept. REML estimator.
  • Binary (OR, RR): MixedLM on a logit-link approximation via
    Laplace-style scoring is heavier; instead we use a fixed-effects
    logistic GLM with trial dummies + treatment, with per-trial random
    intercepts approximated by the dummy-variable approach. Two-stage
    DerSimonian-Laird gives the true random-effects pooled effect.
    (For a strict random-effects logistic the operator should use
    one-stage in practice; this script ships the simpler fixed-effects
    approach + flags it in the `method` field.)
  • Time-to-event (HR): PHReg stratified by trial_id, treatment as a
    fixed covariate. Stratification absorbs trial baseline-hazard
    differences.
"""

from __future__ import annotations

import io
import json
import math
import warnings
from pathlib import Path

import pandas as pd
from scipy.stats import norm

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
    trt_y = df.loc[df["__treatment__"] == 1, outcome_col].astype(float).dropna()
    ctl_y = df.loc[df["__treatment__"] == 0, outcome_col].astype(float).dropna()
    if len(trt_y) < 2 or len(ctl_y) < 2:
        return None
    diff = float(trt_y.mean() - ctl_y.mean())
    se = math.sqrt(trt_y.var(ddof=1) / len(trt_y) + ctl_y.var(ddof=1) / len(ctl_y))
    z = float(norm.ppf(0.975))
    return {
        "trial_id": trial_id,
        "n_subjects": int(len(trt_y) + len(ctl_y)),
        "effect": diff,
        "ci_lower": diff - z * se,
        "ci_upper": diff + z * se,
        "se": se,
    }


def _per_trial_binary(df: pd.DataFrame, trial_id: str, outcome_col: str) -> dict | None:
    trt_y = df.loc[df["__treatment__"] == 1, outcome_col].astype(float).dropna()
    ctl_y = df.loc[df["__treatment__"] == 0, outcome_col].astype(float).dropna()
    if len(trt_y) < 2 or len(ctl_y) < 2:
        return None
    a = float(trt_y.sum()) + 0.5
    b = float(len(trt_y) - trt_y.sum()) + 0.5
    c = float(ctl_y.sum()) + 0.5
    d = float(len(ctl_y) - ctl_y.sum()) + 0.5
    log_or = math.log((a * d) / (b * c))
    se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    z = float(norm.ppf(0.975))
    return {
        "trial_id": trial_id,
        "n_subjects": int(len(trt_y) + len(ctl_y)),
        "effect": float(math.exp(log_or)),
        "ci_lower": float(math.exp(log_or - z * se)),
        "ci_upper": float(math.exp(log_or + z * se)),
        "se": se,
    }


def _per_trial_tte(
    df: pd.DataFrame, trial_id: str, outcome_col: str, event_col: str
) -> dict | None:
    """One-trial Cox PH on treatment alone."""
    try:
        from statsmodels.duration.hazard_regression import PHReg
    except ImportError:
        return None
    sub = df[[outcome_col, event_col, "__treatment__"]].dropna()
    n_events = int(sub[event_col].astype(int).sum())
    if n_events < 5 or sub["__treatment__"].nunique() < 2:
        return None
    endog = sub[outcome_col].astype(float).to_numpy()
    status = sub[event_col].astype(int).to_numpy()
    exog = sub[["__treatment__"]].astype(float).to_numpy()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = PHReg(endog, exog, status=status).fit()
        coef = float(fit.params[0])
        se = float(fit.bse[0])
        z = float(norm.ppf(0.975))
        return {
            "trial_id": trial_id,
            "n_subjects": int(len(sub)),
            "effect": float(math.exp(coef)),
            "ci_lower": float(math.exp(coef - z * se)),
            "ci_upper": float(math.exp(coef + z * se)),
            "se": se,
        }
    except Exception:
        return None


def _one_stage_continuous(all_df: pd.DataFrame, outcome_col: str) -> dict:
    """MixedLM with random trial intercept + treatment fixed effect."""
    try:
        from statsmodels.regression.mixed_linear_model import MixedLM
    except ImportError:
        return {"fitted": False, "skip_reason": "statsmodels not available."}
    df = all_df[[outcome_col, "__treatment__", "__trial_id__"]].dropna().copy()
    if df["__trial_id__"].nunique() < 2:
        return {"fitted": False, "skip_reason": "Need ≥2 trials with data."}
    df[outcome_col] = pd.to_numeric(df[outcome_col], errors="coerce")
    df = df.dropna()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            md = MixedLM(
                endog=df[outcome_col].to_numpy(dtype=float),
                exog=df[["__treatment__"]].to_numpy(dtype=float),
                groups=df["__trial_id__"].to_numpy(),
                exog_re=None,
            )
            fit = md.fit(method=["lbfgs"], disp=False)
        coef = float(fit.fe_params[0])
        se = float(fit.bse_fe[0])
        z = float(norm.ppf(0.975))
        # Heterogeneity: τ² = random-intercept variance.
        try:
            tau2 = float(fit.cov_re.iloc[0, 0])
        except Exception:
            tau2 = None
        # I² requires per-trial within-trial variance estimate; we use
        # tau² / (tau² + median(within_var)) as an approximation when
        # MixedLM doesn't expose the canonical I² directly.
        i2 = None
        try:
            resid_var = float(fit.scale)
            if tau2 is not None and (tau2 + resid_var) > 0:
                i2 = 100 * tau2 / (tau2 + resid_var)
        except Exception:
            i2 = None
        return {
            "fitted": True,
            "effect": coef,
            "ci_lower": coef - z * se,
            "ci_upper": coef + z * se,
            "p_value": float(fit.pvalues_fe[0]),
            "i_squared": i2,
            "tau_squared": tau2,
            "method": "MixedLM REML (random trial intercept + fixed treatment)",
        }
    except Exception as e:
        return {
            "fitted": False,
            "skip_reason": f"MixedLM fit failed: {type(e).__name__}: {e}",
        }


def _one_stage_binary(all_df: pd.DataFrame, outcome_col: str) -> dict:
    """GLM Binomial logit with trial dummies + treatment.

    Strict random-effects logistic via PyMC would be more correct but
    is heavy; the dummy-variable fixed-effects approach is the standard
    cheap-and-cheerful approximation.
    """
    try:
        from statsmodels.genmod.families import Binomial
        from statsmodels.genmod.generalized_linear_model import GLM
    except ImportError:
        return {"fitted": False, "skip_reason": "statsmodels GLM not available."}
    df = all_df[[outcome_col, "__treatment__", "__trial_id__"]].dropna().copy()
    df[outcome_col] = pd.to_numeric(df[outcome_col], errors="coerce")
    df = df.dropna()
    if df["__trial_id__"].nunique() < 2:
        return {"fitted": False, "skip_reason": "Need ≥2 trials."}
    trial_dummies = pd.get_dummies(df["__trial_id__"], prefix="T", drop_first=True).astype(float)
    exog = pd.concat([df[["__treatment__"]].astype(float), trial_dummies], axis=1)
    exog.insert(0, "_intercept", 1.0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = GLM(
                df[outcome_col].astype(float),
                exog.to_numpy(dtype=float),
                family=Binomial(),
            )
            fit = model.fit()
        coef = float(fit.params[1])  # __treatment__ is index 1 after intercept
        se = float(fit.bse[1])
        z = float(norm.ppf(0.975))
        return {
            "fitted": True,
            "effect": float(math.exp(coef)),
            "ci_lower": float(math.exp(coef - z * se)),
            "ci_upper": float(math.exp(coef + z * se)),
            "p_value": float(fit.pvalues[1]),
            "i_squared": None,
            "tau_squared": None,
            "method": (
                "GLM Binomial logit (trial dummies + treatment — fixed-effects approximation)"
            ),
        }
    except Exception as e:
        return {
            "fitted": False,
            "skip_reason": f"GLM Binomial fit failed: {type(e).__name__}: {e}",
        }


def _one_stage_tte(all_df: pd.DataFrame, outcome_col: str, event_col: str) -> dict:
    """Stratified Cox PH (strata = trial_id), treatment as covariate."""
    try:
        from statsmodels.duration.hazard_regression import PHReg
    except ImportError:
        return {"fitted": False, "skip_reason": "PHReg not available."}
    df = all_df[[outcome_col, event_col, "__treatment__", "__trial_id__"]].dropna().copy()
    if df["__trial_id__"].nunique() < 2 or df["__treatment__"].nunique() < 2:
        return {"fitted": False, "skip_reason": "Need ≥2 trials AND both arms."}
    endog = df[outcome_col].astype(float).to_numpy()
    status = df[event_col].astype(int).to_numpy()
    exog = df[["__treatment__"]].astype(float).to_numpy()
    # PHReg uses `strata` to absorb trial-level baseline hazard differences.
    strata = df["__trial_id__"].astype("category").cat.codes.to_numpy()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = PHReg(endog, exog, status=status, strata=strata).fit()
        coef = float(fit.params[0])
        se = float(fit.bse[0])
        z = float(norm.ppf(0.975))
        return {
            "fitted": True,
            "effect": float(math.exp(coef)),
            "ci_lower": float(math.exp(coef - z * se)),
            "ci_upper": float(math.exp(coef + z * se)),
            "p_value": float(fit.pvalues[0]),
            "i_squared": None,
            "tau_squared": None,
            "method": "Stratified Cox PH (strata=trial_id + fixed treatment)",
        }
    except Exception as e:
        return {
            "fitted": False,
            "skip_reason": f"Stratified Cox fit failed: {type(e).__name__}: {e}",
        }


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    trials = data.get("trials") or []
    effect_measure = str(data.get("effect_measure", "OR"))

    per_trial: list[dict] = []
    frames: list[pd.DataFrame] = []
    for t in trials:
        df = _read_trial_df(t)
        if df is None or df.empty:
            continue
        outcome_col = t.get("outcome_column")
        trial_id = str(t.get("trial_id"))
        if not outcome_col or outcome_col not in df.columns:
            continue
        frames.append(df)
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
            per_trial.append(row)

    if not frames or len(per_trial) < 2:
        (_OUTPUT_DIR / "ipd-one-stage.json").write_text(
            json.dumps(
                {
                    "fitted": False,
                    "skip_reason": ("Need ≥2 trials with parseable rows + the requested measure."),
                    "n_trials_parsed": len(frames),
                    "n_per_trial_effects": len(per_trial),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    all_df = pd.concat(frames, ignore_index=True)
    # Pick the per-measure one-stage routine.
    if effect_measure in ("MD", "SMD"):
        outcome_col = next(t["outcome_column"] for t in trials if "outcome_column" in t)
        pooled = _one_stage_continuous(all_df, outcome_col)
    elif effect_measure in ("OR", "RR"):
        outcome_col = next(t["outcome_column"] for t in trials if "outcome_column" in t)
        pooled = _one_stage_binary(all_df, outcome_col)
    elif effect_measure == "HR":
        outcome_col = next(t["outcome_column"] for t in trials if "outcome_column" in t)
        event_col = next(t.get("event_column", "") or "" for t in trials)
        if event_col and event_col in all_df.columns:
            pooled = _one_stage_tte(all_df, outcome_col, event_col)
        else:
            pooled = {"fitted": False, "skip_reason": "event_column missing."}
    else:
        pooled = {"fitted": False, "skip_reason": f"Unknown measure {effect_measure!r}."}

    out: dict = {
        "effect_measure": effect_measure,
        "n_trials": len(per_trial),
        "n_subjects": int(sum(p["n_subjects"] for p in per_trial)),
        "per_trial": per_trial,
    }
    out.update(pooled)
    (_OUTPUT_DIR / "ipd-one-stage.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        f"[ipd_one_stage] measure={effect_measure} trials={out['n_trials']} "
        f"subjects={out['n_subjects']} fitted={out.get('fitted', False)}"
    )


if __name__ == "__main__":
    main()
