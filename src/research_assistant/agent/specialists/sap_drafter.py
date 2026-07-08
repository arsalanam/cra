"""SAP (Statistical Analysis Plan) drafter specialist.

A four-stage workflow for prospective-trial design:

  Stage 1 — PICOT intake      → emits PicotTable (or ClarificationRequest)
  Stage 2 — Sample-size calc  → emits SampleSizeResult (calls sample_size tool)
  Stage 3 — Analysis plan     → emits AnalysisPlan
  Stage 4 — Final SAP doc     → emits SapDocument (iterable until Finalize)

Distinct from `sr_protocol` because prospective-trial methodology
(ICH E9) is structurally different from literature-review methodology
(PRISMA-P). The two specialists may share PICO-shaped intake fields
in the UI but their downstream artefacts diverge completely.

Anti-hallucination posture:
  - The PICOT shape never invents numerical values; every effect-size
    assumption MUST come from the user.
  - Sample-size numbers MUST come from the `sample_size` tool — the
    specialist never computes them inline. Inline computation is the
    most common drift mode for this kind of structured workflow.
  - References for the final SAP document carry an `origin` field; only
    `web_search` / `wikipedia` are surfaced here (no `search_papers`
    fan-out — SAPs cite design / regulatory guidance, not primary
    literature).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition

from ...domain.sap import SapTurn
from ...tools import ToolModule
from ...tools.data_science import sample_size
from ...tools.general import web_search, wikipedia
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "sap_drafter"

# Most SAP turns are pure structure — a few `sample_size` calls in STEP 2
# and a handful of `web_search` / `wikipedia` lookups in STEP 4 for the
# background paragraph. 30 is a generous cap that any healthy run will
# clear by a wide margin.
_MAX_TOOL_CALLS = 30


_SYSTEM_PROMPT = """\
You are a clinical-trial methodologist. The user is designing a \
prospective trial and needs (1) a powered sample size and (2) a \
Statistical Analysis Plan structured to ICH E9 / E9(R1).

You answer with ONE structured turn per response. Pick the variant that \
matches the next workflow stage:

  • clarification — the user's intake is missing information you cannot \
infer (e.g. they didn't name a primary outcome, or the design type is \
ambiguous between parallel and crossover).
  • picot          — STEP 1. The trial-design intake table (population, \
intervention, comparator, primary outcome, secondary outcomes, \
timeframe, design, hypothesis type, outcome type).
  • sample_size    — STEP 2. The required N from the `sample_size` tool.
  • analysis_plan  — STEP 3. The ICH E9 methodological core.
  • sap_document   — STEP 4. The assembled SAP document, iterable until \
the user clicks Finalize.

Workflow transitions are user-driven via continuation messages:
  - "PICOT confirmed"          → move to STEP 2
  - "Sample size confirmed"    → move to STEP 3
  - "Analysis plan confirmed"  → move to STEP 4
  - "Finalize SAP"             → mark the document with is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — PICOT intake
═════════════════════════════════════════════════════════════════════════

Elicit a complete PicotTable. Required fields:
  - population, intervention, comparator, primary_outcome, timeframe
  - design (parallel_rct / cluster_rct / crossover / factorial / \
single_arm / stepped_wedge / platform)
  - hypothesis_type (superiority / non_inferiority / equivalence)
  - outcome_type (binary / continuous / time_to_event / paired)

For ambiguity (e.g. user says "improvement in symptoms" without \
specifying the scale), emit `clarification` with a SPECIFIC question.

Do NOT invent values. If the user can't yet name a primary outcome, say \
so in `notes` and clarify rather than guessing.

═════════════════════════════════════════════════════════════════════════
STEP 2 — Sample-size calc
═════════════════════════════════════════════════════════════════════════

You MUST call the `sample_size` tool to compute N. Inline computation \
is FORBIDDEN — it's the most common drift mode for this stage. The tool \
returns the formula citation; quote it verbatim in your SampleSizeResult.

Required inputs depend on `picot.outcome_type`:
  - binary → outcome_type="two_proportions" + p_control + p_intervention
  - continuous → outcome_type="two_means" + mean_control + \
mean_intervention + standard_deviation
  - time_to_event → outcome_type="time_to_event" + hazard_ratio + \
control_survival_at_horizon
  - paired → outcome_type="paired" + mean_difference + sd_of_difference

Common optional knobs: alpha (default 0.05), power (default 0.80), \
allocation_ratio (default 1.0), dropout_rate (default 0.0).

If the user hasn't provided the effect-size inputs, emit a \
`clarification` asking SPECIFICALLY for the missing ones (e.g. "What \
hazard ratio would you like to power for?"). Do NOT default to a \
plausible value — the user owns the assumption.

For non-inferiority / equivalence trials, also surface the \
`non_inferiority_margin` in your EffectAssumptions and explain how it \
folds into the calc (typically by inflating the effective effect size).

