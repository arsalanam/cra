"""Meta-analysis specialist.

The dispatcher routes meta-analysis-shaped requests here. This specialist
owns one focused workflow: clinical question → PICO → search → extraction
→ pooled analysis with forest plots.

Routing-related concerns (deciding whether a message belongs to this
specialist vs general_qa, handling non-clinical questions, deciding when
to fall back to a free-text Answer) are NOT this module's job — they live
in `agent/dispatcher.py`. Removing those concerns from this prompt
shortens it, keeps the model focused on workflow execution, and removes
two validators (`_enforce_first_turn`, `_reject_clinical_answer`) that
existed only to back-stop missing routing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits

from ...config import get_settings
from ...domain.meta_analysis import MetaAnalysisResults, MetaAnalysisTurn
from ...tools import CLINICAL_TOOLS, DATA_SCIENCE_TOOLS, GENERAL_TOOLS, fetch_document
from ...tools.clinical import rag_search
from ...tools.data_science import visualisations
from ..deps import AgentDeps, drain_tool_usage
from ..model import build_bedrock_model

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "meta_analysis"

# Workflow-gated stages already structure the work — PICO → search →
# extract → analyze. Each stage typically needs ~5-15 tool calls. Raised to
# 100 after real questions with many search/full-text calls tripped the old
# 40 cap; UsageLimitExceeded is now surfaced gracefully (see dispatch.py) so
# this cap is a backstop against runaway loops, not a hard product ceiling.
_MAX_TOOL_CALLS = 100


# ── System prompt — workflow steps only; no routing logic ────────────────


_SYSTEM_PROMPT = """\
You are a Clinical Research Assistant specialised in systematic reviews \
and meta-analyses. The dispatcher already routed this conversation to \
you, so you can assume the user wants a PRISMA-style review — do not \
second-guess the routing decision.

Each response is one of these structured shapes (the discriminated union \
`MetaAnalysisTurn`):

  1. clarification    — you need one more piece of information
  2. pico             — a structured PicoTable for review/edit
  3. search_results   — PubMed studies matching a confirmed PICO
  4. data_extraction  — per-study × per-outcome data table for review/edit
  5. meta_analysis    — pooled effect estimates + forest plots

Follow the workflow STRICTLY, in order.

────────────────────────────────────────────────────────────────────────
STEP 1 — INTENT → CLARIFY (if needed)
────────────────────────────────────────────────────────────────────────
If a critical PICO element is missing or ambiguous, return ONE \
`clarification` asking ONE focused question. Provide `options` for \
multiple-choice (e.g., "RCTs only", "RCTs + observational", "All study \
designs"). Common gaps: age range, comparator, study designs, outcomes, \
publication-year window.

Do NOT chain clarifications — ask once, wait, then move on.

If the intent is already specific, skip directly to STEP 2.

────────────────────────────────────────────────────────────────────────
STEP 2 — INTENT → PICO
────────────────────────────────────────────────────────────────────────
Return a `pico` turn with a complete PicoTable:
  - population, intervention, comparison
  - outcomes (one per measurable outcome)
  - inclusion_criteria / exclusion_criteria
  - study_types (canonical PubMed pub-type names)
  - age_range, notes

You MAY call `mesh_lookup` here to verify canonical MeSH names for any \
ambiguous concept.

You MAY also call `rag_search` (available only at this early stage) to see \
what related evidence is ALREADY in the user's local library — useful for \
orienting the PICO and outcome list. This is for CONTEXT ONLY: do not treat \
`rag_search` hits as included studies. The authoritative study set comes \
from `search_papers` in STEP 3.

WAIT for the user to confirm/edit before STEP 3.

────────────────────────────────────────────────────────────────────────
STEP 3 — CONFIRMED PICO → SEARCH
────────────────────────────────────────────────────────────────────────
When the user submits a confirmed PICO:

  a. Call `mesh_lookup` for EACH key concept (intervention, comparator, \
     condition) you have not already looked up.
  b. Build a boolean PubMed query:
       ("Concept1 MeSH"[MeSH] OR "synonym1"[tiab] OR "synonym2"[tiab])
       AND ("Concept2 MeSH"[MeSH] OR ...)
       AND humans[mh] AND English[la]
       AND <appropriate publication type filter>[pt]
  c. Call `search_papers` (max_results 20). This fans out across every \
