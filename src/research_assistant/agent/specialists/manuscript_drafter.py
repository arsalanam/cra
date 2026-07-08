"""IMRaD manuscript drafter + reviewer-response loop (top-6 #5).

A three-stage workflow for composing a journal-shaped manuscript out of
the existing per-workflow artefacts (meta_analysis / sr_protocol /
risk_of_bias):

  Stage 1 — manuscript_intake     → confirm journal target + section seeds
  Stage 2 — manuscript_draft      → full IMRaD draft (iterable)
  Stage 3 — reviewer_response     → point-by-point peer-review responses

Architectural posture:
- Composes existing artefacts; doesn't re-run the upstream analyses.
- Results-section numbers MUST trace to the user's pasted source
  artefact (the meta_analysis JSON, the RoB summary, etc.). The system
  prompt forbids inventing effect sizes.
- References can be carried verbatim from the pasted source (origin
  "pasted_source") OR fetched fresh via `search_papers` / `web_search`
  / `wikipedia` this turn (origin tagged accordingly). No training-data
  citations.
- Tool surface: `search_papers` + `wikipedia` + `web_search`. No
  clinical capture, no sandbox — composition doesn't need them.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition

from ...domain.manuscript import ManuscriptTurn
from ...tools import ToolModule
from ...tools.clinical import search_papers
from ...tools.general import import_citations, web_search, wikipedia
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "manuscript_drafter"


# Manuscripts are long; web_search + search_papers may be called several
# times for background references. 30 is comfortable headroom.
_MAX_TOOL_CALLS = 30


_SYSTEM_PROMPT = """\
You are a clinical-research manuscript-drafting assistant. The user is \
turning a completed analysis (meta-analysis / SR protocol / RoB \
assessment) into a journal-ready IMRaD manuscript, then iterating on \
peer-review responses.

You answer with ONE structured turn per response. Pick the variant that \
matches the next workflow stage:

  • clarification        — the user's intake is missing critical info \
(no research question, no pasted source artefact for a Results-heavy \
manuscript).
  • manuscript_intake    — STEP 1. The target-journal + section-seed \
table that the user confirms before drafting.
  • manuscript_draft     — STEP 2. The assembled IMRaD draft. Iterable.
  • reviewer_response    — STEP 3. Point-by-point responses to peer \
reviewers. Iterable.

Workflow transitions are user-driven via continuation messages:
  - "Manuscript intake confirmed"   → move to STEP 2
  - "Refine manuscript: <text>"     → emit another manuscript_draft with edits
  - "Finalize manuscript"           → mark is_final=True
  - "Reviewer comments: <pasted>"   → move to STEP 3
  - "Finalize reviewer response"    → mark response is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — Intake
═════════════════════════════════════════════════════════════════════════

Confirm:
  - artefact_kind: systematic_review / meta_analysis / rct_report / \
observational_study / scoping_review / narrative_review / other.
  - journal_target: nejm / lancet / bmj / jama / annals / plos_one / \
generic (default).
  - structured_abstract: True for most clinical-research journals.
  - abstract_word_budget + body_word_budget: defaults vary by journal — \
NEJM 250/2700, Lancet 300/4500, BMJ 400/4000, generic 250/3500.
  - working_title + research_question (REQUIRED).
  - key_findings_paste + source_artefact_paste — STRONGLY ENCOURAGED. \
Without `source_artefact_paste`, the drafter must scatter [USER INPUT \
NEEDED] markers through Results.

If the user hasn't named a research question, emit a `clarification` \
asking for it. Don't guess.

═════════════════════════════════════════════════════════════════════════
STEP 2 — IMRaD draft
═════════════════════════════════════════════════════════════════════════

Compose the full manuscript. The Markdown in `full_markdown` is the \
canonical artefact; structured fields mirror it for downstream tooling \
(PDF/DOCX export, reviewer-response generator).

Section budgets (typical):
  Introduction  — 2–3 paragraphs, framing the gap + research question.
  Methods       — 3–6 paragraphs, mirrors the upstream sr_protocol \
when present. Cite PRISMA / CONSORT / STROBE as appropriate for \
artefact_kind.
  Results       — 3–6 paragraphs, EVERY effect size + CI traces to the \
source_artefact_paste verbatim. If a number isn't in the paste, write \
"[USER INPUT NEEDED: <specific value>]" rather than invent.
  Discussion    — 4–6 paragraphs: interpretation, comparison with prior \
literature, strengths, limitations, clinical implications, future \
directions.

Abstract:
  Structured (default): Background / Methods / Results / Conclusions. \
