"""Waterfall plot — per-subject best response, sandbox-side.

Standard oncology visualisation: each bar is one subject, height = best
percentage change from baseline (tumour volume / lesion size / etc),
sorted descending (largest reductions on the left).

Reads ``/home/sandbox/input/data.json`` with the shape ::

    {
      "data": {
        "outcome_label": "Best change in target lesion (%)",
        "subjects": [
          {"usubjid": "S001", "best_change_pct": -42.1, "treatment": "Drug A"},
          ...
        ],
        "treatments": ["Placebo", "Drug A"]   // colour order
      }
    }

Writes to ``/home/sandbox/output/``:
  • ``waterfall.png`` — coloured-by-treatment waterfall plot.
  • ``trial-stats-waterfall.json`` — per-subject ordered roster +
    response category counts (CR/PR/SD/PD per RECIST 1.1 thresholds:
    PR ≤ -30%, PD ≥ +20%).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")

_RECIST_PR = -30.0
_RECIST_PD = 20.0


def _recist_category(value: float) -> str:
    """RECIST 1.1 categorisation by % change in target lesions.

    True CR (-100%) is rare and usually requires confirmation; the
    grouping below mirrors the standard "responder" definition
    (PR + CR) for waterfall plots.
    """
    if value <= -100.0:
        return "CR"
    if value <= _RECIST_PR:
        return "PR"
    if value >= _RECIST_PD:
        return "PD"
    return "SD"


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    outcome_label = str(data.get("outcome_label", "Best change from baseline (%)"))
    subjects = data.get("subjects") or []
    treatments = data.get("treatments") or []

    if not subjects:
        (_OUTPUT_DIR / "trial-stats-waterfall.json").write_text(
            json.dumps(
                {
                    "outcome_label": outcome_label,
                    "fitted": False,
                    "skip_reason": "No subjects supplied.",
                    "n_subjects": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    # Sort descending by best_change_pct so largest reductions are on the left.
    cleaned = []
    for s in subjects:
        try:
            v = float(s["best_change_pct"])
        except (KeyError, TypeError, ValueError):
            continue
        cleaned.append(
            {
                "usubjid": str(s.get("usubjid", "")),
                "best_change_pct": v,
                "treatment": str(s.get("treatment", "")),
            }
        )
    cleaned.sort(key=lambda r: r["best_change_pct"])  # most negative first
    if not cleaned:
        (_OUTPUT_DIR / "trial-stats-waterfall.json").write_text(
            json.dumps(
                {
                    "outcome_label": outcome_label,
                    "fitted": False,
                    "skip_reason": "No subjects with numeric best_change_pct.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    # Treatment colour map. Reference (first) = steelblue, rest cycle.
    palette = ["#4682B4", "#E76F51", "#2A9D8F", "#264653", "#F4A261"]
    if not treatments:
        treatments = sorted({r["treatment"] for r in cleaned if r["treatment"]})
    colour_for: dict[str, str] = {t: palette[i % len(palette)] for i, t in enumerate(treatments)}
    fallback = "#888888"

    values = np.array([r["best_change_pct"] for r in cleaned])
    colours = [colour_for.get(r["treatment"], fallback) for r in cleaned]

    fig, ax = plt.subplots(figsize=(max(7, len(values) * 0.12 + 4), 5), dpi=110)
    ax.bar(range(len(values)), values, color=colours, edgecolor="black", linewidth=0.3)
    ax.axhline(_RECIST_PR, linestyle="--", color="#2A9D8F", linewidth=1, label="−30% (PR)")
    ax.axhline(_RECIST_PD, linestyle="--", color="#E76F51", linewidth=1, label="+20% (PD)")
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_xlabel("Subjects (sorted by best change)")
    ax.set_ylabel(outcome_label)
    ax.set_title(f"Waterfall — {outcome_label}")
    ax.set_xticks([])
    ax.set_ylim(min(values.min() - 5, -100), max(values.max() + 5, 100))

    # Legend
    handles = [plt.Rectangle((0, 0), 1, 1, color=colour_for[t]) for t in treatments]
    handles += [
        plt.Line2D([0], [0], color="#2A9D8F", linestyle="--", linewidth=1),
        plt.Line2D([0], [0], color="#E76F51", linestyle="--", linewidth=1),
    ]
    legend_labels = list(treatments) + ["−30% (PR threshold)", "+20% (PD threshold)"]
    ax.legend(handles, legend_labels, loc="upper right", fontsize=8)
    fig.tight_layout()
    out = _OUTPUT_DIR / "waterfall.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)

    counts: dict[str, int] = {"CR": 0, "PR": 0, "SD": 0, "PD": 0}
    for r in cleaned:
        counts[_recist_category(r["best_change_pct"])] += 1

    (_OUTPUT_DIR / "trial-stats-waterfall.json").write_text(
        json.dumps(
            {
                "outcome_label": outcome_label,
                "fitted": True,
                "n_subjects": len(cleaned),
                "filename": out.name,
                "subjects": cleaned,
                "response_counts": counts,
                "treatments": treatments,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[waterfall] wrote {out.name} for {len(cleaned)} subjects.")


if __name__ == "__main__":
    main()
