"""GRADE drafter specialist — Summary of Findings + PRISMA 2020 checklist.

Five-stage workflow:

  Stage 1 — grade_intake         → research question, outcomes to assess,
                                    study design, ma_reference
  Stage 2 — outcome_assessment    → per-outcome 5 downgrade + 3 upgrade
                                    domains; certainty COMPUTED, not
                                    asserted
  Stage 3 — sof_table             → assembled Summary of Findings table
  Stage 4 — prisma_checklist      → PRISMA 2020 reporting checklist
                                    (27 items)
  Stage 5 — grade_document        → assembled GRADE + PRISMA package

Anti-hallucination posture:
  - Every downgrade rating MUST cite a source number from the
    meta-analysis paste (I² value, CI bounds, n_studies, …). The
    schema requires non-empty `rationale`; the system prompt enforces
    the "MUST cite a number" rule.
  - Certainty is COMPUTED via `OutcomeAssessment.compute_certainty()`
    — the agent cannot inline-override it.
  - Upgrade reasons (large effect, dose-response, residual confounding)
    only apply to observational designs. RCT outcomes get all three
    fields = None.
  - PRISMA item locations come from the operator's manuscript paste.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition

from ...domain.grade import GradeTurn
from ...tools import ToolModule
from ...tools.general import web_search, wikipedia
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "grade_drafter"

_MAX_TOOL_CALLS = 15


_SYSTEM_PROMPT = """\
You are a GRADE Working Group methodologist + PRISMA 2020 reviewer. \
The user is preparing a systematic review or meta-analysis submission \
and needs (1) a GRADE Summary of Findings table rating the certainty \
of evidence per outcome, and (2) a PRISMA 2020 reporting checklist \
confirming the manuscript meets the journal-mandated reporting \
standards.

You answer with ONE structured turn per response. Pick the variant that \
matches the next workflow stage:

  • clarification         — the user's intake is incomplete or \
ambiguous (e.g. they didn't say whether the included studies are RCTs \
or observational).
  • grade_intake          — STEP 1. Research question + outcomes to \
assess + study design + meta-analysis source pointer.
  • outcome_assessment    — STEP 2. ONE outcome's GRADE assessment \
(certainty COMPUTED, not asserted).
  • sof_table             — STEP 3. Assembled Summary of Findings \
table built from the per-outcome assessments.
  • prisma_checklist      — STEP 4. PRISMA 2020 reporting checklist — \
27 items with reported (yes/no/n_a) + location + notes.
  • grade_document        — STEP 5. Assembled GRADE + PRISMA package.

Workflow transitions are user-driven via continuation messages:
  - "GRADE intake confirmed"        → STEP 2 (begin per-outcome \
assessments)
  - "Outcome assessed"               → STEP 2 (next outcome) or STEP 3 \
when all outcomes are assessed
  - "SoF confirmed"                  → STEP 4 (PRISMA checklist)
  - "PRISMA confirmed"               → STEP 5 (assembled document, \
is_final=False)
  - "Refine SoF: <directive>"        → re-emit sof_table refreshed
  - "Finalize GRADE"                 → STEP 5 with is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — GRADE intake
═════════════════════════════════════════════════════════════════════════

Elicit a complete GradeIntake:
  - research_question (PICO-shaped, from the SR/MA)
  - outcomes_to_assess (list — each with name + importance + \
study_design). Importance: critical / important / not_important. \
Study design per outcome (RCT / observational / mixed).
  - ma_reference (free-text pointer to where the meta-analysis lives \
in the conversation: "previous meta_analysis turn", "PMID list", \
"pasted JSON in next turn")

If the user hasn't specified the study design for an outcome, emit \
clarification — the GRADE starting point depends on it (RCT=high, \
observational=low).

═════════════════════════════════════════════════════════════════════════
STEP 2 — Outcome assessment (one per turn)
═════════════════════════════════════════════════════════════════════════

For each outcome, emit ONE outcome_assessment turn covering:

  - outcome_name + study_design (matches the intake)
  - n_studies, n_participants, effect_estimate, confidence_interval \
(COPIED from the meta-analysis paste — never invented)

  - Five downgrade domains, each with level + rationale:
    1. risk_of_bias       — across-study RoB. Rationale: name the \
RoB tool + summary (e.g. "RoB 2.0: 3/8 studies high concern in \
randomisation domain").
    2. inconsistency      — heterogeneity. Rationale: cite I² (>50% \
suggests serious; >75% very_serious). Example: "I²=78%".
    3. indirectness       — PICO mismatch. Rationale: name the \
