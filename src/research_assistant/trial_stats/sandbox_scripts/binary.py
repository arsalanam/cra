"""Binary endpoint analysis — sandbox-side.

Runs inside the Docker sandbox. Reads ``/home/sandbox/input/data.json``::

    {
      "data": [
        {"USUBJID": "S001", "PARAMCD": "ORR", "AVALC": "RESPONDER",
         "TRT01A": "Drug A"},
        ...
      ],
      "params": {
        "paramcds": ["ORR"],
        "arms": ["Placebo", "Drug A"],     // first is reference
        "event_value": "RESPONDER",          // AVALC equal to this = event
        "method": "fisher_exact"             // or "log_binomial"
      }
    }

Writes to ``/home/sandbox/output/``:
  • ``trial-stats-binary.json`` — per-PARAMCD risk-difference / risk-ratio
    + 95% CI + p-value

`fisher_exact` uses scipy.stats; `log_binomial` uses
statsmodels.GLM with log link.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact, norm

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _two_by_two(
    df: pd.DataFrame,
    arms: list[str],
    event_value: str,
) -> dict[str, int] | None:
    """Build a 2×2 contingency for events_treatment vs events_comparator."""
    if len(arms) < 2:
        return None
    reference, treatment = arms[0], arms[1]
    df_t = df[df["TRT01A"] == treatment]
    df_c = df[df["TRT01A"] == reference]
    n_t = int(len(df_t))
    n_c = int(len(df_c))
    e_t = int((df_t["AVALC"] == event_value).sum())
    e_c = int((df_c["AVALC"] == event_value).sum())
    return {
        "n_treatment": n_t,
        "events_treatment": e_t,
        "n_comparator": n_c,
        "events_comparator": e_c,
    }


def _risk_difference_ci(e_t: int, n_t: int, e_c: int, n_c: int) -> tuple[float, float, float]:
    """Wald CI for the risk difference (Newcombe 1998 method 10)."""
    if n_t == 0 or n_c == 0:
        return (float("nan"), float("nan"), float("nan"))
    p_t = e_t / n_t
    p_c = e_c / n_c
    rd = p_t - p_c
    se = float(np.sqrt(p_t * (1 - p_t) / n_t + p_c * (1 - p_c) / n_c))
    z = float(norm.ppf(0.975))
    return rd, rd - z * se, rd + z * se


def _fisher_one(
    paramcd: str,
    counts: dict[str, int],
    arms: list[str],
) -> dict:
    e_t = counts["events_treatment"]
    n_t = counts["n_treatment"]
    e_c = counts["events_comparator"]
    n_c = counts["n_comparator"]
    if n_t == 0 or n_c == 0:
        return {
            "paramcd": paramcd,
            "fitted": False,
            "skip_reason": "Zero subjects in one arm.",
        }
    rd, rd_lo, rd_hi = _risk_difference_ci(e_t, n_t, e_c, n_c)
    odds_table = [[e_t, n_t - e_t], [e_c, n_c - e_c]]
    odds, p_value = fisher_exact(odds_table, alternative="two-sided")
    p_t = e_t / n_t
    p_c = e_c / n_c
    rr = float("nan")
    rr_lo = float("nan")
    rr_hi = float("nan")
    if p_c > 0 and p_t > 0:
        rr = p_t / p_c
        # Log-RR CI (Katz 1978)
        log_rr = float(np.log(rr))
        log_rr_se = float(np.sqrt(1 / e_t - 1 / n_t + 1 / e_c - 1 / n_c))
        z = float(norm.ppf(0.975))
        rr_lo = float(np.exp(log_rr - z * log_rr_se))
        rr_hi = float(np.exp(log_rr + z * log_rr_se))
    return {
        "paramcd": paramcd,
        "fitted": True,
        "method": "fisher_exact",
        "comparison": f"{arms[1]} vs {arms[0]}",
        "n_treatment": n_t,
        "events_treatment": e_t,
        "n_comparator": n_c,
        "events_comparator": e_c,
        "risk_difference": float(rd),
        "rd_ci_lower": float(rd_lo),
        "rd_ci_upper": float(rd_hi),
        "risk_ratio": float(rr) if not np.isnan(rr) else None,
        "rr_ci_lower": float(rr_lo) if not np.isnan(rr_lo) else None,
        "rr_ci_upper": float(rr_hi) if not np.isnan(rr_hi) else None,
        "odds_ratio": float(odds),
        "p_value": float(p_value),
    }


def _log_binomial_one(
    paramcd: str,
    counts: dict[str, int],
    arms: list[str],
) -> dict:
    """Risk ratio from a log-binomial GLM. Falls back to Fisher when the
    fit fails to converge (frequent with rare events)."""
    e_t = counts["events_treatment"]
    n_t = counts["n_treatment"]
    e_c = counts["events_comparator"]
    n_c = counts["n_comparator"]
    if n_t == 0 or n_c == 0:
        return _fisher_one(paramcd, counts, arms) | {
            "method": "fisher_exact",
            "skip_reason": "Log-binomial fallback to Fisher: zero arm.",
        }
    y = np.concatenate([np.ones(n_t), np.ones(n_c)])
    y[:n_t] = np.concatenate([np.ones(e_t), np.zeros(n_t - e_t)])
    y[n_t:] = np.concatenate([np.ones(e_c), np.zeros(n_c - e_c)])
    trt = np.concatenate([np.ones(n_t), np.zeros(n_c)])
    intercept = np.ones_like(trt)
    exog = np.column_stack([intercept, trt])
    try:
        from statsmodels.genmod.families import Binomial, links
        from statsmodels.genmod.generalized_linear_model import GLM

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = GLM(
                y,
                exog,
                family=Binomial(link=links.Log()),
            )
            fit = model.fit()
        coef = float(fit.params[1])
        se = float(fit.bse[1])
        rr = float(np.exp(coef))
        z = float(norm.ppf(0.975))
        rr_lo = float(np.exp(coef - z * se))
        rr_hi = float(np.exp(coef + z * se))
        p_value = float(fit.pvalues[1])
    except Exception as e:
        # Common: log-binomial fails to converge with rare or all-event arms.
        return _fisher_one(paramcd, counts, arms) | {
            "method": "fisher_exact",
            "skip_reason": f"Log-binomial fallback to Fisher: {type(e).__name__}.",
        }
    rd, rd_lo, rd_hi = _risk_difference_ci(e_t, n_t, e_c, n_c)
    return {
        "paramcd": paramcd,
        "fitted": True,
        "method": "log_binomial",
        "comparison": f"{arms[1]} vs {arms[0]}",
        "n_treatment": n_t,
        "events_treatment": e_t,
        "n_comparator": n_c,
        "events_comparator": e_c,
        "risk_difference": float(rd),
        "rd_ci_lower": float(rd_lo),
        "rd_ci_upper": float(rd_hi),
        "risk_ratio": rr,
        "rr_ci_lower": rr_lo,
        "rr_ci_upper": rr_hi,
        "p_value": p_value,
    }


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    rows = payload.get("data", [])
    params = payload.get("params", {})
    paramcds_requested = params.get("paramcds") or []
    arms_param = params.get("arms") or []
    event_value = params.get("event_value", "RESPONDER")
    method = params.get("method", "fisher_exact")

    if not rows:
        (_OUTPUT_DIR / "trial-stats-binary.json").write_text(
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
        counts = _two_by_two(sub, arms_param, str(event_value))
        if counts is None:
            summaries.append(
                {
                    "paramcd": paramcd,
                    "fitted": False,
                    "skip_reason": "Fewer than 2 arms supplied.",
                }
            )
            continue
        if method == "log_binomial":
            summaries.append(_log_binomial_one(str(paramcd), counts, arms_param))
        else:
            summaries.append(_fisher_one(str(paramcd), counts, arms_param))

    (_OUTPUT_DIR / "trial-stats-binary.json").write_text(
        json.dumps({"params": summaries}, indent=2), encoding="utf-8"
    )
    print(f"[binary] wrote {len(summaries)} parameter summaries.")


if __name__ == "__main__":
    main()
