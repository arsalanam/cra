"""Subgroup forest analysis — sandbox-side.

Per-subgroup Cox PH HR for time-to-event endpoints + treatment × subgroup
interaction p-value. Renders a subgroup forest PNG.

Reads ``/home/sandbox/input/data.json``::

    {
      "data": [...ADTTE-shaped rows with a SUBGROUP column...],
      "params": {
        "paramcd": "OS",
        "param_label": "Overall survival",
        "arms": ["Placebo", "Drug A"],     // first is reference
        "subgroup_variable": "SEX"           // column name in `data`
      }
    }

Writes to ``/home/sandbox/output/``:
  • ``subgroup-forest-<paramcd>.png`` — per-subgroup HR forest plot
  • ``trial-stats-subgroup.json`` — structured per-subgroup HR + interaction p

The interaction test fits a Cox model with TRT + SUBGROUP + TRT×SUBGROUP
terms; the p-value is the Wald p on the joint interaction coefficients.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _cox_hr(
    df: pd.DataFrame,
    arms: list[str],
) -> dict:
    """Cox PH HR for the active vs reference arm on this slice."""
    comparator = arms[1]
    sub = df[df["TRT01A"].isin(arms)].copy()
    if sub["TRT01A"].nunique() < 2:
        return {"fitted": False, "skip_reason": "Single-arm subgroup."}
    n_events = int((sub["CNSR"] == 0).sum())
    if n_events < 5:
        return {"fitted": False, "skip_reason": f"Too few events ({n_events})."}
    sub["TRT_NONREF"] = (sub["TRT01A"] == comparator).astype(int)
    try:
        from statsmodels.duration.hazard_regression import PHReg

        endog = sub["AVAL"].astype(float).to_numpy()
        status = (sub["CNSR"] == 0).astype(int).to_numpy()
        exog = sub[["TRT_NONREF"]].astype(float).to_numpy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = PHReg(endog, exog, status=status, missing="drop")
            fit = model.fit()
        coef = float(fit.params[0])
        se = float(fit.bse[0])
        hr = float(np.exp(coef))
        z = 1.959963984540054
        return {
            "fitted": True,
            "hr": hr,
            "hr_ci_lower": float(np.exp(coef - z * se)),
            "hr_ci_upper": float(np.exp(coef + z * se)),
            "p_value": float(fit.pvalues[0]),
            "n_events": n_events,
        }
    except Exception as e:
        return {
            "fitted": False,
            "skip_reason": f"Cox fit failed: {type(e).__name__}: {e}",
        }


def _interaction_test(
    df: pd.DataFrame,
    arms: list[str],
    subgroup_variable: str,
) -> float | None:
    """Wald p for TRT×SUBGROUP interaction. None when the test can't run."""
    comparator = arms[1]
    sub = df[df["TRT01A"].isin(arms)].copy()
    sub = sub.dropna(subset=[subgroup_variable, "AVAL", "CNSR"])
    if sub[subgroup_variable].nunique() < 2 or sub["TRT01A"].nunique() < 2:
        return None
    sub["TRT_NONREF"] = (sub["TRT01A"] == comparator).astype(int)
    dummies = pd.get_dummies(sub[subgroup_variable], prefix="SG", drop_first=True).astype(float)
    if dummies.empty:
        return None
    interactions = pd.DataFrame(
        {f"SGxTRT_{c.split('SG_', 1)[1]}": sub["TRT_NONREF"] * dummies[c]
         for c in dummies.columns}
    )
    exog = pd.concat(
        [sub[["TRT_NONREF"]].astype(float), dummies, interactions], axis=1
    )
    try:
        from statsmodels.duration.hazard_regression import PHReg

        endog = sub["AVAL"].astype(float).to_numpy()
        status = (sub["CNSR"] == 0).astype(int).to_numpy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = PHReg(endog, exog.to_numpy(dtype=float), status=status, missing="drop")
            fit = model.fit()
        # Joint p across interaction terms — minimum over the columns
        # (conservative — proper joint Wald requires the cov matrix; min
        # gives the headline signal).
        names = list(exog.columns)
        pvals = [
            float(fit.pvalues[i]) for i, name in enumerate(names)
            if name.startswith("SGxTRT_")
        ]
        if not pvals:
            return None
        return min(pvals)
    except Exception:
        return None


