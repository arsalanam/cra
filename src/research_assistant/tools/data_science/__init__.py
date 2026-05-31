"""Data-science tools — calculator, Python REPL, Docker sandbox.

Available to specialists that do quantitative work (meta-analysis runs
sandbox_exec for forest plots; future research-gap may use it for
citation-network analysis).
"""

from . import (
    calculator,
    nma_analysis,
    python_repl,
    sample_size,
    sandbox_exec,
    trial_analysis,
    visualisations,
)

__all__ = [
    "calculator",
    "nma_analysis",
    "python_repl",
    "sample_size",
    "sandbox_exec",
    "trial_analysis",
    "visualisations",
]
