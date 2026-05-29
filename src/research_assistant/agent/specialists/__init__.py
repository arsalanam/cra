"""Workflow specialists.

Each specialist owns:
  • a focused system prompt
  • a discriminated-union output type (its own subset of turn shapes)
  • a tool subset (composed from `tools.GENERAL_TOOLS`, `CLINICAL_TOOLS`,
    `DATA_SCIENCE_TOOLS`)
  • per-stage tool gating where the workflow is multi-step

The dispatcher picks one specialist per user turn and calls
`specialist.run_turn(user_message, message_history, last_turn_kind)`.

Adding a new specialist:
  1. Create a new module here exporting `WORKFLOW_NAME = "..."` and `run_turn(...)`
  2. Define its own output union in `domain/<workflow>.py`
  3. Add a clause to `agent/dispatcher.py::classify`
  4. Add card components to the frontend
"""

from . import (
    general_qa,
    meta_analysis,
    risk_of_bias,
    sap_drafter,
    search_strategy,
    sr_protocol,
)

# Map workflow id → specialist module. The dispatcher uses this.
SPECIALISTS = {
    meta_analysis.WORKFLOW_NAME: meta_analysis,
    search_strategy.WORKFLOW_NAME: search_strategy,
    sr_protocol.WORKFLOW_NAME: sr_protocol,
    risk_of_bias.WORKFLOW_NAME: risk_of_bias,
    sap_drafter.WORKFLOW_NAME: sap_drafter,
    general_qa.WORKFLOW_NAME: general_qa,
}

__all__ = [
    "SPECIALISTS",
    "general_qa",
    "meta_analysis",
    "risk_of_bias",
    "sap_drafter",
    "search_strategy",
    "sr_protocol",
]
