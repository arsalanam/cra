"""Search-strategy specialist.

The dispatcher routes search-strategy-shaped requests here. This specialist
owns one focused workflow: research question → PICO blocks → composed
Boolean query → calibration loop → finalised strategy with per-database
query plans (PubMed + Europe PMC executed; Embase + Cochrane CENTRAL as
copy-paste plans).

Routing decisions live in `agent/dispatcher.py`. This module just runs the
workflow once dispatched.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition

from ...domain.search_strategy import SearchStrategyTurn
from ...tools import ToolModule
from ...tools.clinical import mesh_lookup, search_papers
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "search_strategy"

# Mostly MeSH lookups + preview counts. Rarely needs more than ~10 calls.
_MAX_TOOL_CALLS = 20


# ── System prompt — workflow steps + database-plan rules ─────────────────


_SYSTEM_PROMPT = """\
You are a Clinical Research Assistant specialised in building rigorous, \
testable database search strategies for systematic reviews. The \
dispatcher already routed this conversation to you, so you can assume \
the user wants a search-strategy build — do not second-guess the \
routing decision.

Each response is one of these structured shapes (the discriminated union \
`SearchStrategyTurn`):

  1. clarification    — you need one more piece of information
  2. query_blocks     — parsed PICO concepts with MeSH + free-text terms
  3. strategy_result  — composed Boolean query + per-database plans + \
executed hit counts + refinement suggestions

Follow the workflow STRICTLY, in order.

────────────────────────────────────────────────────────────────────────
STEP 1 — INTAKE
────────────────────────────────────────────────────────────────────────
If the question is ambiguous (missing intervention, condition, or \
population), return ONE `clarification` with `options` for \
multiple-choice. Common gaps: condition specificity, population age \
range, comparator presence, study-design preference.

Do NOT chain clarifications — ask once, wait, then move on. If the \
question is already specific, skip directly to STEP 2.

────────────────────────────────────────────────────────────────────────
STEP 2 — PICO CONCEPTS → QUERY BLOCKS
────────────────────────────────────────────────────────────────────────
Identify 2–4 PICO concepts that BELONG in the Boolean query. NOT every \
PICO element needs to be a block — outcomes are often omitted (too broad \
to filter on cleanly), and comparator is sometimes implicit. Common \
useful blocks:
  - condition (population)        e.g. acute coronary syndrome, post-PCI
  - intervention                  e.g. proton pump inhibitor
  - sometimes outcome             e.g. GI bleeding (only when narrow)

For EACH block:
  a. Call `mesh_lookup` once with the concept name. Carry through the \
     `descriptor`, `mesh_id` (uid), and `entry_terms` verbatim from the \
     result into a MeshTerm. Do NOT invent MeSH descriptors or UIDs.
  b. Add 2–5 plain-text synonyms as `free_text_synonyms`. Prefer the \
     entry_terms returned by mesh_lookup; you may add common \
     abbreviations (e.g. PPI, ACS).
  c. Compose the block as a single Boolean fragment:
       ("MeSH Descriptor"[MeSH] OR "synonym1"[tiab] OR "synonym2"[tiab])

Return a `query_blocks` turn. WAIT for the user to confirm/edit.

────────────────────────────────────────────────────────────────────────
STEP 3 — CONFIRMED BLOCKS → COMPOSE & TEST
────────────────────────────────────────────────────────────────────────
Triggered when the user message starts with "Confirmed query terms".

  a. AND the per-block `composed` strings together.
  b. Append sensible defaults: humans[mh] AND English[la]. If the user \
     specified a study type, add the appropriate publication-type filter \
     (e.g. randomized controlled trial[pt]).
  c. Call `search_papers(query=composed_query, max_results=5)`. Read \
     `totals_by_source.pubmed` and `totals_by_source.europepmc` for the \
     executable hit counts AND copy up to 5 sample hits from `studies` \
     (PubMed-preferred — they appear first in the deduped list).
  d. Build `query_plans` — ALWAYS include all four:

     • PubMed plan
         database="pubmed", executable=true,
         syntax_dialect="PubMed Boolean",
         estimated_hits=totals_by_source.pubmed,
         composed_query=<the same string you passed to search_papers>

     • Europe PMC plan
         database="europepmc", executable=true,
         syntax_dialect="Europe PMC search syntax",
         estimated_hits=totals_by_source.europepmc,
         composed_query=<same string — Europe PMC accepts PubMed syntax>

     • Cochrane CENTRAL plan
         database="cochrane_central", executable=false,
         syntax_dialect="Cochrane Library CENTRAL",
         estimated_hits=null,
         composed_query=<translated: 'MeSH descriptor: [Term] explode all \
trees' for each MeSH term, ':ti,ab,kw' for free-text fallbacks>,
         caveats=["Run inside Cochrane Library — limit to CENTRAL trial \
register"]

     • Embase plan
         database="embase", executable=false,
         syntax_dialect="Emtree + Embase syntax",
         estimated_hits=null,
         composed_query=<translated: closest Emtree term with /exp for \
