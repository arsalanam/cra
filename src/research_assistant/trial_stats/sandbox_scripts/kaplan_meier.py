"""Kaplan-Meier + log-rank + Cox PH — sandbox-side analysis script.

Runs inside the Docker sandbox (no network). Reads
``/home/sandbox/input/data.json`` with the shape ::

    {
      "data": [
        {"USUBJID": "S001", "PARAMCD": "OS",
         "AVAL": 12.5, "CNSR": 0, "TRT01A": "Drug A"},
        ...
      ],
      "params": {
        "paramcds": ["OS"],          // analyses to run; if omitted, all PARAMCDs
        "arms": ["Placebo", "Drug A"], // first entry is reference
        "label_map": {"OS": "Overall survival"}
      }
    }

Writes to ``/home/sandbox/output/``:
  • ``km-<paramcd>.png`` — one K-M plot per parameter (stratified by
    TRT01A when ≥2 arms with ≥2 subjects each)
  • ``trial-stats-tte.json`` — structured summary the host parses

Anti-hallucination posture:
  - Skip reasons are reported explicitly ("Single arm", "Too few events")
    rather than fabricated as fitted Cox results.
  - K-M median is reported as "Not reached" when survival never crosses 0.5
    within the observation window.
  - Statsmodels.duration.PHReg is the Cox PH backend (already pinned in the
    sandbox image — no lifelines dependency).
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
from scipy.stats import chi2  # noqa: E402

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _kaplan_meier(times: np.ndarray, events: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame({"t": times, "e": events}).sort_values("t")
    rows: list[dict[str, float]] = [
        {"t": 0.0, "surv": 1.0, "at_risk": float(len(df)), "events": 0.0, "censored": 0.0}
    ]
    surv = 1.0
    n_at_risk = len(df)
    for t, group in df.groupby("t", sort=True):
        d = int(group["e"].sum())
        c = int((group["e"] == 0).sum())
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


def _km_median(km: pd.DataFrame) -> str:
    """Median event time from a K-M table. 'Not reached' when survival
    never drops to 0.5 within the observation window."""
    below = km[km["surv"] <= 0.5]
    if below.empty:
        return "Not reached"
    return f"{float(below.iloc[0]['t']):.2f}"


def _log_rank(df_by_arm: dict[str, pd.DataFrame]) -> float | None:
    """Two-arm log-rank test (chi-square, 1 df). Returns p-value or None
    when there are not enough events / arms."""
    arms = list(df_by_arm.keys())
    if len(arms) < 2:
        return None
    all_times = sorted(
        {float(t) for sub in df_by_arm.values() for t in sub.loc[sub["CNSR"] == 0, "AVAL"]}
    )
    if not all_times:
        return None
    # Counts at risk + events at each event time
    obs_minus_exp = 0.0
    variance = 0.0
    for t in all_times:
        n_at_risk_total = sum(int((sub["AVAL"] >= t).sum()) for sub in df_by_arm.values())
        d_total = sum(
            int(((sub["AVAL"] == t) & (sub["CNSR"] == 0)).sum()) for sub in df_by_arm.values()
        )
        if n_at_risk_total <= 1 or d_total == 0:
            continue
        # Per-arm contributions (focus on the first arm)
        sub_a = df_by_arm[arms[0]]
        n_a = int((sub_a["AVAL"] >= t).sum())
        d_a = int(((sub_a["AVAL"] == t) & (sub_a["CNSR"] == 0)).sum())
        e_a = d_total * n_a / n_at_risk_total
        var_a = (
            d_total
            * (n_at_risk_total - d_total)
            * n_a
            * (n_at_risk_total - n_a)
            / (n_at_risk_total**2 * (n_at_risk_total - 1))
        )
        obs_minus_exp += d_a - e_a
        variance += var_a
    if variance <= 0:
        return None
    chi_sq = obs_minus_exp**2 / variance
    return float(1.0 - chi2.cdf(chi_sq, df=1))


def _plot_km_one(ax: object, df_param: pd.DataFrame, *, label: str) -> None:
    times = df_param["AVAL"].astype(float).to_numpy()
    events = (df_param["CNSR"] == 0).astype(int).to_numpy()
    if len(times) == 0:
        return
    km = _kaplan_meier(times, events)
    ax.step(km["t"], km["surv"], where="post", label=label, linewidth=1.6)  # type: ignore[attr-defined]
    cens_t = df_param.loc[df_param["CNSR"] == 1, "AVAL"].astype(float)
    if not cens_t.empty:
        cens_surv = []
        for t in cens_t:
            mask = km["t"] <= t
            cens_surv.append(km.loc[mask, "surv"].iloc[-1] if mask.any() else 1.0)
        ax.scatter(cens_t, cens_surv, marker="|", s=60, linewidths=1.4)  # type: ignore[attr-defined]


def _km_plot_save(df: pd.DataFrame, paramcd: str, param_label: str) -> Path:
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=110)
    arms = sorted(a for a in df["TRT01A"].dropna().unique() if a not in ("TBD", ""))
    can_stratify = len(arms) >= 2 and all(df[df["TRT01A"] == a].shape[0] >= 2 for a in arms)
    if can_stratify:
        for arm in arms:
            sub = df[df["TRT01A"] == arm]
            _plot_km_one(ax, sub, label=f"{arm} (n={len(sub)})")
    else:
        _plot_km_one(ax, df, label=f"All subjects (n={len(df)})")
    ax.set_xlabel("Time")
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


def _cox_one(df: pd.DataFrame, paramcd: str, param_label: str, reference: str | None) -> dict:
    arms = [a for a in df["TRT01A"].dropna().unique() if a not in ("TBD", "")]
    if len(arms) < 2:
        return {
            "paramcd": paramcd,
            "param_label": param_label,
            "fitted": False,
            "skip_reason": "Single treatment arm — Cox PH requires ≥2 arms.",
        }
    n_events = int((df["CNSR"] == 0).sum())
    if n_events < 5:
        return {
            "paramcd": paramcd,
            "param_label": param_label,
            "fitted": False,
            "skip_reason": f"Too few events ({n_events}) — Cox PH requires ≥5.",
        }
    if reference is None or reference not in arms:
        reference = sorted(arms)[0]
    others = [a for a in sorted(arms) if a != reference]
    df_fit = df.copy()
    for arm in others:
        df_fit[f"TRT_{arm}"] = (df_fit["TRT01A"] == arm).astype(int)
    covariate_cols = [f"TRT_{arm}" for arm in others]
    try:
        from statsmodels.duration.hazard_regression import PHReg

        endog = df_fit["AVAL"].astype(float).to_numpy()
        status = (df_fit["CNSR"] == 0).astype(int).to_numpy()
        exog = df_fit[covariate_cols].astype(float).to_numpy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = PHReg(endog, exog, status=status, missing="drop")
            fit = model.fit()
        coefs = fit.params.tolist()
        hrs = np.exp(coefs).tolist()
        se = fit.bse.tolist()
        lo = np.exp(np.array(coefs) - 1.959963984540054 * np.array(se)).tolist()
        hi = np.exp(np.array(coefs) + 1.959963984540054 * np.array(se)).tolist()
        pvals = fit.pvalues.tolist()
    except Exception as e:
        return {
            "paramcd": paramcd,
            "param_label": param_label,
            "fitted": False,
            "skip_reason": f"Cox PH fit failed: {type(e).__name__}: {e}",
        }
    return {
        "paramcd": paramcd,
        "param_label": param_label,
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
            }
            for i, arm in enumerate(others)
        ],
    }


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    rows = payload.get("data", [])
    params = payload.get("params", {})
    paramcds_requested = params.get("paramcds") or []
    arms_param = params.get("arms") or []
    reference = arms_param[0] if arms_param else None
    label_map = params.get("label_map") or {}

    if not rows:
        (_OUTPUT_DIR / "trial-stats-tte.json").write_text(
            json.dumps({"params": [], "note": "No data rows."}, indent=2),
            encoding="utf-8",
        )
        return

    df = pd.DataFrame(rows)
    df["AVAL"] = pd.to_numeric(df["AVAL"], errors="coerce")
    df["CNSR"] = pd.to_numeric(df["CNSR"], errors="coerce").fillna(1).astype(int)
    df["TRT01A"] = df.get("TRT01A", pd.Series([""] * len(df))).fillna("")

    summaries: list[dict] = []
    available_paramcds = sorted(df["PARAMCD"].dropna().unique())
    targets = [p for p in available_paramcds if not paramcds_requested or p in paramcds_requested]

    for paramcd in targets:
        group = df[df["PARAMCD"] == paramcd].dropna(subset=["AVAL"])
        if group.empty:
            continue
        param_label = str(label_map.get(paramcd, paramcd))
        try:
            _km_plot_save(group, str(paramcd), param_label)
        except Exception as e:
            print(f"[kaplan_meier] plot for {paramcd} failed: {e!r}")
        km_pooled = _kaplan_meier(
            group["AVAL"].astype(float).to_numpy(),
            (group["CNSR"] == 0).astype(int).to_numpy(),
        )
        arms = sorted(a for a in group["TRT01A"].dropna().unique() if a not in ("TBD", ""))
        df_by_arm = {a: group[group["TRT01A"] == a] for a in arms}
        log_rank_p = _log_rank(df_by_arm) if len(arms) >= 2 else None
        cox = _cox_one(group, str(paramcd), param_label, reference=reference)
        summaries.append(
            {
                "paramcd": str(paramcd),
                "param_label": param_label,
                "n_subjects": int(len(group)),
                "n_events": int((group["CNSR"] == 0).sum()),
                "median_event_time": _km_median(km_pooled),
                "log_rank_p_value": log_rank_p,
                "km_filename": f"km-{str(paramcd).lower()}.png",
                "cox": cox,
            }
        )

    (_OUTPUT_DIR / "trial-stats-tte.json").write_text(
        json.dumps({"params": summaries}, indent=2), encoding="utf-8"
    )
    print(
        f"[kaplan_meier] wrote {len(summaries)} parameter summaries and "
        f"{sum(1 for _ in _OUTPUT_DIR.glob('km-*.png'))} K-M plots."
    )


if __name__ == "__main__":
    main()
