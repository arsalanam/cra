"""IRT — randomisation algorithms + schedule generation (eCRF E8).

This package is pure-Python and stateless: every function is
deterministic given its seed + inputs. The repository layer
(`persistence.clinical.repository`) wraps these with persistence +
the audit trail; the algorithms here have no DB awareness.

Module layout:

  • `algorithms.py` — the four algorithms (simple / permuted_block /
    stratified_permuted_block / pocock_simon_minimisation).
  • `schedule.py` — pre-generation helpers that produce the
    `sequence_json` payload the schedule stores at creation time.
"""

from __future__ import annotations

from .algorithms import (
    MinimisationState,
    StratumKey,
    canonical_stratum_label,
    generate_permuted_block,
    generate_simple,
    generate_stratified_permuted_block,
    pocock_simon_choose_arm,
)

__all__ = [
    "MinimisationState",
    "StratumKey",
    "canonical_stratum_label",
    "generate_permuted_block",
    "generate_simple",
    "generate_stratified_permuted_block",
    "pocock_simon_choose_arm",
]
