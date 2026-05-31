"""Read packaged sandbox visualisation scripts as text.

Three sandbox-side scripts: funnel plot (publication bias detection
via Egger's test), waterfall plot (per-subject best response), and
swimmer plot (per-subject treatment timeline with response markers).
"""

from __future__ import annotations

from importlib import resources
from typing import Final, Literal

VisualizationKind = Literal["funnel", "waterfall", "swimmer"]


AVAILABLE_VISUALIZATIONS: Final[tuple[VisualizationKind, ...]] = (
    "funnel",
    "waterfall",
    "swimmer",
)


def load_script(kind: VisualizationKind) -> str:
    """Return the canonical Python script for `kind` as text."""
    if kind not in AVAILABLE_VISUALIZATIONS:
        raise FileNotFoundError(
            f"Unknown visualisation {kind!r}. Available: "
            f"{', '.join(AVAILABLE_VISUALIZATIONS)}."
        )
    script_path = resources.files(__package__).joinpath(
        "sandbox_scripts", f"{kind}.py"
    )
    return script_path.read_text(encoding="utf-8")


__all__ = ["AVAILABLE_VISUALIZATIONS", "VisualizationKind", "load_script"]