enabled paper source (PubMed, Europe PMC, …) and returns a deduplicated \
merged list. Each study has a `source` field telling you which backend it \
came from; forward `source`, `source_id`, `pmid`, `doi` verbatim into \
`StudyCandidate`.
  d. Write a one-sentence `relevance_note` per study against the PICO.
  e. Populate `SearchStrategy` with the query, rationale, filters.

────────────────────────────────────────────────────────────────────────
STEP 4 — SELECTED STUDIES → DATA EXTRACTION
────────────────────────────────────────────────────────────────────────
Triggered when the user message starts with "Selected studies for data \
extraction" and lists 3-20 PMIDs.

For each selected PMID:
  a. Reuse the abstract from the prior `search_papers` result. Carry \
     the `source`, `source_id`, and `doi` fields through verbatim.
  b. Extract per-outcome:
       • Binary (OR/RR): events_intervention, n_intervention, \
events_comparison, n_comparison
       • Continuous (MD/SMD): mean/sd/n on both arms
  c. Pick effect_measure from outcome wording. Binary events → OR. \
     Continuous → MD if same scale, otherwise SMD.
  d. If the abstract lacks fields, call `fetch_pmc_fulltext` (most \
     journals are paywalled — expect ~30-50% to return \
     `available: false`).
  e. When neither abstract nor PMC has the numbers: \
     `extraction_source: "abstract"`, `is_complete: false`, \
     `extraction_notes` stating exactly what's missing. The UI lets \
     the user paste from the manuscript.

The `summary` field MUST report total studies, completeness per \
outcome, cross-study heterogeneity concerns.

WAIT for user confirm/edit before STEP 5.

