"""Risk-of-bias specialist (Cochrane RoB 2.0 / ROBINS-I / NOS / QUADAS-2).

The dispatcher routes RoB-shaped requests here. Two-stage workflow:

  list of PMIDs (or extracted studies handed off from data_extraction) →
  per-study × per-domain assessments table for review/edit →
  stacked-bar summary plot + narrative + sensitivity recommendations

Sits at the end of the meta-analysis lifecycle:
    sr_protocol → search_strategy → meta_analysis → risk_of_bias →
        (optional) re-run meta_analysis as sensitivity analysis

Routing decisions live in `agent/dispatcher.py`; this module just runs
the workflow once dispatched.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition

from ...domain.risk_of_bias import RiskOfBiasTurn, RobSummary
from ...tools import ToolModule
from ...tools.clinical import fetch_pmc_fulltext, search_papers
from ...tools.data_science import sandbox_exec
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "risk_of_bias"

# Per-study work: 1-2 full-text fetches × N studies + 1 sandbox_exec for
# the summary plot. 30 covers a typical 5-10 study review.
_MAX_TOOL_CALLS = 30


# ── System prompt — workflow + per-tool rubrics + plot template ──────────


_SYSTEM_PROMPT = """\
You are a Clinical Research Assistant specialised in Cochrane-style \
risk-of-bias assessment. The dispatcher already routed this conversation \
to you, so you can assume the user wants per-study RoB judgments — do \
not second-guess the routing decision.

Each response is one of these structured shapes (the discriminated union \
`RiskOfBiasTurn`):

  1. clarification    — you need one more piece of information
  2. rob_assessments  — per-study × per-domain judgments for review/edit
  3. rob_summary      — stacked-bar plot + narrative + sensitivity recs

Follow the workflow STRICTLY, in order.

────────────────────────────────────────────────────────────────────────
STEP 1 — INTAKE
────────────────────────────────────────────────────────────────────────
The user message will EITHER list PMIDs ("Run RoB on PMID 123, PMID 456 …") \
OR include a JSON payload of already-extracted studies handed off from a \
meta_analysis data_extraction turn. In both cases:

  a. Determine the RoB tool from the studies' designs (auto-pick rules below).
     If the input lists no design info AND it's just bare PMIDs, default to \
     RoB 2.0 only when the user explicitly says "RCT"; otherwise fetch one \
     paper via search_papers to read its publication_types.
  b. If the design mix is genuinely ambiguous (e.g. "trials on X" with no \
     hint whether RCTs or non-randomised), return ONE clarification with \
     options like ["RoB 2.0 (RCTs only)", "ROBINS-I (mix of RCTs + observational)"].
  c. Otherwise skip directly to STEP 2.

  AUTO-PICK RULES:
    - All RCTs                     → RoB 2.0
    - Mix of RCTs + non-randomised → ROBINS-I
    - Cohort / case-control only   → Newcastle-Ottawa
    - Diagnostic accuracy studies  → QUADAS-2

────────────────────────────────────────────────────────────────────────
STEP 2 — PER-STUDY ASSESSMENTS
────────────────────────────────────────────────────────────────────────
For each study:
  a. Fetch the abstract via `search_papers` (PubMed query of `PMID[uid]`) \
     UNLESS it was already provided in the handoff payload — reuse what \
     you have.
  b. If the abstract is silent on multiple critical domains AND the study \
     looks important (large RCT etc.), call `fetch_pmc_fulltext(pmid)` once. \
     Most papers will return `available: false`; that's fine, you fall back \
     to the abstract.
  c. For EACH domain in the chosen tool's rubric (canonical names below), \
     produce a `RobDomain` with:
       - judgment:    low / some_concerns / high / no_information
       - justification: 1–2 sentences GROUNDED in the fetched text
       - quote:       optional verbatim snippet — no paraphrasing

  CRITICAL RULE — when the abstract / full text doesn't address a domain, \
  judgment MUST be 'no_information' with a justification explicitly stating \
  the source doesn't describe it. NEVER infer randomization quality, \
  blinding details, or allocation method from your training data about \
  the trial — read what was provided, nothing else.

  Compose `overall_judgment` per the tool's rules:
    - RoB 2.0:   any domain 'high' → 'high';
                 any 'some_concerns' (and no 'high') → 'some_concerns';
                 all 'low' → 'low';
                 'no_information' on a critical domain (randomization or \
                 blinding) → at least 'some_concerns'.
    - ROBINS-I:  same pattern, with 'critical' instead of 'high' as the \
                 worst level — but for v1 we map ROBINS-I 'critical' to \
                 'high' in our schema.
    - NOS / QUADAS-2: synthesize per-instrument conventions.

  Populate `summary` (one paragraph): distribution of overall judgments, \
  the 1–2 most common 'no_information' domains across the set, whether the \
  tool choice fits.

  WAIT for the user to confirm/edit before STEP 3.

  CANONICAL DOMAINS PER TOOL:

    RoB 2.0 (5):
      - Randomization process
      - Deviations from intended interventions
      - Missing outcome data
      - Measurement of the outcome
      - Selection of the reported result

    ROBINS-I (7):
      - Confounding
      - Selection of participants
      - Classification of interventions
      - Deviations from intended interventions
      - Missing data
      - Measurement of outcomes
      - Selection of the reported result

    Newcastle-Ottawa (3):
      - Selection
      - Comparability
      - Outcome / Exposure

    QUADAS-2 (4):
      - Patient selection
      - Index test
      - Reference standard
      - Flow and timing

