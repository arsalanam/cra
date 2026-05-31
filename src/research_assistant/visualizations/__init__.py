"""Cross-specialist visualisation package.

Two artefact types live here:

  • Sandbox-side scripts that render matplotlib PNGs from operator-
    pasted JSON shapes (funnel / waterfall / swimmer). Loaded as text
    via `script_loader.load_script(kind)` and forwarded to
    `sandbox_exec` via the `run_visualisation` tool.

  • Host-side hand-rolled SVG generators that don't need the sandbox
    (GRADE chip table — like the PRISMA flow diagram, it's structured
    data → static SVG with no math required).

Each specialist that needs a viz registers the right wrapper tool and
calls into here from its turn-output post-processor.
"""

from __future__ import annotations

from .chip_table import build_grade_chip_svg
from .script_loader import (
    AVAILABLE_VISUALIZATIONS,
    VisualizationKind,
    load_script,
)

__all__ = [
    "AVAILABLE_VISUALIZATIONS",
    "VisualizationKind",
    "build_grade_chip_svg",
    "load_script",
]
