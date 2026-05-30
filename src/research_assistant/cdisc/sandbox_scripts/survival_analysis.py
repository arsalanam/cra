"""Survival analysis — K-M curves + Cox PH summary.

Runs INSIDE the sandbox container (Docker, no network). Reads
`/input/data.json` with the shape ::

    {
      "adtte": [{"USUBJID": "...", "PARAMCD": "TTAE", "AVAL": 12.5,
                  "CNSR": 0, "TRT01A": "Drug A"}, ...],
      "adsl":  [{"USUBJID": "...", "TRT01A": "Drug A"}, ...]
    }

Outputs go to ``/output/``:
  • ``km-{paramcd}.png`` — one Kaplan-Meier plot per parameter,
                          overlaid with stratification by TRT01A when
                          ≥2 arms are present + sample size ≥2 per arm.
  • ``cox-summary.json`` — per-PARAMCD Cox PH summary with HR, 95% CI,
                          p-value, and the reference treatment label.
                          Empty for params where Cox can't fit
                          (single arm / <5 events / convergence
                          failure). Errors are explicit rather than
                          fabricated.

Uses statsmodels.duration.hazard_regression.PHReg (already pinned in
the sandbox image — no lifelines dependency) plus pure-Python K-M
math so the bundle works without rebuilding the sandbox image.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # noqa: E402  — must precede pyplot import
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _kaplan_meier(times: np.ndarray, events: np.ndarray) -> pd.DataFrame:
    """Compute K-M survival function. Returns a DataFrame with columns
    ``t``, ``surv``, ``at_risk``, ``events``, ``censored`` aggregated
    at distinct event/censor times.
    """
    df = pd.DataFrame({"t": times, "e": events}).sort_values("t")
    rows: list[dict[str, float]] = [
        {"t": 0.0, "surv": 1.0, "at_risk": float(len(df)), "events": 0.0, "censored": 0.0}
    ]
    surv = 1.0
    n_at_risk = len(df)
    for t, group in df.groupby("t", sort=True):
        d = int(group["e"].sum())  # events at t
        c = int((group["e"] == 0).sum())  # censored at t
        if d > 0:
            surv *= 1.0 - d / n_at_risk
        rows.append(
            {
                "t": float(t),
                "surv": float(surv),
                "at_risk": float(n_at_risk),
                "events": float(d),
                "censored": float(c),
            }
        )
        n_at_risk -= d + c
    return pd.DataFrame(rows)


def _plot_km_one(ax, df_param: pd.DataFrame, *, label: str) -> None:
    """Draw the K-M step function + censor ticks for one stratum."""
    times = df_param["AVAL"].astype(float).to_numpy()
    events = (df_param["CNSR"] == 0).astype(int).to_numpy()
    if len(times) == 0:
        return
    km = _kaplan_meier(times, events)
    ax.step(km["t"], km["surv"], where="post", label=label, linewidth=1.6)
    # Censor ticks
    cens_t = df_param.loc[df_param["CNSR"] == 1, "AVAL"].astype(float)
    if not cens_t.empty:
        # Find surv at each censor time (use the last surv ≤ t)
        cens_surv = []
        for t in cens_t:
            mask = km["t"] <= t
            if mask.any():
                cens_surv.append(km.loc[mask, "surv"].iloc[-1])
            else:
                cens_surv.append(1.0)
        ax.scatter(cens_t, cens_surv, marker="|", s=60, linewidths=1.4)


def _km_plot(df: pd.DataFrame, paramcd: str, param_label: str) -> Path:
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=110)
    arms = sorted(a for a in df["TRT01A"].dropna().unique() if a not in ("TBD", ""))
    # Need ≥2 arms with ≥2 subjects each to stratify, otherwise plot
    # the pooled curve.
    can_stratify = len(arms) >= 2 and all(
        df[df["TRT01A"] == a].shape[0] >= 2 for a in arms
    )
    if can_stratify:
        for arm in arms:
            sub = df[df["TRT01A"] == arm]
            _plot_km_one(ax, sub, label=f"{arm} (n={len(sub)})")
    else:
        _plot_km_one(ax, df, label=f"All subjects (n={len(df)})")
    ax.set_xlabel("Time (days)")
    ax.set_ylabel("Survival probability")
    ax.set_ylim(-0.02, 1.02)
    ax.set_title(f"Kaplan-Meier — {paramcd}: {param_label}")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    out = _OUTPUT_DIR / f"km-{paramcd.lower()}.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def _cox_one(df: pd.DataFrame, paramcd: str, param_label: str) -> dict | None:
    """Cox PH on TRT01A using statsmodels.duration.

    Requires ≥2 arms with at least one event each and a total of ≥5
    events. Returns None when those preconditions aren't met — the
    JSON summary records the skip reason explicitly.
    """
    arms = [a for a in df["TRT01A"].dropna().unique() if a not in ("TBD", "")]
    if len(arms) < 2:
        return {
            "paramcd": paramcd,
            "param": param_label,
            "fitted": False,
            "skip_reason": "Single treatment arm — Cox PH requires ≥2 arms.",
        }
    n_events = int((df["CNSR"] == 0).sum())
    if n_events < 5:
        return {
            "paramcd": paramcd,
            "param": param_label,
            "fitted": False,
            "skip_reason": f"Too few events ({n_events}) — Cox PH requires ≥5.",
        }
    # Reference = the alphabetically-first arm; the rest get one
    # dummy each.
    reference = arms[0]
    others = arms[1:]
    df_fit = df.copy()
    for arm in others:
        df_fit[f"TRT_{arm}"] = (df_fit["TRT01A"] == arm).astype(int)
    covariate_cols = [f"TRT_{arm}" for arm in others]
    try:
        from statsmodels.duration.hazard_regression import PHReg

        endog = df_fit["AVAL"].astype(float).to_numpy()
        status = (df_fit["CNSR"] == 0).astype(int).to_numpy()  # 1 = event
        exog = df_fit[covariate_cols].astype(float).to_numpy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = PHReg(endog, exog, status=status, missing="drop")
            fit = model.fit()
        coefs = fit.params.tolist()
        hrs = np.exp(coefs).tolist()
        se = fit.bse.tolist()
        # 95% CIs on log-HR scale -> exponentiate
        lo = np.exp(np.array(coefs) - 1.959963984540054 * np.array(se)).tolist()
        hi = np.exp(np.array(coefs) + 1.959963984540054 * np.array(se)).tolist()
        pvals = fit.pvalues.tolist()
    except Exception as e:
        return {
            "paramcd": paramcd,
            "param": param_label,
            "fitted": False,
            "skip_reason": f"Cox PH fit failed: {type(e).__name__}: {e}",
        }
    return {
        "paramcd": paramcd,
        "param": param_label,
        "fitted": True,
        "reference": reference,
        "n_events": n_events,
        "n_subjects": int(len(df)),
        "rows": [
            {
                "comparison": f"{arm} vs {reference}",
                "hr": float(hrs[i]),
                "hr_95ci_lo": float(lo[i]),
                "hr_95ci_hi": float(hi[i]),
                "p_value": float(pvals[i]),
                "log_hr": float(coefs[i]),
                "se_log_hr": float(se[i]),
            }
            for i, arm in enumerate(others)
        ],
    }


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    adtte = pd.DataFrame(payload.get("adtte", []))
    if adtte.empty:
        # Still emit a manifest so the calling endpoint can persist
        # an empty result rather than treating an empty trial as an
        # error.
        (_OUTPUT_DIR / "cox-summary.json").write_text(
            json.dumps({"params": [], "note": "No ADTTE rows."}, indent=2),
            encoding="utf-8",
        )
        return

    # Defensive coercions — JSON can carry strings for these.
    adtte["AVAL"] = pd.to_numeric(adtte["AVAL"], errors="coerce")
    adtte["CNSR"] = pd.to_numeric(adtte["CNSR"], errors="coerce").fillna(1).astype(int)
    adtte["TRT01A"] = adtte.get("TRT01A", pd.Series([None] * len(adtte))).fillna("")

    param_summaries: list[dict] = []
    by_param = list(adtte.groupby("PARAMCD"))
    for paramcd, group in by_param:
        group = group.dropna(subset=["AVAL"])
        if group.empty:
            continue
        param_label = str(group["PARAM"].iloc[0]) if "PARAM" in group.columns else str(paramcd)
        try:
            _km_plot(group, str(paramcd), param_label)
        except Exception as e:
            print(f"[survival_analysis] K-M plot for {paramcd} failed: {e!r}")
        cox = _cox_one(group, str(paramcd), param_label)
        if cox is not None:
            param_summaries.append(cox)

    (_OUTPUT_DIR / "cox-summary.json").write_text(
        json.dumps({"params": param_summaries}, indent=2), encoding="utf-8"
    )
    print(
        f"[survival_analysis] Wrote {len(param_summaries)} Cox summaries "
        f"and {sum(1 for _ in _OUTPUT_DIR.glob('km-*.png'))} K-M plots."
    )


if __name__ == "__main__":
    main()
