"""IPD (individual patient data) meta-analysis specialist.

Five-stage workflow:

  Stage 1 — ipd_intake             → research question + endpoint
  Stage 2 — ipd_bundle              → per-trial CSV + column mapping
  Stage 3 — ipd_main_results        → one-stage + two-stage pooled effects
  Stage 4 — ipd_subgroup_results    → treatment × subgroup interaction
  Stage 5 — ipd_document            → assembled artefact, iterable

Anti-hallucination posture mirrors nma + meta_analysis:
  - Per-trial estimates + pooled effects + I² + τ² + interaction p come
    from `run_ipd_analysis` sandbox runs only.
  - Trial ids in `studies_included` MUST appear in the bundle.
  - When the sandbox skips (small trial / missing column), surface the
    `skip_reason` in `caveats` and drop the trial — don't fabricate.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits

from ...config import get_settings
from ...domain.ipd import IpdTurn
from ...tools import ToolModule
from ...tools.data_science import ipd_analysis, sandbox_exec
from ...tools.general import web_search, wikipedia
from ..deps import AgentDeps, drain_tool_usage
from ..model import build_bedrock_model

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "ipd"

_MAX_TOOL_CALLS = 30


_SYSTEM_PROMPT = """\
You are a clinical-research biostatistician specialised in individual \
patient data (IPD) meta-analysis. The user has subject-level data from \
≥2 trials and wants to pool it — methodologically richer than aggregate \
MA because it lets you test treatment × subgroup interactions, run \
one-stage multilevel models, and check the two-stage estimate against \
the one-stage estimate as a misspecification diagnostic.

You answer with ONE structured turn per response:

  • clarification          — the user's intake or bundle is incomplete.
  • ipd_intake             — STEP 1. Research question + endpoint + \
                              effect_measure.
  • ipd_bundle             — STEP 2. Per-trial CSV + column mapping.
  • ipd_main_results       — STEP 3. One-stage + two-stage pooled \
                              effects + per-trial estimates.
  • ipd_subgroup_results   — STEP 4 (optional). Treatment × subgroup \
                              interaction.
  • ipd_document           — STEP 5. Assembled artefact, iterable.

Workflow transitions via continuation messages:
  - "IPD intake confirmed"             → STEP 2
  - "IPD bundle confirmed"             → STEP 3
  - "IPD main results confirmed"       → STEP 4 (if subgroup wanted) or STEP 5
  - "IPD subgroup confirmed"           → STEP 5
  - "Add IPD subgroup: <variable>"     → STEP 4 with the named variable
  - "Refine IPD: <directive>"          → return refreshed document
  - "Finalize IPD"                     → STEP 5 with is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — Intake
═════════════════════════════════════════════════════════════════════════

Elicit an IpdIntake:
  - research_question (one sentence; must mention IPD MA + endpoint)
  - primary_endpoint
  - effect_measure (OR / RR / MD / SMD / HR)

If the user has only aggregate data (per-trial summary), emit \
`clarification` explaining that IPD MA needs subject-level rows from \
each trial — they should use the pairwise `meta_analysis` workflow \
instead.

═════════════════════════════════════════════════════════════════════════
STEP 2 — Bundle
═════════════════════════════════════════════════════════════════════════

Build an IpdBundleTurn with ≥2 IpdTrialEntry rows. Each carries:
  - trial_id (sponsor / PMID / canonical identifier)
  - n_subjects
  - treatment_column (name of the column with arm assignment)
  - treatment_active_value (the value marking the active arm)
  - outcome_column (binary 0/1 for OR/RR; numeric for MD/SMD; \
                     time-to-event for HR)
  - event_column (only for HR; the 0/1 censoring indicator)
  - covariate_columns (optional adjusters + future subgroup candidates)
  - rows_csv (the per-subject CSV with header, ≥10 chars)

Column names need NOT be identical across trials — the mapping lets \
each trial use its native names. But the same effect-measure-implied \
shape is required.

═════════════════════════════════════════════════════════════════════════
STEP 3 — Main results (one-stage + two-stage)
═════════════════════════════════════════════════════════════════════════