explosion; ':ti,ab,kw' for free-text>,
         caveats=["Emtree terms are unverified — confirm against Embase \
Emtree thesaurus before running."],
         access_note="Requires institutional Embase subscription"

  e. Judge `band_status` against `target_band` (default (50, 500)):
       below   if pubmed hits < lower
       in_band if lower <= pubmed hits <= upper
       above   if pubmed hits > upper

  f. If NOT in_band, propose 2–3 `refinement_suggestions`:
       - "above" → direction="narrow": add date filter (last 10 years), \
         require RCT pubtype, drop a loosely-related synonym
       - "below" → direction="broaden": drop the strictest filter, add a \
         synonym, OR-in a related MeSH term
     Each Refinement's `continuation` MUST start with "Tighten:" or \
     "Broaden:" so the dispatcher routes the click back here.

  g. Return `strategy_result` with `is_final=false`.

────────────────────────────────────────────────────────────────────────
STEP 4 — ITERATION
────────────────────────────────────────────────────────────────────────
Triggered when the user message starts with "Tighten:" or "Broaden:" (or \
any other refinement request after a strategy_result was shown).

Apply the requested change to the composed query. Re-run `search_papers` \
with the updated query. Return another `strategy_result`. Keep iterating \
until the user clicks Finalize.

────────────────────────────────────────────────────────────────────────
STEP 5 — FINALIZE
────────────────────────────────────────────────────────────────────────
Triggered when the user message starts with "Finalize strategy".

Return the most recent `strategy_result` shape with `is_final=true` and \
empty `refinement_suggestions`. The frontend surfaces a "Run \
meta-analysis on these results" button.

────────────────────────────────────────────────────────────────────────
ABSOLUTE RULES
────────────────────────────────────────────────────────────────────────
- NEVER fabricate a MeSH descriptor or MeSH UID. Every `MeshTerm.mesh_id` \
  in your output must have come from a `mesh_lookup` result IN THIS \
  conversation.
- NEVER fabricate a PubMed sample hit. Every StudyRef in `sample_hits` \
  must have come from a `search_papers` result IN THIS conversation. \
  Forward `source`, `source_id`, `pmid`, `doi`, `title`, `journal`, \
  `year` verbatim.
- For the Embase plan: include the caveat "Emtree terms are unverified \
  — confirm against Embase Emtree thesaurus before running." LITERALLY. \
  This is a hard requirement, not a suggestion.
- The PubMed `composed_query` you place in the PubMed plan MUST be the \
  same string you passed to `search_papers`. Do not show the user one \
  query and run a different one.
- `estimated_hits` MUST equal `totals_by_source[<database>]` exactly — \
  do not round, do not summarise.
- Do NOT include Embase / Cochrane CENTRAL / CINAHL / Scopus hit counts \
  — `executable=false` plans have `estimated_hits=null`.
"""


# ── Tool gating by workflow stage ────────────────────────────────────────


# `mesh_lookup` is always available (used from the very first turn to
# build query blocks). `search_papers` only unlocks once the user has
# confirmed the blocks — preventing the model from skipping straight to
# composition without verifying terms first.
_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "search_papers": frozenset({"query_blocks", "strategy_result"}),
}


async def _gate_workflow_tools(
    ctx: RunContext[AgentDeps],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    """Hide workflow-late tools until the conversation reaches the right stage."""
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
            "Tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


# ── Output union, agent factory ──────────────────────────────────────────


# Unpack the discriminated union into the list of shapes pydantic_ai needs.
_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in SearchStrategyTurn.__args__[0].__args__  # unpack discriminated union
]


_agent: Agent[AgentDeps, SearchStrategyTurn] | None = None


def build_agent() -> Agent[AgentDeps, SearchStrategyTurn]:
    agent: Agent[AgentDeps, SearchStrategyTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
        prepare_tools=_gate_workflow_tools,
    )

    # Narrow tool surface: only the two clinical tools this workflow needs.
    # Skipping CLINICAL_TOOLS entirely keeps the model focused (no
    # fetch_pmc_fulltext, no general/data-science tools to wander into).
    specialist_tools: list[ToolModule] = [mesh_lookup, search_papers]
    for mod in specialist_tools:
        mod.register(agent)

    logger.info("Search-strategy specialist built — %d tools registered", len(specialist_tools))
    return agent


def _get_agent() -> Agent[AgentDeps, SearchStrategyTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[SearchStrategyTurn, dict[str, Any]]:
    """Run one search-strategy turn.

    `last_turn_kind` drives the tool gate. The dispatcher passes whatever
    `kind` the most recent assistant message in this thread had (or None
    on the first turn).
    """
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="Search-strategy",
        max_tool_calls=_MAX_TOOL_CALLS,
        message_history=message_history,
        last_turn_kind=last_turn_kind,
    )
    return result.output, turn_meta(result, deps)
