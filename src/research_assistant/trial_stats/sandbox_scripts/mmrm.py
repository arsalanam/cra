"""MMRM (Mixed Models for Repeated Measures) — sandbox-side analysis.

Runs inside the Docker sandbox (no network). Reads
``/home/sandbox/input/data.json`` with the shape ::

    {
      "data": [
        {"USUBJID": "S001", "PARAMCD": "CHGFBL", "AVISIT": "Week 24",
         "AVISITN": 24, "AVAL": -1.4, "TRT01A": "Drug A", "BASE": 8.1},
        ...
      ],
      "params": {
        "paramcds": ["CHGFBL"],
        "arms": ["Placebo", "Drug A"],     // first is reference
        "target_visits": ["Week 24"]       // restrict LSMean reporting
      }
    }

Writes to ``/home/sandbox/output/``:
  • ``trial-stats-mmrm.json`` — per-PARAMCD × visit LSMean diffs + 95% CI

MMRM via ``statsmodels.regression.mixed_linear_model.MixedLM`` — visit as
a fixed effect, subject random intercept, BASE as a covariate when
present. This is a simplified MMRM (no unstructured covariance
matrix — statsmodels MixedLM defaults to a random intercept + residual
covariance). For regulator-grade MMRM with unstructured covariance,
operators run their qualified stats package; this is the headline-
generating preview.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _fit_one(
    df: pd.DataFrame,
    paramcd: str,
    arms: list[str],
    target_visits: list[str] | None,
) -> dict:
    if df.empty:
        return {
            "paramcd": paramcd,
            "fitted": False,
            "skip_reason": "No rows for this PARAMCD.",
        }
    if len(arms) < 2:
        return {
            "paramcd": paramcd,
            "fitted": False,
            "skip_reason": "Single arm — comparator required for MMRM.",
        }
    df = df.dropna(subset=["AVAL", "USUBJID", "TRT01A", "AVISIT"])
    if df.empty:
        return {
            "paramcd": paramcd,
            "fitted": False,
            "skip_reason": "All rows dropped after NA filter on AVAL/USUBJID/TRT01A/AVISIT.",
        }
    reference = arms[0]
    others = arms[1:]
    df = df[df["TRT01A"].isin(arms)].copy()
    if df["TRT01A"].nunique() < 2:
        return {
            "paramcd": paramcd,
            "fitted": False,
            "skip_reason": "Fewer than 2 arms after filtering to declared arms.",
        }
    df["TRT_NONREF"] = (df["TRT01A"] != reference).astype(int)
    visits = sorted(df["AVISIT"].dropna().unique().tolist())
    if not visits:
        return {
            "paramcd": paramcd,
            "fitted": False,
            "skip_reason": "No AVISIT values present.",
        }
    # One-hot encode visit (drop first as the baseline).
    visit_dummies = pd.get_dummies(df["AVISIT"], prefix="V", drop_first=True).astype(float)
    use_base = "BASE" in df.columns and df["BASE"].notna().any()
    fixed = pd.DataFrame({"TRT_NONREF": df["TRT_NONREF"].astype(float)})
    fixed = pd.concat([fixed, visit_dummies], axis=1)
    if use_base:
        fixed["BASE"] = pd.to_numeric(df["BASE"], errors="coerce").fillna(df["BASE"].mean())
    # Interaction terms TRT × visit so LSMean diffs vary by visit.
    interactions = pd.DataFrame(
        {
            f"TRTxV_{c.split('V_', 1)[1]}": fixed["TRT_NONREF"] * fixed[c]
            for c in fixed.columns
            if c.startswith("V_")
        }
    )
    fixed = pd.concat([fixed, interactions], axis=1)
    fixed = fixed.assign(_intercept=1.0)
    endog = pd.to_numeric(df["AVAL"], errors="coerce").to_numpy(dtype=float)
    valid_mask = ~np.isnan(endog) & ~fixed.isna().any(axis=1).to_numpy()
    if valid_mask.sum() < 6:
        return {
            "paramcd": paramcd,
            "fitted": False,
            "skip_reason": "Too few valid rows after coercion (<6).",
        }
    endog = endog[valid_mask]
    exog = fixed.loc[valid_mask].to_numpy(dtype=float)
    exog_names = list(fixed.columns)
    groups = df.loc[valid_mask, "USUBJID"].to_numpy()

    try:
        from statsmodels.regression.mixed_linear_model import MixedLM

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = MixedLM(endog=endog, exog=exog, groups=groups, exog_re=None)
            fit = model.fit(method=["lbfgs"], disp=False)
    except Exception as e:
        return {
            "paramcd": paramcd,
            "fitted": False,
            "skip_reason": f"MMRM fit failed: {type(e).__name__}: {e}",
        }

    params = dict(zip(exog_names, fit.fe_params.tolist(), strict=False))
    bse_map = dict(zip(exog_names, fit.bse_fe.tolist(), strict=False))
    pvals_map = dict(zip(exog_names, fit.pvalues_fe.tolist(), strict=False))
    visits_target = [v for v in visits if v in (target_visits or [])] or visits
    rows_out: list[dict] = []
    for v in visits_target:
        # LSMean diff at visit v = TRT_NONREF + TRTxV_{v}.
        diff = params.get("TRT_NONREF", 0.0)
        diff_se_sq = bse_map.get("TRT_NONREF", 0.0) ** 2
        interaction_key = f"TRTxV_{v}"
        if interaction_key in params:
            diff += params[interaction_key]
            diff_se_sq += bse_map.get(interaction_key, 0.0) ** 2
            # Approximate covariance ignored — gives slightly conservative CIs.
        se = float(np.sqrt(diff_se_sq))
        p_lookup = pvals_map.get(interaction_key, pvals_map.get("TRT_NONREF", float("nan")))
        rows_out.append(
            {
                "visit": v,
                "n_observed": int(((df["AVISIT"] == v) & valid_mask).sum()),
                "lsmean_difference": float(diff),
                "diff_ci_lower": float(diff - 1.959963984540054 * se),
                "diff_ci_upper": float(diff + 1.959963984540054 * se),
                "p_value": float(p_lookup),
                "comparison": f"{others[0]} vs {reference}" if others else "",
            }
        )
    return {
        "paramcd": paramcd,
        "fitted": True,
        "reference": reference,
        "comparator": others[0] if others else "",
        "model": "MMRM (statsmodels MixedLM, random subject intercept)",
        "use_base_covariate": use_base,
        "rows": rows_out,
    }


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    rows = payload.get("data", [])
    params = payload.get("params", {})
    paramcds_requested = params.get("paramcds") or []
    arms_param = params.get("arms") or []
    target_visits = params.get("target_visits") or []

    if not rows:
        (_OUTPUT_DIR / "trial-stats-mmrm.json").write_text(
            json.dumps({"params": [], "note": "No data rows."}, indent=2),
            encoding="utf-8",
        )
        return
    df = pd.DataFrame(rows)
    available = sorted(df["PARAMCD"].dropna().unique())
    targets = [p for p in available if not paramcds_requested or p in paramcds_requested]

    summaries: list[dict] = []
    for paramcd in targets:
        sub = df[df["PARAMCD"] == paramcd]
        summaries.append(_fit_one(sub, str(paramcd), arms_param, target_visits))

    (_OUTPUT_DIR / "trial-stats-mmrm.json").write_text(
        json.dumps({"params": summaries}, indent=2), encoding="utf-8"
    )
    print(f"[mmrm] wrote {len(summaries)} parameter summaries.")


if __name__ == "__main__":
    main()