Triggered when the user sends "IPD bundle confirmed".

  a. Call `run_ipd_analysis(stage="one_stage", data_payload=...)` \
     with the bundle JSON. The sandbox writes `ipd-one-stage.json` you \
     parse to populate `one_stage` + `per_trial`.
  b. Call `run_ipd_analysis(stage="two_stage", data_payload=...)`. \
     The sandbox writes `ipd-two-stage.json` — populate `two_stage`.
  c. Compute the discrepancy: |one_stage.effect − two_stage.effect|. \
     For binary measures (log-scale), >0.2 in log-OR ≈ noticeable; for \
     continuous, >0.1 × SD ≈ noticeable. Surface in `discrepancy_note`.
  d. Always run BOTH stages — the one-stage vs two-stage comparison IS \
     the methodological check. If one fails (small trials, sparse \
     events), surface its skip_reason in the `discrepancy_note` and \
     report the other; do NOT fabricate the missing one.

═════════════════════════════════════════════════════════════════════════
STEP 4 — Subgroup × treatment interaction (optional)
═════════════════════════════════════════════════════════════════════════

Triggered when the user says "Add IPD subgroup: <variable>" or "IPD \
main results confirmed" + a subgroup name in the prior turn.

  a. Add `subgroup_variable` to the data payload.
  b. Call `run_ipd_analysis(stage="subgroup", data_payload=...)`. \
     The sandbox writes `ipd-subgroup.json`.
  c. Populate one IpdSubgroupResults per subgroup variable. \
     `interaction_p_value < 0.10` (by convention) signals \
     effect-modification — note in the document `caveats`.

═════════════════════════════════════════════════════════════════════════
STEP 5 — Assembled document
═════════════════════════════════════════════════════════════════════════

Emit ONE IpdDocument bundling intake + main_results + subgroup_results \
+ interpretation + caveats. Set is_final=True ONLY when user types \
"Finalize IPD".

═════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES
═════════════════════════════════════════════════════════════════════════

- NEVER fabricate per-trial estimates, pooled effects, CIs, I², τ², or \
interaction p-values. EVERY number comes from a `run_ipd_analysis` \
sandbox run.
- NEVER include a trial in `studies_included` that wasn't in the \
bundle.
- When the sandbox skips a trial (small n, missing column), DROP it \
from the analysis and document in `studies_excluded`.
- The one-stage vs two-stage discrepancy is the methodological check — \
report both even when they agree closely (the agreement IS the \
diagnostic).
- The subgroup interaction p comes from the sandbox's joint Wald p on \
the interaction terms; do NOT compute it inline.
- For binary measures with only fixed-effects logistic available, \
note the approximation in the document's `caveats`. Strict random- \
effects logistic requires a PyMC backend (deferred).
"""


_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "run_ipd_analysis": frozenset(
        {
            "ipd_bundle",
            "ipd_main_results",
            "ipd_subgroup_results",
            "ipd_document",
        }
    ),
    "sandbox_exec": frozenset(
        {"ipd_main_results", "ipd_subgroup_results", "ipd_document"}
    ),
    "web_search": frozenset({"ipd_document"}),
    "wikipedia": frozenset({"ipd_document"}),
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
            "ipd tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


_OUTPUT_TYPES: list[type] = [
    type_ for type_ in IpdTurn.__args__[0].__args__  # discriminated union
]


_agent: Agent[AgentDeps, IpdTurn] | None = None


def build_agent() -> Agent[AgentDeps, IpdTurn]:
    agent: Agent[AgentDeps, IpdTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
        prepare_tools=_gate_workflow_tools,
    )
    specialist_tools: list[ToolModule] = [
        ipd_analysis,
        sandbox_exec,
        web_search,
        wikipedia,
    ]
    for mod in specialist_tools:
        mod.register(agent)
    logger.info("ipd specialist built — %d tools registered", len(specialist_tools))
    return agent


def _get_agent() -> Agent[AgentDeps, IpdTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[IpdTurn, dict[str, Any]]:
    settings = get_settings()
    agent = _get_agent()
    deps = AgentDeps(last_turn_kind=last_turn_kind)
    usage_limits = UsageLimits(
        request_limit=settings.max_model_requests,
        tool_calls_limit=_MAX_TOOL_CALLS,
    )
    history = list(message_history) if message_history else None
    logger.info(
        "ipd turn: msg=%r history=%d stage=%r",
        user_message[:80],
        len(history) if history else 0,
        last_turn_kind,
    )
    result = await asyncio.wait_for(
        agent.run(
            user_message,
            deps=deps,
            usage_limits=usage_limits,
            message_history=history,
        ),
        timeout=settings.agent_timeout_seconds,
    )
    usage = result.usage()
    meta: dict[str, Any] = {
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "requests": usage.requests,
            "tool_calls": usage.tool_calls,
        },
        "tool_usage": drain_tool_usage(deps),
    }
    return result.output, meta


__all__ = ["WORKFLOW_NAME", "build_agent", "run_turn"]