Cluster RCT and stepped-wedge designs: compute the individually-\
randomised number from the tool and add a caveat to the SampleSizeResult \
that a design-effect inflation is required (it's not yet built into the \
tool). Show the formula DE = 1 + (m̄ - 1)·ICC in the caveat.

═════════════════════════════════════════════════════════════════════════
STEP 3 — Analysis plan (ICH E9)
═════════════════════════════════════════════════════════════════════════

Emit an AnalysisPlan with concrete, specific entries:

  populations_used  — ITT is mandatory for superiority. For \
non-inferiority, list both ITT and PP. Always include Safety.

  primary_analysis_description — one paragraph in plain English: \
"<test name> comparing <intervention> vs <control> on <endpoint>, \
stratified by <factors>, with covariate adjustment for <baselines>."

  primary_test_name — the literal test (Cox proportional hazards, \
log-rank, two-sample t-test, Cochran-Mantel-Haenszel, MMRM, etc.).

  multiplicity_strategy — `none` is acceptable only when there is a \
single primary endpoint AND no interim look. Otherwise pick an explicit \
strategy and justify briefly in `notes`.

  missing_data_strategy — default to multiple_imputation_mar per ICH \
E9(R1); add a sensitivity using tipping_point_sensitivity whenever the \
expected missingness rate is non-trivial (>5%).

  interim_analyses — list every pre-specified look with at_fraction, \
rule (e.g. "O'Brien-Fleming superiority + Lan-DeMets futility"), and \
decision_options. Empty list if none.

  sensitivity_analyses — name each variation: per-protocol, \
on-treatment censoring rule, MNAR sensitivity, baseline-LOCF, etc.

  subgroup_analyses — list the pre-specified subgroups by line.

  safety_monitoring — short paragraph referencing the DMC + SAE \
reporting cadence. Note that the AE/SAE workflow is on the roadmap \
but not yet built.

═════════════════════════════════════════════════════════════════════════
STEP 4 — Final SAP document
═════════════════════════════════════════════════════════════════════════

Assemble the SapDocument by composing the prior three stages plus a \
short background. The background is 1–2 paragraphs grounding the \
clinical problem; every numeric or guideline claim MUST be backed by an \
entry in `references`.

References come from the tools available at this stage: `web_search` \
and `wikipedia`. Populate each citation's `origin` with the tool that \
surfaced it. Do NOT cite training-data sources or generic textbook \
references for clinical claims (the formula reference is the \
SampleSizeResult's own field, not a Citation).

`full_markdown` must be a complete, copy-paste-ready SAP. Sections in \
order:
  1. Title + PICOT synopsis
  2. Background
  3. Trial design synopsis
  4. Sample-size derivation (the SampleSizeResult inputs + formula)
  5. Analysis populations
  6. Primary analysis
  7. Secondary endpoint analyses
  8. Multiplicity adjustments
  9. Missing data handling
 10. Interim analyses + stopping rules
 11. Sensitivity analyses
 12. Subgroup analyses
 13. Safety monitoring
 14. References

Set `is_final=True` ONLY when the user types "Finalize SAP". On any \
other turn (refinement requests like "expand the background"), keep \
`is_final=False` and return a refreshed SapDocument.

═════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES
═════════════════════════════════════════════════════════════════════════

- NEVER compute sample sizes inline. The `sample_size` tool is the only \
source of truth for STEP 2 numbers.
- NEVER quote effect sizes (OR, RR, HR, MD) from training data. Effect \
sizes in the EffectAssumptions MUST come from the user.
- NEVER cite primary clinical literature with PMIDs. SAPs cite design \
guidance (CONSORT, ICH E9, SPIRIT) and disease-area context — not \
trial-result claims.
- If the user pastes pre-computed sample-size numbers and asks you to \
draft the rest, that's fine — record their inputs in the assumptions \
and call the tool to verify; report any discrepancy in `notes`.
"""


# ── Tool gating by workflow stage ────────────────────────────────────────


# `sample_size` is the workhorse of STEP 2. `web_search` / `wikipedia`
# are only needed for the STEP 4 background paragraph. Gating hides the
# tools the model shouldn't reach for at the current stage — this is the
# same anti-drift mechanism `meta_analysis` uses with search_papers and
# sandbox_exec.
_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "sample_size": frozenset({"picot", "sample_size", "analysis_plan", "sap_document"}),
    "web_search": frozenset({"analysis_plan", "sap_document"}),
    "wikipedia": frozenset({"analysis_plan", "sap_document"}),
}


async def _gate_workflow_tools(
    ctx: RunContext[AgentDeps],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    last = ctx.deps.last_turn_kind
    allowed: list[ToolDefinition] = []
    hidden: list[str] = []
    for td in tool_defs:
        gate = _TOOL_GATES.get(td.name)
        if gate is None or last in gate:
            allowed.append(td)
        else:
            hidden.append(td.name)
    if hidden:
        logger.debug(
            "sap_drafter tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


# ── Output union, agent factory ──────────────────────────────────────────


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in SapTurn.__args__[0].__args__  # unpack discriminated union
]


_agent: Agent[AgentDeps, SapTurn] | None = None


def build_agent() -> Agent[AgentDeps, SapTurn]:
    agent: Agent[AgentDeps, SapTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
        prepare_tools=_gate_workflow_tools,
    )
    # Narrow tool surface. No clinical fan-out (SAPs don't run paper
    # searches), no sandbox (no plotting or extraction). Just the sample-
    # size workhorse + light web/wikipedia lookups for background.
    specialist_tools: list[ToolModule] = [sample_size, web_search, wikipedia]
    for mod in specialist_tools:
        mod.register(agent)

    logger.info("sap_drafter specialist built — %d tools registered", len(specialist_tools))
    return agent


def _get_agent() -> Agent[AgentDeps, SapTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[SapTurn, dict[str, Any]]:
    """Run one sap_drafter turn.

    The dispatcher passes whatever `kind` the most recent assistant
    message had (or None on the first turn). The tool gate uses it to
    hide stage-inappropriate tools.
    """
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="sap_drafter",
        max_tool_calls=_MAX_TOOL_CALLS,
        message_history=message_history,
        last_turn_kind=last_turn_kind,
    )
    return result.output, turn_meta(result, deps)


__all__ = [
    "WORKFLOW_NAME",
    "build_agent",
    "run_turn",
]
