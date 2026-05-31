"""Read packaged sandbox scripts as text.

The 4 sandbox-side scripts live alongside this module so they're
shipped as package data. The trial_analysis tool (under
`tools/data_science/`) calls `load_script(kind)` to fetch the text
and forwards it to `sandbox_exec`.
"""

from __future__ import annotations

from importlib import resources
from typing import Final, Literal

AnalysisKind = Literal["kaplan_meier", "mmrm", "binary", "subgroup_forest"]


AVAILABLE_ANALYSES: Final[tuple[AnalysisKind, ...]] = (
    "kaplan_meier",
    "mmrm",
    "binary",
    "subgroup_forest",
)


def load_script(kind: AnalysisKind) -> str:
    """Return the canonical Python script for `kind` as text.

    Raises FileNotFoundError if `kind` is not one of the bundled
    scripts — the tool wrapper validates upfront so this is purely
    defensive.
    """
    if kind not in AVAILABLE_ANALYSES:
        raise FileNotFoundError(
            f"Unknown trial-stats analysis {kind!r}. Available: {', '.join(AVAILABLE_ANALYSES)}."
        )
    script_path = resources.files(__package__).joinpath("sandbox_scripts", f"{kind}.py")
    return script_path.read_text(encoding="utf-8")


__all__ = ["AVAILABLE_ANALYSES", "AnalysisKind", "load_script"]
