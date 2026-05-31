"""Funnel plot + Egger's test — sandbox-side.

Publication-bias diagnostic for a meta-analysis. Reads
``/home/sandbox/input/data.json`` with the shape ::

    {
      "data": {
        "outcome_label": "All-cause mortality",
        "effect_measure": "OR",              // "OR" / "RR" / "MD" / "SMD"
        "studies": [
          {"label": "PMID 12345", "effect": 0.72, "se": 0.18},
          ...
        ]
      }
    }

For binary effect measures (OR / RR), `effect` is the point estimate on
the natural scale; the funnel is plotted on the log scale. For
continuous measures (MD / SMD), the funnel is on the original scale.

Writes to ``/home/sandbox/output/``:
  • ``funnel-<slug>.png`` — funnel plot with pseudo-CI envelope.
  • ``trial-stats-funnel.json`` — Egger's regression intercept p-value
    + interpretation. Egger's test is a regression of the standardised
    effect on its precision (1/SE); a non-zero intercept signals
    small-study effects.

References:
  - Egger M, Smith GD, Schneider M, Minder C. Bias in meta-analysis
    detected by a simple, graphical test. BMJ 1997;315:629-634.
  - Sterne JAC, et al. Recommendations for examining and interpreting
    funnel plot asymmetry in meta-analyses of randomised controlled
    trials. BMJ 2011;343:d4002.
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _eggers_test(effects: np.ndarray, ses: np.ndarray) -> dict:
    """Egger's regression test for small-study effects.

    Fits standardised_effect = (effect / SE) on precision = (1 / SE).
    Tests intercept against zero — a non-zero intercept signals
    funnel asymmetry.
    """
    if len(effects) < 3:
        return {
            "fitted": False,
            "skip_reason": "Egger's test requires ≥3 studies.",
        }
    try:
        import statsmodels.api as sm

        y = effects / ses
        x = sm.add_constant(1.0 / ses)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = sm.OLS(y, x)
            fit = model.fit()
        intercept = float(fit.params[0])
        intercept_p = float(fit.pvalues[0])
        intercept_se = float(fit.bse[0])
        return {
            "fitted": True,
            "intercept": intercept,
            "intercept_se": intercept_se,
            "intercept_p_value": intercept_p,
            "slope": float(fit.params[1]),
            "n_studies": int(len(effects)),
        }
    except Exception as e:
        return {
            "fitted": False,
            "skip_reason": f"Egger's fit failed: {type(e).__name__}: {e}",
        }


def _interpret(eggers: dict, effect_measure: str) -> str:
    """One-line interpretation."""
    if not eggers.get("fitted"):
        return eggers.get("skip_reason", "Inconclusive.")
    p = eggers["intercept_p_value"]
    n = eggers["n_studies"]
    asymmetry = "asymmetry" if p < 0.10 else "symmetric"
    return (
        f"Egger's intercept p={p:.3f} across {n} studies — "
        f"funnel is {asymmetry}. "
        f"Effect measure: {effect_measure}."
    )


def _slug(label: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in label.lower())
    return safe.strip("-")[:48] or "outcome"


def _plot_funnel(
    log_scale: bool,
    effects: np.ndarray,
    ses: np.ndarray,
    labels: list[str],
    outcome_label: str,
    out_path: Path,
) -> None:
    pooled_est = float(np.average(effects, weights=1.0 / ses**2))
    fig, ax = plt.subplots(figsize=(7, 5.5), dpi=110)
    ax.scatter(effects, ses, s=30, color="steelblue", alpha=0.7)
    for x, y, lbl in zip(effects, ses, labels, strict=False):
        ax.text(x, y, f" {lbl}", fontsize=7, va="center")
    ax.axvline(
        pooled_est,
        color="black",
        linestyle="--",
        linewidth=1,
        label="Pooled effect",
    )
    # Pseudo 95% CI envelope.
    max_se = float(np.max(ses) * 1.05)
    se_grid = np.linspace(0, max_se, 50)
    z = 1.959963984540054
    upper = pooled_est + z * se_grid
    lower = pooled_est - z * se_grid
    ax.plot(upper, se_grid, color="gray", linestyle=":", linewidth=1)
    ax.plot(lower, se_grid, color="gray", linestyle=":", linewidth=1)
    ax.invert_yaxis()
    if log_scale:
        ax.set_xscale("symlog", linthresh=0.1)
        ax.set_xlabel("Effect (log scale)")
    else:
        ax.set_xlabel("Effect")
    ax.set_ylabel("Standard error")
    ax.set_title(f"Funnel plot — {outcome_label}")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    outcome_label = str(data.get("outcome_label", "outcome"))
    effect_measure = str(data.get("effect_measure", "OR"))
    studies = data.get("studies") or []
    if len(studies) < 3:
        (_OUTPUT_DIR / "trial-stats-funnel.json").write_text(
            json.dumps(
                {
                    "outcome_label": outcome_label,
                    "effect_measure": effect_measure,
                    "fitted": False,
                    "skip_reason": "Funnel + Egger's require ≥3 studies.",
                    "n_studies": len(studies),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    effects_raw = np.array([float(s["effect"]) for s in studies])
    ses = np.array([float(s["se"]) for s in studies])
    labels = [str(s.get("label", f"study-{i}")) for i, s in enumerate(studies)]

    log_scale = effect_measure in ("OR", "RR", "HR")
    if log_scale:
        effects = np.log(effects_raw)
        plot_effects = effects_raw
    else:
        effects = effects_raw
        plot_effects = effects_raw

    eggers = _eggers_test(effects, ses)
    interpretation = _interpret(eggers, effect_measure)

    out_path = _OUTPUT_DIR / f"funnel-{_slug(outcome_label)}.png"
    try:
        _plot_funnel(log_scale, plot_effects, ses, labels, outcome_label, out_path)
    except Exception as e:
        print(f"[funnel] plot failed: {e!r}")
        # Re-raise so the wrapper sees the error
        raise

    (_OUTPUT_DIR / "trial-stats-funnel.json").write_text(
        json.dumps(
            {
                "outcome_label": outcome_label,
                "effect_measure": effect_measure,
                "funnel_filename": out_path.name,
                "n_studies": len(studies),
                "eggers": eggers,
                "interpretation": interpretation,
                "pooled_effect_log": (
                    float(np.average(effects, weights=1.0 / ses**2))
                    if log_scale
                    else float(np.average(effects, weights=1.0 / ses**2))
                ),
            },
            indent=2,
            default=lambda x: None if isinstance(x, float) and math.isnan(x) else x,
        ),
        encoding="utf-8",
    )
    print(f"[funnel] wrote {out_path.name} + Egger's summary.")


if __name__ == "__main__":
    main()