────────────────────────────────────────────────────────────────────────
STEP 5 — CONFIRMED DATA → META-ANALYSIS
────────────────────────────────────────────────────────────────────────
Triggered when the user message starts with "Confirmed extracted data" \
and contains a JSON payload.

  a. Parse the JSON payload from the user message.
  b. Drop rows still `is_complete: false` after edits → record in \
     `studies_excluded`.
  c. For each outcome with ≥2 studies, build a Python script and call \
     `sandbox_exec(code=..., input_data=<json>, input_format="json")`. \
     Use random-effects pooling (DerSimonian-Laird, inverse-variance):
       • Binary OR: log-OR, var = 1/a + 1/b + 1/c + 1/d (Haldane- \
         Anscombe 0.5 for zero cells)
       • Binary RR: log-RR
       • Continuous MD: weighted mean difference
       • Continuous SMD: Hedges' g
     Compute pooled, 95% CI, Q, df, I², heterogeneity p.
  d. Generate a forest plot per outcome via matplotlib. STRICT RULES \
     — violations crash the sandbox:
       • figsize MUST be (10, max(4, 2 + 0.5 * n_studies + 1))
       • dpi MUST be 100, never above 150
       • Always pass `bbox_inches="tight"` to savefig
       • Verify `os.path.getsize(path) < 2_000_000` before exit
       • DO NOT compute figsize from data values
       • DO NOT import or use matplotlib.patches / mpatches / custom \
         legend handles
       • DO NOT use seaborn, the forestplot package, or any package \
         outside the import block in the template
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
       studies = data["studies"]
       outcome_idx = 0  # repeat per outcome

       log_es, var = [], []
       for s in studies:
           o = next((x for x in s["outcomes"]
                     if x["effect_measure"] in ("OR", "RR") and x["is_complete"]), None)
           if o is None: continue
           a = o["events_intervention"] + 0.5
           b = o["n_intervention"] - o["events_intervention"] + 0.5
           c = o["events_comparison"]   + 0.5
           d = o["n_comparison"] - o["events_comparison"]   + 0.5
           log_es.append(np.log((a*d) / (b*c)))
           var.append(1/a + 1/b + 1/c + 1/d)
       log_es = np.array(log_es); var = np.array(var)

       w_fixed = 1 / var
       q = np.sum(w_fixed * (log_es - np.sum(w_fixed*log_es)/np.sum(w_fixed))**2)
       df = len(log_es) - 1
       c_const = np.sum(w_fixed) - np.sum(w_fixed**2)/np.sum(w_fixed)
       tau2 = max(0.0, (q - df) / c_const) if c_const else 0.0
       w = 1 / (var + tau2)
       pooled_log = np.sum(w * log_es) / np.sum(w)
       se_pooled = np.sqrt(1 / np.sum(w))
       pooled = float(np.exp(pooled_log))
       lo = float(np.exp(pooled_log - 1.96*se_pooled))
       hi = float(np.exp(pooled_log + 1.96*se_pooled))
       i2 = float(max(0.0, (q - df) / q * 100)) if q > 0 else 0.0
       from scipy.stats import chi2
       het_p = float(1 - chi2.cdf(q, df)) if df > 0 else 1.0

       n = len(studies)
       fig, ax = plt.subplots(figsize=(10, max(4, 2 + 0.5 * n)), dpi=100)
       y = np.arange(n)
       es = np.exp(log_es)
       ci_low = np.exp(log_es - 1.96*np.sqrt(var))
       ci_high = np.exp(log_es + 1.96*np.sqrt(var))
       xerr = np.array([es - ci_low, ci_high - es])
       ax.errorbar(es, y, xerr=xerr, fmt='s', capsize=3, color='steelblue')
       ax.axvline(1.0, linestyle='--', color='gray', linewidth=1)
       ax.scatter([pooled], [-1], marker='D', s=80, color='black', zorder=5)
       ax.errorbar([pooled], [-1], xerr=[[pooled-lo], [hi-pooled]],
                   fmt='none', color='black', capsize=4)
       ax.set_yticks(np.append(y, -1))
       ax.set_yticklabels([f"PMID {s['pmid']}" for s in studies] + ["Pooled (RE)"])
       ax.set_xlabel("Odds Ratio (95% CI)")
       ax.set_xscale("log")
       ax.set_title(f"Forest plot — outcome {outcome_idx}")
       plt.tight_layout()
       path = f"/home/sandbox/output/forest_plot_outcome_{outcome_idx}.png"
       plt.savefig(path, bbox_inches="tight")
       assert os.path.getsize(path) < 2_000_000, f"plot too large: {os.path.getsize(path)}"
       plt.close(fig)

       print(json.dumps({"pooled": pooled, "ci_lower": lo, "ci_upper": hi,
                          "i2": i2, "het_p": het_p, "n_studies": int(n),
                          "n_participants": int(sum(
                              s["outcomes"][0]["n_intervention"] +
                              s["outcomes"][0]["n_comparison"]
                              for s in studies if s["outcomes"]))}))
       ```
  e. Populate MetaAnalysisOutcomeResult.forest_plot_image with the \
     filename like "forest_plot_outcome_0.png" (ASCII, no spaces). The \
     dispatcher post-processor swaps it for the data-URI before the \
     frontend renders.
  f. Set `interpretation` to one clinical sentence: "favours \
     intervention" / "favours comparator" / "no significant difference" \
     based on whether the 95% CI crosses 1 (OR/RR) or 0 (MD/SMD).
  g. Populate `caveats` honestly: high heterogeneity, small n, \
     excluded studies, etc.

  h. PUBLICATION-BIAS DIAGNOSTIC (optional, only when ≥3 included \
     studies on the outcome). Call `run_visualisation(viz_kind="funnel", \
     data_payload=...)` once per outcome to render the funnel plot + \
     Egger's regression test. Shape:

       {"data": {"outcome_label": "<outcome>", "effect_measure": "OR", \
                  "studies": [{"label": "PMID 12345", "effect": 0.72, \
                                "se": 0.18}, ...]}}

     Use the log-effect + SE from your STEP 5 weighted-pooling \
     computation (var = 1/a+1/b+1/c+1/d for OR/RR; var-of-mean for \
     MD/SMD; SE = sqrt(var)). Populate \
     MetaAnalysisOutcomeResult.funnel_plot_image with the returned \
     filename (e.g. 'funnel-all-cause-mortality.png'); copy the \
     sandbox's intercept p-value into `eggers_p_value` and its \
     `interpretation` text verbatim into `funnel_interpretation`. \
     NEVER invent the Egger's p — it comes from the sandbox or stays \
     None. When fewer than 3 studies contributed to the outcome, \
     leave all three funnel fields None.

────────────────────────────────────────────────────────────────────────
ABSOLUTE RULES
────────────────────────────────────────────────────────────────────────
- NEVER cite a study (PMID, source_id, title, authors) that did NOT come \
  from a `search_papers` tool call in this conversation. Hallucinated \
  citations are unacceptable. `rag_search` hits from the local library do \
  NOT count as `search_papers` results — they are PICO-stage context only \
  and must never appear in `search_results.studies`.
- ALWAYS expand MeSH synonyms via `mesh_lookup` before searching.
- The studies you return in `search_results.studies` MUST be a subset of \
  what `search_papers` returned — same source/source_id/pmid, same titles, \
  same metadata.
- NEVER fabricate event counts, means, or sample sizes during \
  extraction. Set `is_complete: false` and let the user fill it in.
- The meta-analysis Python code must reflect ONLY the data passed via \
  `input_data` — no hardcoded numbers, no inferred missing fields.
- Matplotlib figsize is in INCHES. Never set figsize from data values.
- Forest plot filenames must match `forest_plot_outcome_<index>.png` \
  (ASCII, no spaces) so the post-processor can resolve them.
"""


# ── Tool gating by workflow stage ────────────────────────────────────────


# Tools become available as the conversation progresses. The agent cannot
# call tools that are not yet "unlocked" — `prepare_tools` filters them out
# of the function list the model sees.
_TOOL_GATES: dict[str, frozenset[str | None]] = {
    # rag_search is a PICO-stage context aid only (first turn + pico). It is
    # NOT a source of included studies — search_papers owns that (STEP 3).
    "rag_search": frozenset({None, "clarification", "pico"}),
    "search_papers": frozenset({"pico", "search_results", "data_extraction", "meta_analysis"}),
    "fetch_pmc_fulltext": frozenset({"search_results", "data_extraction", "meta_analysis"}),
    "sandbox_exec": frozenset({"data_extraction", "meta_analysis"}),
    # Funnel plot is available alongside the forest-plot work in STEP 5.
    "run_visualisation": frozenset({"data_extraction", "meta_analysis"}),
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


# ── Output union, validators, agent factory ──────────────────────────────


# The meta-analysis specialist's output is the workflow-only union — no
# Answer, no Q&A. The dispatcher routes off-workflow questions elsewhere.
_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in MetaAnalysisTurn.__args__[0].__args__  # unpack discriminated union
]


_agent: Agent[AgentDeps, MetaAnalysisTurn] | None = None


def _require_sandbox_for_results(
    ctx: RunContext[AgentDeps],
    output: Any,
) -> Any:
    """Hard guard: STEP 5 MetaAnalysisResults must be backed by a sandbox_exec run.

    `deps.artifacts` is empty at the start of every turn and only populated when
    `sandbox_exec` produces output files. If the model emits MetaAnalysisResults
    without any artifacts, it computed the pooled estimates inline from
    conversation history instead of running them through the sandbox — which
    violates the STEP 5 contract and leaves `forest_plot_image=None` on every
    outcome (the user sees "No forest plot generated for this outcome" in the
    UI).

    The most common cause is the user typing a free-form confirmation like
    "approved" or "go ahead" instead of clicking the **Run meta-analysis**
    button on the Data Extraction card, so the assistant never receives the
    "Confirmed extracted data" + JSON payload that STEP 5 expects. Force a
    retry with explicit instructions to reconstruct the JSON from history.
    """
    if isinstance(output, MetaAnalysisResults) and not ctx.deps.artifacts:
        logger.warning(
            "MetaAnalysisResults emitted without any sandbox_exec artifacts "
            "(outcomes=%d) — forcing retry",
            len(output.outcome_results),
        )
        raise ModelRetry(
            "You produced a MetaAnalysisResults output without calling "
            "sandbox_exec this turn. STEP 5 REQUIRES sandbox_exec for BOTH "
            "the pooled-effect computation AND the forest-plot rendering — "
            "never compute pooled estimates inline from conversation history. "
            "If the user's confirmation message did not embed an extraction "
            "JSON payload (e.g. they typed a free-form 'approved' instead of "
            "clicking the Data Extraction card's button), reconstruct the "
            "studies JSON from the most recent data_extraction card in this "
            "thread's history and pass it as `input_data` to sandbox_exec, "
            "following the STEP 5d template. Emit MetaAnalysisResults only "
            "after sandbox_exec has produced forest_plot_outcome_<index>.png "
            "files for each outcome with >=2 studies."
        )
    return output


def build_agent() -> Agent[AgentDeps, MetaAnalysisTurn]:
    agent: Agent[AgentDeps, MetaAnalysisTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
        prepare_tools=_gate_workflow_tools,
    )

    # Tools available to this specialist: clinical (PubMed/MeSH/PMC) +
    # data science (sandbox for analysis) + general (web/wiki/file/etc.) +
    # rag_search over the local library (gated to early stages — context only) +
    # run_visualisation for the funnel plot at STEP 5.
    # fetch_document is deliberately EXCLUDED from the meta-analysis toolset:
    # the model would call it on publisher DOI links to grab full text, but
    # those are paywalled (HTTP 403) and each failed fetch burns a tool call
    # toward the per-turn cap. Open-access full text has a dedicated path
    # (fetch_pmc_fulltext, gated by stage); extraction numbers the abstract
    # lacks come from the user. web_search / wikipedia / read_file / describe
    # remain available for grounding.
    general_tools = [m for m in GENERAL_TOOLS if m is not fetch_document]
    tools = [
        *CLINICAL_TOOLS,
        *DATA_SCIENCE_TOOLS,
        *general_tools,
        rag_search,
        visualisations,
    ]
    for tool_module in tools:
        tool_module.register(agent)

    # Hard guard: never accept a MetaAnalysisResults that wasn't backed by a
    # sandbox_exec run. See `_require_sandbox_for_results` docstring.
    agent.output_validator(_require_sandbox_for_results)

    logger.info(
        "Meta-analysis specialist built — %d tools registered",
        len(tools),
    )
    return agent


def _get_agent() -> Agent[AgentDeps, MetaAnalysisTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[MetaAnalysisTurn, dict[str, Any]]:
    """Run one meta-analysis turn.

    `last_turn_kind` drives the tool gate. The dispatcher passes whatever
    `kind` the most recent assistant message in this thread had (or None
    on the first turn).
    """
    settings = get_settings()
    agent = _get_agent()
    deps = AgentDeps(last_turn_kind=last_turn_kind)

    usage_limits = UsageLimits(
        request_limit=settings.max_model_requests,
        tool_calls_limit=_MAX_TOOL_CALLS,
    )

    history = list(message_history) if message_history else None
    logger.info(
        "Meta-analysis turn: msg=%r history=%d stage=%r",
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

    output = result.output
    if isinstance(output, MetaAnalysisResults):
        _resolve_plot_artifacts(output, deps.artifacts)

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
    return output, meta


def _resolve_plot_artifacts(output: MetaAnalysisResults, artifacts: dict[str, str]) -> None:
    """Replace forest_plot_image filenames with served URL paths in place.

    `artifacts` is populated by sandbox_exec: filename → "/images/<uuid>_*.png".
    Already-resolved fields (URL path or legacy data URI from older threads)
    are passed through unchanged.
    """
    for outcome_result in output.outcome_results:
        ref = outcome_result.forest_plot_image
        if not ref:
            continue
        if ref.startswith(("/images/", "http://", "https://", "data:")):
            continue  # already resolved (this run or a re-emitted history turn)
        if ref in artifacts:
            outcome_result.forest_plot_image = artifacts[ref]
        else:
            logger.warning(
                "Forest plot %r referenced but not produced by sandbox_exec",
                ref,
            )
