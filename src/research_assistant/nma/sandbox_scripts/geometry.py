"""Network geometry — sandbox-side.

Renders a network-meta-analysis geometry diagram: nodes for each
intervention (radius ∝ √n_studies), edges for head-to-head pairs
(width ∝ n_trials). Circular layout, hand-rolled (no networkx
dependency — pinned only matplotlib + numpy in the sandbox image).

Reads ``/home/sandbox/input/data.json``::

    {
      "data": {
        "nodes": [
          {"intervention": "Drug A", "n_studies": 8, "n_participants": 1240},
          ...
        ],
        "edges": [
          {"source_intervention": "Drug A", "target_intervention": "Placebo",
           "n_trials": 3},
          ...
        ]
      }
    }

Writes ``/home/sandbox/output/nma-geometry.png``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")


def _circular_positions(n: int, radius: float = 1.0) -> dict[int, tuple[float, float]]:
    positions: dict[int, tuple[float, float]] = {}
    for i in range(n):
        angle = 2 * math.pi * i / n - math.pi / 2  # start at top
        positions[i] = (radius * math.cos(angle), radius * math.sin(angle))
    return positions


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    if not nodes:
        # Empty placeholder PNG so the host doesn't crash chasing the file.
        fig, ax = plt.subplots(figsize=(6, 4), dpi=110)
        ax.text(0.5, 0.5, "No network nodes.", ha="center", va="center")
        ax.axis("off")
        out = _OUTPUT_DIR / "nma-geometry.png"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        return

    n = len(nodes)
    positions = _circular_positions(n, radius=1.0)
    name_to_index = {node["intervention"]: i for i, node in enumerate(nodes)}

    fig, ax = plt.subplots(figsize=(7, 7), dpi=110)
    ax.set_xlim(-1.6, 1.6)
    ax.set_ylim(-1.6, 1.6)
    ax.set_aspect("equal")
    ax.axis("off")

    # Edges first, behind nodes.
    max_edge = max((e.get("n_trials") or 1 for e in edges), default=1)
    for edge in edges:
        a = name_to_index.get(edge.get("source_intervention"))
        b = name_to_index.get(edge.get("target_intervention"))
        if a is None or b is None:
            continue
        xa, ya = positions[a]
        xb, yb = positions[b]
        width = 0.6 + 3.0 * (edge.get("n_trials") or 1) / max_edge
        ax.plot([xa, xb], [ya, yb], color="#6c757d", linewidth=width, alpha=0.55, zorder=1)
        # Edge label — n_trials at midpoint.
        midx, midy = (xa + xb) / 2, (ya + yb) / 2
        ax.text(
            midx,
            midy,
            f"k={edge.get('n_trials')}",
            fontsize=8,
            color="#495057",
            ha="center",
            va="center",
            bbox={
                "boxstyle": "round,pad=0.18",
                "facecolor": "white",
                "edgecolor": "#dee2e6",
                "linewidth": 0.4,
            },
            zorder=2,
        )

    # Nodes
    max_n = max((node.get("n_studies") or 1 for node in nodes), default=1)
    for i, node in enumerate(nodes):
        x, y = positions[i]
        n_studies = node.get("n_studies") or 1
        radius = 0.07 + 0.10 * math.sqrt(n_studies / max_n)
        circle = plt.Circle(
            (x, y),
            radius,
            color="#4682B4",
            ec="#1E6E8C",
            linewidth=1.6,
            zorder=3,
        )
        ax.add_patch(circle)
        ax.text(
            x,
            y - radius - 0.07,
            node.get("intervention", ""),
            fontsize=11,
            fontweight="bold",
            ha="center",
            va="top",
            zorder=4,
        )
        ax.text(
            x,
            y,
            f"{n_studies}",
            fontsize=9,
            color="white",
            ha="center",
            va="center",
            fontweight="bold",
            zorder=5,
        )

    ax.set_title("Network geometry — node n=#studies, edge label=#head-to-head trials")
    fig.tight_layout()
    out = _OUTPUT_DIR / "nma-geometry.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"[nma_geometry] wrote {out.name} ({n} nodes, {len(edges)} edges)")


if __name__ == "__main__":
    main()


# Defensive: silence "imported but unused" for numpy when running under
# strict linters in the host venv (the file is excluded from mypy/ruff
# strict via the sandbox_scripts exclude, but defence in depth).
_ = np
