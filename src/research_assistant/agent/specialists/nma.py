"""Network meta-analysis specialist.

Five-stage workflow for indirect comparisons across ≥3 interventions:

  Stage 1 — nma_intake            → multi-arm clinical question
  Stage 2 — nma_pico               → PicoNetwork (≥3 interventions)
  Stage 3 — nma_search_results     → studies with per-arm coverage
  Stage 4 — nma_data_extraction    → per-study × per-arm data
  Stage 5 — nma_results            → league table + SUCRA + network +
                                       interpretation

Distinct from pairwise meta_analysis because NMA pools direct +
indirect evidence under a consistency assumption that needs explicit
methodological treatment.

Anti-hallucination posture (same as meta_analysis + tighter):
  - PMIDs come from `search_papers` tool calls only.
  - Pooled effects + CIs + SUCRA + network counts come from
    `run_nma_analysis` sandbox runs only — NEVER authored inline.
  - Transitivity assumption surfaced in `pico.rationale`; consistency
    caveats surfaced in `nma_results.caveats`.
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
from ...domain.nma import NmaTurn
from ...tools import ToolModule
from ...tools.clinical import fetch_pmc_fulltext, search_papers
from ...tools.data_science import nma_analysis, sandbox_exec
from ...tools.general import web_search, wikipedia
from ..deps import AgentDeps, drain_tool_usage
from ..model import build_bedrock_model

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "nma"

_MAX_TOOL_CALLS = 40


_SYSTEM_PROMPT = """\
You are a clinical-research biostatistician specialised in network \
meta-analysis (NMA). The user wants to compare ≥3 interventions for the \
same condition + outcome using both direct (head-to-head) and indirect \
evidence. NMA is methodologically distinct from pairwise meta-analysis: \
it pools the entire network under the assumption that direct + indirect \
evidence converge (transitivity / consistency).

You answer with ONE structured turn per response. Pick the variant that \
matches the next workflow stage:

  • clarification          — the user's intake is missing or ambiguous.
  • nma_intake             — STEP 1. Multi-arm clinical question.
  • nma_pico               — STEP 2. PicoNetwork with ≥3 interventions + \
                              transitivity rationale.
  • nma_search_results     — STEP 3. Studies retrieved from \
                              `search_papers`; each carries \
                              `arms_evaluated`.
  • nma_data_extraction    — STEP 4. Per-study × per-arm n + events \
                              (binary) or mean + sd (continuous).
  • nma_results            — STEP 5. League table + SUCRA + network + \
                              interpretation, produced by the sandbox.

Workflow transitions are user-driven via continuation messages:
  - "NMA PICO confirmed"        → STEP 3 (search)
  - "NMA studies selected"      → STEP 4 (extraction)
  - "NMA extraction confirmed"  → STEP 5 (run analysis)
  - "Run Bayesian NMA"          → STEP 5 with backend='bayesian'
  - "Refine NMA: <directive>"   → return a refreshed nma_results
  - "Finalize NMA"              → STEP 5 with is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — Intake
═════════════════════════════════════════════════════════════════════════

Elicit a one-sentence research question mentioning ≥3 interventions. If \
the user named only 2 interventions, emit `clarification` asking for the \
additional comparators (NMA below 3 arms is just pairwise + adds no \
value).

═════════════════════════════════════════════════════════════════════════
STEP 2 — PicoNetwork
═════════════════════════════════════════════════════════════════════════

Emit a `nma_pico` turn with:
  - population, outcome, effect_measure (OR / RR / MD / SMD / HR)
  - interventions list (≥3; first entry is the reference comparator)
  - inclusion/exclusion criteria
  - study_types (PubMed publication-type filter)

The `rationale` field MUST address:
  (a) transitivity — why the trials comparing different pairs are \
      sufficiently similar in population, setting, and outcome \
      definition that indirect comparisons through common comparators \
      are credible;
  (b) reference choice — why the first intervention is the natural \
      anchor (typically placebo or standard of care).

═════════════════════════════════════════════════════════════════════════
STEP 3 — Search
═════════════════════════════════════════════════════════════════════════

Build a PubMed query covering the disease + outcome + ALL named \
interventions (use OR across intervention names). Call \
`search_papers` (max_results 30). For each retrieved study, set \
`arms_evaluated` to the subset of `PicoNetwork.interventions` that \
study covers — single-arm studies don't enter NMA. Always populate \
`relevance_note`.

═════════════════════════════════════════════════════════════════════════
STEP 4 — Per-arm extraction
═════════════════════════════════════════════════════════════════════════

