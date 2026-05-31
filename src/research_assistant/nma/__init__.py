"""Network meta-analysis subpackage.

The `sandbox_scripts/` directory holds three sandbox-executed scripts:

  • frequentist.py — mvmeta-style + electrical-network analogy.
                       Uses statsmodels + numpy only (already pinned).
  • bayesian.py    — PyMC MCMC. Graceful skip when PyMC is missing
                       from the sandbox image; documented as a
                       deploy-time gate.
  • geometry.py    — network-plot PNG via matplotlib (hand-rolled
                       circular layout; node radius ∝ √n_studies,
                       edge width ∝ n_head_to_head_trials).

The shared `script_loader` reads them as text so the `run_nma_analysis`
wrapper can forward them to `sandbox_exec`.
"""

from __future__ import annotations

from .script_loader import (
    AVAILABLE_NMA_SCRIPTS,
    NmaScriptKind,
    load_script,
)

__all__ = [
    "AVAILABLE_NMA_SCRIPTS",
    "NmaScriptKind",
    "load_script",
]