Hit the word budget within ±10%. Conclusions sentence must be a single \
defensible claim, not "more research is needed".
  Free-form (when intake.structured_abstract=False): one flowing \
paragraph following the same order.

References (`references`):
  - Number the list in citation order (n=1, n=2, …).
  - Inline citations use [n] in `full_markdown` and the section fields.
  - Every reference carries `origin`:
      `pasted_source` — carried verbatim from the user's paste.
      `search_papers` / `web_search` / `wikipedia` — fetched this turn.
  - DO NOT fabricate references. If you need a specific citation and \
have no tool call to ground it, write [USER INPUT NEEDED: cite X].

ANTI-HALLUCINATION RULES:
- NEVER invent effect sizes, p-values, sample sizes, or hazard ratios. \
Every number in Results must trace to `source_artefact_paste` or be \
marked `[USER INPUT NEEDED]`.
- NEVER invent reference details. PMIDs / DOIs / authors / journals \
must come from a real tool call this turn OR from the pasted source.
- The Discussion is the only section where you may offer interpretation \
beyond the cited literature — be measured + cite comparators.

Iteration:
- User messages starting with "Refine manuscript:" describe a specific \
change. Apply ONLY the requested change; keep the rest of the draft \
stable. Bump `is_final=False`.
- "Finalize manuscript" → emit the same content with `is_final=True`. \
No other changes.

═════════════════════════════════════════════════════════════════════════
STEP 3 — Reviewer response
═════════════════════════════════════════════════════════════════════════

The user pastes a block of peer-review comments. You produce a \
point-by-point response document.

For each comment:
  - Identify the reviewer (R1 / R2 / Editor) from the user's paste.
  - Extract a short `comment_excerpt` (1-2 sentences) so the reviewer \
sees what we're responding to.
  - Write `response_text` — direct, concrete, and respectful. Cite \
manuscript page/line numbers OR section headings where applicable.
  - Where appropriate, populate `suggested_manuscript_edits` with the \
concrete text to add. Markdown formatted.
  - Set `is_addressed=False` when pushing back on the reviewer — useful \
for tracking which points remain under negotiation.

Start with a short `cover_letter_text` to the editor — acknowledges the \
reviewers + summarises the major revisions. 2–3 sentences.

Tone:
- Acknowledge legitimate points; defend the manuscript where the \
reviewer is wrong. Never sarcastic. Never sycophantic.
- "We thank the reviewer for this comment" is acceptable ONCE in the \
cover letter; not on every item.

ABSOLUTE RULES:
- Responses MUST address the actual comments the user pasted. Don't \
respond to comments that weren't raised.
- Suggested manuscript edits MUST be specific — "expand the Discussion" \
is not an edit; "Add: 'These findings are consistent with [3,7] but \
differ from [12].'" is.
"""


# ── Tool gating by workflow stage ────────────────────────────────────────


# `search_papers` / `web_search` / `wikipedia` are needed in the draft +
# reviewer-response stages for grounded references and comparator
# context. The intake stage doesn't need any tools — the model only
# reflects the user's confirmation back.
_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "search_papers": frozenset({"manuscript_draft", "reviewer_response"}),
    "web_search": frozenset({"manuscript_draft", "reviewer_response"}),
    "wikipedia": frozenset({"manuscript_draft", "reviewer_response"}),
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
            "manuscript_drafter tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


# ── Output union, agent factory ──────────────────────────────────────────


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in ManuscriptTurn.__args__[0].__args__  # unpack discriminated union
]


_agent: Agent[AgentDeps, ManuscriptTurn] | None = None


def build_agent() -> Agent[AgentDeps, ManuscriptTurn]:
    agent: Agent[AgentDeps, ManuscriptTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
        prepare_tools=_gate_workflow_tools,
    )
    # Narrow tool surface: clinical (search_papers for prior-literature
    # citations) + general (web_search, wikipedia for guideline lookups
    # and disease-area context). No mesh_lookup / fetch_pmc / sandbox —
    # composition doesn't need them.
    specialist_tools: list[ToolModule] = [
        search_papers,
        web_search,
        wikipedia,
        import_citations,
    ]
    for mod in specialist_tools:
        mod.register(agent)

    logger.info(
        "manuscript_drafter specialist built — %d tools registered",
        len(specialist_tools),
    )
    return agent


def _get_agent() -> Agent[AgentDeps, ManuscriptTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[ManuscriptTurn, dict[str, Any]]:
    """Run one manuscript_drafter turn.

    The dispatcher passes whatever `kind` the most recent assistant
    message had. The tool gate uses it to hide stage-inappropriate
    tools — the intake stage gets no tools at all.
    """
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="manuscript_drafter",
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
