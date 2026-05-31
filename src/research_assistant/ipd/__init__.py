"""Individual patient data (IPD) meta-analysis subpackage.

The `sandbox_scripts/` directory holds three sandbox-executed scripts:

  • one_stage.py — single multilevel model pooling all subjects
                     (random trial intercept + treatment fixed effect).
                     Continuous: MixedLM. Binary: GLM Binomial / logit.
                     TTE: PHReg stratified by trial.
  • two_stage.py — fits per-trial models then DerSimonian-Laird pools
                     the per-trial estimates. The classical IPD
                     two-stage approach; transparent + auditable.
  • subgroup.py  — adds a treatment × subgroup interaction term to the
                     one-stage model + per-level pooled effects + the
                     Wald interaction p-value.

The shared `script_loader` reads them as text so the `run_ipd_analysis`
wrapper can forward them to `sandbox_exec`.
"""

from __future__ import annotations

from .script_loader import (
    AVAILABLE_IPD_SCRIPTS,
    IpdScriptKind,
    load_script,
)

__all__ = [
    "AVAILABLE_IPD_SCRIPTS",
    "IpdScriptKind",
    "load_script",
]