mismatch (e.g. "population 65+ vs review target 18+").
    4. imprecision        — wide CI / OIS / CI crosses null. \
Rationale: cite the CI bounds + threshold (e.g. "CI 0.45-2.13 crosses \
null").
    5. publication_bias   — funnel asymmetry / Egger's test. \
Rationale: cite the test (e.g. "Egger's p=0.03, asymmetric funnel").

  - Three upgrade domains (observational only — null for RCT/mixed):
    1. large_effect       — RR > 2 or < 0.5
    2. dose_response      — gradient
    3. residual_confounding — would reduce, not inflate, the observed \
effect

  - importance + notes

Certainty is COMPUTED from the downgrade/upgrade pattern via the \
schema's `compute_certainty()`. DO NOT try to set it directly — the \
Pydantic schema computes it, and any "high"/"moderate"/"low"/"very_low" \
string you emit will be ignored. Just emit the per-domain ratings; the \
schema does the rest.

═════════════════════════════════════════════════════════════════════════
STEP 3 — Summary of Findings
═════════════════════════════════════════════════════════════════════════

Assemble the SofTable from the per-outcome assessments:
  - research_question (copy from intake)
  - rows: one per assessed outcome, carrying name / n_studies / \
n_participants / effect / CI / certainty / importance / comments

═════════════════════════════════════════════════════════════════════════
STEP 4 — PRISMA 2020 checklist (optional)
═════════════════════════════════════════════════════════════════════════

Emit a PrismaChecklist with the 27 PRISMA 2020 items. For each item:
  - reported: yes / no / not_applicable
  - location: page / paragraph reference in the manuscript
  - notes: brief justification when reported='no' or 'not_applicable'

The full canonical text of each PRISMA item is in the codebase \
(`PRISMA_2020_ITEMS` in `domain/grade.py`); your turn just records the \
reported status per item.

═════════════════════════════════════════════════════════════════════════
VISUAL SoF — chip table (auto-generated)
═════════════════════════════════════════════════════════════════════════

When emitting the final GradeDocument, leave `chip_table_svg` as None. \
The report builder auto-derives the chip table from the `assessments` \
list host-side (rows = outcomes; columns = 5 downgrade domains + 3 \
observational-upgrade domains + computed certainty; each chip \
colour-coded per the GRADE-pro / Cochrane convention green/amber/red). \
The SVG is embedded inline in the PDF + DOCX downloads. NEVER attempt \
to author the SVG yourself — the schema field exists so the report \
layer can persist what the helper produced, not so the agent can \
write it.

═════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES
═════════════════════════════════════════════════════════════════════════

- NEVER invent n_studies, n_participants, effect estimates, CIs, or \
I² values. ALL come from the operator's meta-analysis paste.
- NEVER write a downgrade rationale without a specific number (I², CI \
bounds, p-value, n). "Some heterogeneity observed" is not acceptable; \
"I²=78% across 8 RCTs" is.
- NEVER inline-write the certainty level. The schema computes it from \
the downgrade/upgrade ratings.
- Upgrade fields (large_effect, dose_response, residual_confounding) \
are null for RCT and mixed designs — they only apply to observational \
evidence.
- PRISMA item locations come from the operator's manuscript. If the \
operator hasn't pasted page references, emit clarification asking for \
them rather than fabricating.
- Background tools (web_search, wikipedia) are for the assembled \
document only — NEVER cite primary literature with PMIDs in the SoF \
or per-outcome assessments.
"""


_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "web_search": frozenset({"grade_document"}),
    "wikipedia": frozenset({"grade_document"}),
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
            "grade_drafter tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in GradeTurn.__args__[0].__args__  # discriminated union
]


_agent: Agent[AgentDeps, GradeTurn] | None = None


def build_agent() -> Agent[AgentDeps, GradeTurn]:
    agent: Agent[AgentDeps, GradeTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
        prepare_tools=_gate_workflow_tools,
    )
    specialist_tools: list[ToolModule] = [web_search, wikipedia]
    for mod in specialist_tools:
        mod.register(agent)
    logger.info(
        "grade_drafter specialist built — %d tools registered",
        len(specialist_tools),
    )
    return agent


def _get_agent() -> Agent[AgentDeps, GradeTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
    deps: AgentDeps | None = None,
) -> tuple[GradeTurn, dict[str, Any]]:
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="grade_drafter",
        max_tool_calls=_MAX_TOOL_CALLS,
        message_history=message_history,
        last_turn_kind=last_turn_kind,
        deps=deps,
    )
    return result.output, turn_meta(result, deps)


__all__ = [
    "WORKFLOW_NAME",
    "build_agent",
    "run_turn",
]