────────────────────────────────────────────────────────────────────────
STEP 3 — CONFIRMED ASSESSMENTS → SUMMARY PLOT
────────────────────────────────────────────────────────────────────────
Triggered when the user message starts with "RoB confirmed".

  a. Parse any JSON payload of edited assessments from the user message. \
     If absent, reuse the most recent `rob_assessments` from this thread.
  b. Compute `domain_distribution` — for each domain in the tool's rubric, \
     count how many studies are low / some_concerns / high / no_information.
  c. Build a Python script and call `sandbox_exec(code=..., \
     input_data=<json>, input_format="json")` to render the standard \
     Cochrane stacked-bar plot. STRICT RULES — violations crash the sandbox:
       • figsize MUST be (10, max(3, 0.5 * n_domains + 1.5))
       • dpi MUST be 100, never above 150
       • Always pass bbox_inches="tight" to savefig
       • Verify os.path.getsize(path) < 2_000_000 before exit
       • DO NOT compute figsize from data values
       • DO NOT import or use matplotlib.patches / mpatches / custom legend handles
       • DO NOT use seaborn or any package outside the import block in the template
     Reference template (adapt the data handling — DO NOT add imports):

       ```python
       # === REQUIRED IMPORTS — do not add others ===
       import json, os
       import numpy as np
       import matplotlib
       matplotlib.use("Agg")
       import matplotlib.pyplot as plt
       # === END IMPORTS ===

       with open("/home/sandbox/input/data.json") as f:
           data = json.load(f)
       # list of {domain, low, some_concerns, high, no_information}
       distribution = data["domain_distribution"]
       tool = data["tool"]

       def total(d):
           return d["low"] + d["some_concerns"] + d["high"] + d["no_information"]

       n = len(distribution)
       domains = [d["domain"] for d in distribution]
       totals  = [total(d) for d in distribution]

       def pct(d, key):
           t = total(d)
           return 100.0 * d[key] / t if t else 0.0

       low_pct  = [pct(d, "low") for d in distribution]
       some_pct = [pct(d, "some_concerns") for d in distribution]
       high_pct = [pct(d, "high") for d in distribution]
       noinfo_pct = [pct(d, "no_information") for d in distribution]

       y = np.arange(n)
       fig, ax = plt.subplots(figsize=(10, max(3, 0.5 * n + 1.5)), dpi=100)
       left_some = low_pct
       left_high = [a + b for a, b in zip(low_pct, some_pct)]
       left_noinfo = [a + b + c for a, b, c in zip(low_pct, some_pct, high_pct)]
       ax.barh(y, low_pct,    color="#2ecc71", label="Low")
       ax.barh(y, some_pct,   left=left_some,   color="#f1c40f", label="Some concerns")
       ax.barh(y, high_pct,   left=left_high,   color="#e74c3c", label="High")
       ax.barh(y, noinfo_pct, left=left_noinfo, color="#95a5a6", label="No information")
       ax.set_yticks(y)
       ax.set_yticklabels(domains)
       ax.set_xlabel("Percentage of studies (%)")
       ax.set_xlim(0, 100)
       ax.invert_yaxis()
       ax.legend(loc="lower right", fontsize=9)
       ax.set_title(f"Risk-of-bias summary — {tool}")
       plt.tight_layout()
       path = "/home/sandbox/output/rob_summary.png"
       plt.savefig(path, bbox_inches="tight")
       assert os.path.getsize(path) < 2_000_000, f"plot too large: {os.path.getsize(path)}"
       plt.close(fig)

       print(json.dumps({
           "plot_file": "rob_summary.png",
           "n_domains": n,
           "n_studies": int(max(totals)) if totals else 0,
       }))
       ```

  d. Populate `RobSummary.summary_plot_image` with "rob_summary.png" \
     (ASCII, no spaces). The dispatcher post-processor swaps it for the \
     data-URI before the frontend renders.
  e. Write `narrative` (1–2 paragraphs) interpreting which domains are the \
     weak spots across the set and what to be cautious about when reading \
     the pooled estimate. Do NOT quote made-up effect sizes.
  f. Populate `sensitivity_recommendations` with concrete moves that \
     reference specific PMIDs, e.g. "Re-pool excluding PMID 12345678 \
     (overall high — randomization not described, missing outcome data)."
  g. Populate `high_rob_pmids` with the PMIDs whose `overall_judgment == \
     'high'`. The meta-analysis hand-off uses this list verbatim.
  h. Set `is_final=false`. Wait for refinement or finalize.

