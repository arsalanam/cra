"""Swimmer plot — per-subject treatment timeline, sandbox-side.

Standard oncology visualisation: each row is one subject, horizontal
bar spans enrolment to last follow-up. Event markers (response onset,
progression, death, off-treatment) overlaid on the bar.

Reads ``/home/sandbox/input/data.json`` with the shape ::

    {
      "data": {
        "outcome_label": "Treatment timeline",
        "subjects": [
          {
            "usubjid": "S001",
            "treatment": "Drug A",
            "duration_days": 412,
            "ongoing": false,
            "events": [
              {"day": 56, "kind": "response_onset"},
              {"day": 240, "kind": "progression"},
              {"day": 412, "kind": "off_treatment"}
            ]
          },
          ...
        ],
        "treatments": ["Placebo", "Drug A"],
        "event_kinds": ["response_onset", "progression", "death", "off_treatment"]
      }
    }

Writes to ``/home/sandbox/output/``:
  • ``swimmer.png`` — per-subject horizontal-bar plot with event markers.
  • ``trial-stats-swimmer.json`` — per-subject summary + event counts.

Event-kind glyph mapping (consistent with most oncology lit):
  response_onset → green triangle right
  progression    → orange diamond
  death          → black ×
  off_treatment  → gray |
  cr             → dark-green star (complete response)
  pr             → green triangle right (partial response — same as
                    response_onset; treat as alias)
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


_EVENT_GLYPHS: dict[str, tuple[str, str]] = {
    # kind → (marker, colour)
    "response_onset": (">", "#2A9D8F"),
    "pr": (">", "#2A9D8F"),
    "cr": ("*", "#1E6E8C"),
    "progression": ("d", "#E76F51"),
    "death": ("x", "#000000"),
    "off_treatment": ("|", "#888888"),
}


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    outcome_label = str(data.get("outcome_label", "Treatment timeline"))
    subjects = data.get("subjects") or []
    treatments = data.get("treatments") or []
    if not subjects:
        (_OUTPUT_DIR / "trial-stats-swimmer.json").write_text(
            json.dumps(
                {
                    "outcome_label": outcome_label,
                    "fitted": False,
                    "skip_reason": "No subjects supplied.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    # Sort by treatment then by duration descending so the bars
    # cascade visually.
    cleaned: list[dict] = []
    for s in subjects:
        try:
            dur = float(s["duration_days"])
        except (KeyError, TypeError, ValueError):
            continue
        cleaned.append(
            {
                "usubjid": str(s.get("usubjid", "")),
                "treatment": str(s.get("treatment", "")),
                "duration_days": dur,
                "ongoing": bool(s.get("ongoing", False)),
                "events": list(s.get("events") or []),
            }
        )
    if not cleaned:
        (_OUTPUT_DIR / "trial-stats-swimmer.json").write_text(
            json.dumps(
                {
                    "outcome_label": outcome_label,
                    "fitted": False,
                    "skip_reason": "No subjects with numeric duration_days.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    if not treatments:
        treatments = sorted({r["treatment"] for r in cleaned if r["treatment"]})
    palette = ["#4682B4", "#E76F51", "#2A9D8F", "#264653", "#F4A261"]
    colour_for = {t: palette[i % len(palette)] for i, t in enumerate(treatments)}
    fallback_colour = "#888888"

    # Order: by treatment, then by duration descending.
    cleaned.sort(key=lambda r: (r["treatment"], -r["duration_days"]))

    n = len(cleaned)
    fig, ax = plt.subplots(
        figsize=(9, max(3.5, 0.18 * n + 1.5)), dpi=110
    )

    y_positions = np.arange(n)
    bar_colours = [colour_for.get(r["treatment"], fallback_colour) for r in cleaned]
    ax.barh(
        y_positions,
        [r["duration_days"] for r in cleaned],
        color=bar_colours,
        edgecolor="black",
        linewidth=0.2,
        alpha=0.75,
    )

    # Ongoing arrows: an open arrow at the right end of the bar.
    for i, r in enumerate(cleaned):
        if r["ongoing"]:
            ax.annotate(
                "",
                xy=(r["duration_days"] + 4, i),
                xytext=(r["duration_days"], i),
                arrowprops={"arrowstyle": "->", "color": "black"},
            )

    # Event markers.
    seen_kinds: set[str] = set()
    for i, r in enumerate(cleaned):
        for evt in r["events"]:
            kind = str(evt.get("kind", "")).lower()
            day = evt.get("day")
            if day is None:
                continue
            try:
                day_val = float(day)
            except (TypeError, ValueError):
                continue
            marker, colour = _EVENT_GLYPHS.get(kind, ("o", "#888888"))
            ax.scatter([day_val], [i], marker=marker, color=colour, s=46, zorder=3)
            seen_kinds.add(kind)

    ax.set_yticks(y_positions)
    ax.set_yticklabels([r["usubjid"] for r in cleaned], fontsize=7)
    ax.set_xlabel("Days from enrolment")
    ax.set_title(f"Swimmer — {outcome_label}")
    ax.set_ylim(-0.6, n - 0.4)
    ax.invert_yaxis()
    ax.grid(True, axis="x", alpha=0.3)

    # Legend: treatments + event glyphs that appeared.
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=colour_for[t], alpha=0.75) for t in treatments
    ]
    legend_labels: list[str] = list(treatments)
    for kind in (
        "response_onset",
        "cr",
        "progression",
        "death",
        "off_treatment",
    ):
        if kind in seen_kinds:
            marker, colour = _EVENT_GLYPHS[kind]
            handles.append(
                plt.Line2D(
                    [0],
                    [0],
                    marker=marker,
                    color="w",
                    markerfacecolor=colour,
                    markeredgecolor=colour,
                    markersize=8,
                    linewidth=0,
                )
            )
            legend_labels.append(kind.replace("_", " "))
    ax.legend(handles, legend_labels, loc="lower right", fontsize=8)
    fig.tight_layout()
    out = _OUTPUT_DIR / "swimmer.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)

    (_OUTPUT_DIR / "trial-stats-swimmer.json").write_text(
        json.dumps(
            {
                "outcome_label": outcome_label,
                "fitted": True,
                "n_subjects": len(cleaned),
                "filename": out.name,
                "treatments": treatments,
                "subjects": cleaned,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[swimmer] wrote {out.name} for {len(cleaned)} subjects.")


if __name__ == "__main__":
    main()
