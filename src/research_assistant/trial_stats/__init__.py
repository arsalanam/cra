"""Trial-specific statistical analysis subpackage.

The `sandbox_scripts/` package data holds standalone Python programs
that the `trial_stats` specialist drives via `sandbox_exec`:

  • kaplan_meier.py    — K-M curves + log-rank + Cox PH HR
  • mmrm.py            — Mixed-Models for Repeated Measures
  • binary.py          — Fisher's exact + log-binomial
  • subgroup_forest.py — per-subgroup HR + interaction p

Each script reads `/home/sandbox/input/data.json` with the bundled
shape ``{"data": [...rows...], "params": {...}}`` and writes JSON
results + PNG artefacts under `/home/sandbox/output/`.

The shared `script_loader` helper reads them as text so the wrapper
tool can forward them to `sandbox_exec(code=..., input_data=...)`.
"""

from __future__ import annotations

from .script_loader import (
    AVAILABLE_ANALYSES,
    AnalysisKind,
    load_script,
)

__all__ = [
    "AVAILABLE_ANALYSES",
    "AnalysisKind",
    "load_script",
]