For each selected study, extract one `NmaStudyExtractedData` carrying \
ALL the arms_evaluated rows. Binary (OR/RR/HR): set `events` + `n` per \
arm. Continuous (MD/SMD): set `mean` + `sd` + `n`. Mark `is_complete: \
false` when the abstract doesn't yield the numbers and let the user \
paste from the full PDF.

═════════════════════════════════════════════════════════════════════════
STEP 5 — NMA analysis
═════════════════════════════════════════════════════════════════════════

Triggered when the user sends "NMA extraction confirmed" + the JSON \
payload of confirmed extracted data (or "Run Bayesian NMA" for the \
opt-in posterior backend).

  a. Build the canonical `data_payload`:
       {"data": {
         "studies": [...complete StudyExtractedData rows only],
         "interventions": [...PicoNetwork.interventions],
         "effect_measure": "OR"  // or matching measure
       }}
  b. Call `run_nma_analysis(backend="frequentist", data_payload=...)`. \
     The sandbox writes `nma-league-frequentist.json` you parse to \
     populate `league_table`, `sucra`, and `network.nodes/edges`.
  c. Call `run_nma_analysis(backend="geometry", data_payload=...)` with \
     the `nodes + edges` shape from step (b). The sandbox writes \
     `nma-geometry.png`; populate `network.image_url` with the \
     returned `/images/...` URL.
  d. If the user requested Bayesian (continuation prefix "Run Bayesian \
     NMA"), additionally call `run_nma_analysis(backend="bayesian", \
     data_payload=...)`. Set `backend="bayesian"` on `nma_results`. \
     When the sandbox reports a skip (PyMC missing), surface the \
     `skip_reason` in `caveats` and fall back to the frequentist \
     results.
  e. Populate `interpretation` with a 2-3 sentence plain-language \
     summary: which intervention has the highest SUCRA, the relative \
     effects of the top 2 vs reference, and the transitivity caveat \
     verbatim from the PICO rationale.
  f. Populate `caveats` honestly: high heterogeneity, sparse network \
     (low n_direct_trials in some edges), inconsistency concerns, \
     methodological reservations.

═════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES
═════════════════════════════════════════════════════════════════════════

- NEVER cite a study (PMID, source_id, title) that did NOT come from a \
`search_papers` tool call this conversation.
- NEVER fabricate league-table effect estimates, CIs, SUCRA values, or \
network counts — they come from `run_nma_analysis` sandbox runs only.
- NEVER omit the transitivity rationale in `nma_pico.rationale`. \
Without transitivity the indirect comparisons are uninterpretable.
- NEVER claim Bayesian results when the sandbox returned a \
`skip_reason`. Surface the skip in `caveats` and use the frequentist \
backend.
- When extraction is incomplete (`is_complete: false`), DROP the study \
from the NMA pass and document the drop in `studies_excluded`.
"""


_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "search_papers": frozenset(
        {"nma_pico", "nma_search_results", "nma_data_extraction", "nma_results"}
    ),
    "fetch_pmc_fulltext": frozenset(
        {"nma_search_results", "nma_data_extraction", "nma_results"}
    ),
    "run_nma_analysis": frozenset(
        {"nma_data_extraction", "nma_results"}
    ),
    "sandbox_exec": frozenset({"nma_data_extraction", "nma_results"}),
    "web_search": frozenset({"nma_results"}),
    "wikipedia": frozenset({"nma_results"}),
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
            "nma tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in NmaTurn.__args__[0].__args__  # discriminated union
]


_agent: Agent[AgentDeps, NmaTurn] | None = None


def build_agent() -> Agent[AgentDeps, NmaTurn]:
    agent: Agent[AgentDeps, NmaTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
        prepare_tools=_gate_workflow_tools,
    )
    specialist_tools: list[ToolModule] = [
        search_papers,
        fetch_pmc_fulltext,
        nma_analysis,
        sandbox_exec,
        web_search,
        wikipedia,
    ]
    for mod in specialist_tools:
        mod.register(agent)
    logger.info("nma specialist built — %d tools registered", len(specialist_tools))
    return agent


def _get_agent() -> Agent[AgentDeps, NmaTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[NmaTurn, dict[str, Any]]:
    settings = get_settings()
    agent = _get_agent()
    deps = AgentDeps(last_turn_kind=last_turn_kind)
    usage_limits = UsageLimits(
        request_limit=settings.max_model_requests,
        tool_calls_limit=_MAX_TOOL_CALLS,
    )
    history = list(message_history) if message_history else None
    logger.info(
        "nma turn: msg=%r history=%d stage=%r",
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