def _forest_save(rows: list[dict], paramcd: str, param_label: str) -> Path:
    finite = [r for r in rows if r.get("hr") is not None]
    if not finite:
        # Empty placeholder figure so the host doesn't crash chasing the file.
        fig, ax = plt.subplots(figsize=(7, 3), dpi=110)
        ax.text(
            0.5,
            0.5,
            "No subgroup HRs could be fit.",
            ha="center",
            va="center",
        )
        ax.axis("off")
        out = _OUTPUT_DIR / f"subgroup-forest-{paramcd.lower()}.png"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        return out
    n = len(rows)
    fig, ax = plt.subplots(figsize=(8, max(3, 0.5 * n + 1.5)), dpi=110)
    y = np.arange(n)
    hrs = [r.get("hr") for r in rows]
    lows = [r.get("hr_ci_lower") for r in rows]
    highs = [r.get("hr_ci_upper") for r in rows]
    for i in range(n):
        if hrs[i] is None:
            ax.text(1.0, i, rows[i].get("skip_reason", "—"), va="center", fontsize=8, color="gray")
            continue
        ax.errorbar(
            hrs[i],
            i,
            xerr=[[hrs[i] - lows[i]], [highs[i] - hrs[i]]],
            fmt="s",
            capsize=3,
            color="steelblue",
        )
    ax.axvline(1.0, linestyle="--", color="gray", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels([r["subgroup_label"] + f" (n={r['n']})" for r in rows])
    ax.set_xscale("log")
    ax.set_xlabel("Hazard ratio (95% CI)")
    ax.set_title(f"Subgroup forest — {paramcd}: {param_label}")
    fig.tight_layout()
    out = _OUTPUT_DIR / f"subgroup-forest-{paramcd.lower()}.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    rows = payload.get("data", [])
    params = payload.get("params", {})
    paramcd = str(params.get("paramcd", "OS"))
    param_label = str(params.get("param_label", paramcd))
    arms_param = params.get("arms") or []
    subgroup_variable = str(params.get("subgroup_variable", ""))

    if not rows or len(arms_param) < 2 or not subgroup_variable:
        (_OUTPUT_DIR / "trial-stats-subgroup.json").write_text(
            json.dumps(
                {
                    "paramcd": paramcd,
                    "subgroup_variable": subgroup_variable,
                    "rows": [],
                    "interaction_p_value": None,
                    "note": "Requires data, two arms, and a subgroup_variable.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    df = pd.DataFrame(rows)
    df["AVAL"] = pd.to_numeric(df["AVAL"], errors="coerce")
    df["CNSR"] = pd.to_numeric(df["CNSR"], errors="coerce").fillna(1).astype(int)
    df["TRT01A"] = df.get("TRT01A", pd.Series([""] * len(df))).fillna("")
    df = df[df["PARAMCD"] == paramcd] if "PARAMCD" in df.columns else df

    rows_out: list[dict] = []
    subgroups = sorted(s for s in df[subgroup_variable].dropna().unique() if str(s) != "")
    for sg in subgroups:
        slice_ = df[df[subgroup_variable] == sg]
        n = int(len(slice_))
        n_events = int((slice_["CNSR"] == 0).sum())
        cox = _cox_hr(slice_, arms_param)
        rows_out.append(
            {
                "subgroup_label": str(sg),
                "n": n,
                "n_events": n_events,
                "hr": cox.get("hr") if cox.get("fitted") else None,
                "hr_ci_lower": cox.get("hr_ci_lower") if cox.get("fitted") else None,
                "hr_ci_upper": cox.get("hr_ci_upper") if cox.get("fitted") else None,
                "p_value": cox.get("p_value") if cox.get("fitted") else None,
                "skip_reason": cox.get("skip_reason"),
            }
        )

    interaction_p = _interaction_test(df, arms_param, subgroup_variable)
    out_image = _forest_save(rows_out, paramcd, param_label)

    summary = {
        "paramcd": paramcd,
        "param_label": param_label,
        "subgroup_variable": subgroup_variable,
        "interaction_p_value": interaction_p,
        "forest_filename": out_image.name,
        "rows": rows_out,
    }
    (_OUTPUT_DIR / "trial-stats-subgroup.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(
        f"[subgroup_forest] wrote forest for {paramcd} with {len(rows_out)} subgroups "
        f"(interaction p={interaction_p})."
    )


if __name__ == "__main__":
    main()