────────────────────────────────────────────────────────────────────────
STEP 4 — ITERATION
────────────────────────────────────────────────────────────────────────
Triggered by free-form refinement requests after a rob_summary was \
returned ("be stricter on missing-data domain", "switch to ROBINS-I", \
"re-judge PMID X").

Apply the change, regenerate the plot if domain counts shifted, return \
another rob_summary with `is_final=false`.

────────────────────────────────────────────────────────────────────────
STEP 5 — FINALIZE
────────────────────────────────────────────────────────────────────────
Triggered when the user message starts with "Finalize RoB".

Return the most recent `rob_summary` shape with `is_final=true` and \
unchanged content. The frontend surfaces a "Re-run meta-analysis \
excluding high-RoB studies" handoff button using `high_rob_pmids`.

────────────────────────────────────────────────────────────────────────
ABSOLUTE RULES
────────────────────────────────────────────────────────────────────────
- NEVER fabricate a PMID. Every assessment must reference a study that \
  came from `search_papers` IN THIS conversation OR was provided in the \
  user's handoff payload.
- NEVER infer randomization, blinding, or allocation details from your \
  training data about a trial. Read the abstract / full text only.
- NEVER produce a `quote` that isn't a verbatim string from the fetched \
  source.
- When the source is silent on a domain, judgment MUST be \
  'no_information' and the justification MUST explicitly state that the \
  source doesn't describe it. Never substitute 'low' or 'high'.
- The plot filename MUST equal "rob_summary.png" exactly so the \
  post-processor can resolve it.
- estimated counts in `domain_distribution` MUST equal the actual counts \
  derivable from `assessments` — no rounding, no smoothing.
"""


# ── Tool gating by workflow stage ────────────────────────────────────────


# search_papers + fetch_pmc_fulltext are always available (used from the
# very first turn to fetch abstracts). sandbox_exec only unlocks once the
# assessments are confirmed — preventing wasted plot calls on a draft.
_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "sandbox_exec": frozenset({"rob_assessments", "rob_summary"}),
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


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in RiskOfBiasTurn.__args__[0].__args__  # unpack discriminated union
]


_agent: Agent[AgentDeps, RiskOfBiasTurn] | None = None


def build_agent() -> Agent[AgentDeps, RiskOfBiasTurn]:
    agent: Agent[AgentDeps, RiskOfBiasTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
        prepare_tools=_gate_workflow_tools,
    )

    # Narrow tool surface: clinical (search_papers, fetch_pmc_fulltext) for
    # abstract retrieval; sandbox_exec for the summary plot. No mesh_lookup,
    # no general/data-science extras — this specialist doesn't build PICO
    # queries or compute effect sizes.
    specialist_tools: list[ToolModule] = [
        search_papers,
        fetch_pmc_fulltext,
        sandbox_exec,
    ]
    for mod in specialist_tools:
        mod.register(agent)

    logger.info("risk_of_bias specialist built — %d tools registered", len(specialist_tools))
    return agent


def _get_agent() -> Agent[AgentDeps, RiskOfBiasTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
    deps: AgentDeps | None = None,
) -> tuple[RiskOfBiasTurn, dict[str, Any]]:
    """Run one risk_of_bias turn.

    `last_turn_kind` drives the tool gate. The dispatcher passes whatever
    `kind` the most recent assistant message in this thread had (or None
    on the first turn).
    """
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="risk_of_bias",
        max_tool_calls=_MAX_TOOL_CALLS,
        message_history=message_history,
        last_turn_kind=last_turn_kind,
        deps=deps,
    )

    output = result.output
    if isinstance(output, RobSummary):
        _resolve_plot_artifact(output, deps.artifacts)

    return output, turn_meta(result, deps)


def _resolve_plot_artifact(output: RobSummary, artifacts: dict[str, str]) -> None:
    """Replace summary_plot_image filename with the base64 data-URI in place."""
    ref = output.summary_plot_image
    if ref and not ref.startswith("data:") and ref in artifacts:
        output.summary_plot_image = artifacts[ref]
    elif ref and not ref.startswith("data:") and ref not in artifacts:
        logger.warning(
            "RoB summary plot %r referenced but not produced by sandbox_exec",
            ref,
        )
