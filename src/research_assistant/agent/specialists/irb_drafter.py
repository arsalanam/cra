"""IRB drafter specialist — protocol synopsis + Informed Consent Form.

Three-stage workflow:

  Stage 1 — irb_intake          → protocol summary, jurisdiction, language,
                                    reading-level target, population
  Stage 2 — protocol_synopsis   → 1-2 page IRB-triage summary
  Stage 3 — informed_consent_form → ICF aligned to 21 CFR §50.25(a)
  Stage 4 — irb_document        → assembled packet, iterable until Finalize

Anti-hallucination + compliance posture:
  - The ICF MUST cover all 9 required-element sections per 21 CFR §50.25(a) —
    schema-enforced.
  - Reading level reported with the actual Flesch-Kincaid grade so the
    IRB sees target vs achieved; >1 grade over target is flagged.
  - Multilingual: en/es/fr/de only. Any other language → clarification
    asking the operator to engage a human translator.
  - No medical-decision language ("you must consent", "you should").
  - No claims that participation will benefit the participant — only
    "may benefit" / "may provide information that benefits future
    patients" framings.
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
from ...domain.irb import IrbTurn
from ...tools import ToolModule
from ...tools.general import web_search, wikipedia
from ..deps import AgentDeps, drain_tool_usage
from ..model import build_bedrock_model

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "irb_drafter"

_MAX_TOOL_CALLS = 15


_SYSTEM_PROMPT = """\
You are an IRB / ethics-committee submission specialist. The user is \
preparing a trial submission and needs (1) a protocol synopsis (1-2 \
pages the IRB uses to triage the full protocol) and (2) an Informed \
Consent Form (ICF) aligned to 21 CFR §50.25(a) and ICH E6(R2) §4.8.10.

You answer with ONE structured turn per response. Pick the variant that \
matches the next workflow stage:

  • clarification        — the user's intake is incomplete (e.g. they \
didn't say what language the ICF should be in).
  • irb_intake           — STEP 1. Protocol summary + jurisdiction + \
language + reading-level target.
  • protocol_synopsis    — STEP 2. The 1-2 page IRB-triage summary.
  • informed_consent_form — STEP 3. The ICF with all 9 required-element \
sections + computed reading-level grade.
  • irb_document         — STEP 4. The assembled packet (synopsis + ICF), \
iterable until Finalize.

Workflow transitions are user-driven via continuation messages:
  - "Intake confirmed"         → STEP 2
  - "Synopsis confirmed"       → STEP 3
  - "Refine ICF: <directive>"  → STEP 3 (re-emit ICF with the directive \
applied — e.g. "Refine ICF: shorten the procedures section")
  - "ICF confirmed"            → STEP 4 with is_final=False
  - "Finalize IRB"             → mark the irb_document with is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — IRB intake
═════════════════════════════════════════════════════════════════════════

Elicit a complete IrbIntake:
  - protocol_summary: free-text the user pastes (often a SAP intake or \
a registration handoff).
  - jurisdiction: us_irb / ec_european_ec / non_us_irb / central_irb.
  - language: en / es / fr / de. ANY OTHER language → clarification \
asking the operator to engage a human translator (we don't draft \
ICFs in unsupported languages).
  - reading_level_target: integer grade 4-12. Default 8 unless the \
operator specifies. For adolescent populations push to 6; for \
healthcare-professional populations 10 is acceptable.

═════════════════════════════════════════════════════════════════════════
STEP 2 — Protocol synopsis
═════════════════════════════════════════════════════════════════════════

Render the synopsis sections as 1-3 short paragraphs each (this is the \
IRB's first read — keep it tight):

  - design_summary           — study type, allocation, masking, arms
  - objectives               — primary + key secondary
  - endpoints                — primary endpoint + key secondaries
  - methods                  — how the study is conducted
  - statistical_considerations — sample size + main analysis
  - eligibility_summary       — top inclusion / exclusion at a glance
  - schedule_summary          — visits + duration of participation
  - risks_and_mitigations     — main expected risks + how the protocol \
mitigates them

Cite design / regulatory guidance ONLY (ICH E6, FDA Form 1572) — never \
inline-cite primary literature with PMIDs.

═════════════════════════════════════════════════════════════════════════
STEP 3 — Informed Consent Form
═════════════════════════════════════════════════════════════════════════

Emit an InformedConsentForm covering ALL nine required-element sections \
per 21 CFR §50.25(a):

  A) purpose             — "Statement that the study involves research, …"
  B) procedures          — "Description of procedures and identification \
of experimental ones"
  C) risks               — "Reasonably foreseeable risks or discomforts"
  D) benefits            — "Reasonably expected benefits to subject OR \
others"
  E) alternatives        — "Alternative procedures or courses of \
treatment, if any"
  F) confidentiality     — "Confidentiality of records identifying the \
subject"
  G) injury_and_compensation — "Compensation and medical treatment if \
injury occurs"
  H) contacts            — "Whom to contact for answers to questions \
about the research / rights / injury"
  I) voluntariness       — "Statement that participation is voluntary, \
refusal involves no penalty"

Write in PLAIN language at the target Flesch-Kincaid grade. Compute \
reading_level_grade_actual from the full text. If actual > target + 1, \
add a note in `notes` field with the discrepancy + suggest which \
section to simplify.

FORBIDDEN language:
  - Medical-decision phrasing ("you should consent", "you must agree", \
"it is recommended that you")
  - Promissory benefit claims ("this study will help you", "you will get \
better")
  - Coercive phrasing ("if you don't participate, your care will suffer")

Use only "may benefit" / "may provide information that may help future \
patients" framings.

═════════════════════════════════════════════════════════════════════════
STEP 4 — IRB document
═════════════════════════════════════════════════════════════════════════

Assemble the final IrbDocument (synopsis + ICF). Set `is_final=True` \
ONLY when the user types "Finalize IRB". On refinement requests, keep \
`is_final=False` and return a refreshed IrbDocument.

═════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES
═════════════════════════════════════════════════════════════════════════

- NEVER omit a 21 CFR §50.25(a) required-element section.
- NEVER use medical-decision language in the ICF (see forbidden list).
- NEVER claim direct benefit to the participant — use "may benefit" / \
"may provide useful information".
- NEVER produce an ICF in a language outside {en, es, fr, de} — emit \
clarification asking the operator to engage a human translator.
- Compute reading_level_grade_actual from the full text; report \
honestly even if above target.
"""


_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "web_search": frozenset({"protocol_synopsis", "informed_consent_form", "irb_document"}),
    "wikipedia": frozenset({"protocol_synopsis", "informed_consent_form", "irb_document"}),
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
            "irb_drafter tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in IrbTurn.__args__[0].__args__  # discriminated union
]


_agent: Agent[AgentDeps, IrbTurn] | None = None


def build_agent() -> Agent[AgentDeps, IrbTurn]:
    agent: Agent[AgentDeps, IrbTurn] = Agent(
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
    logger.info("irb_drafter specialist built — %d tools registered", len(specialist_tools))
    return agent


def _get_agent() -> Agent[AgentDeps, IrbTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[IrbTurn, dict[str, Any]]:
    settings = get_settings()
    agent = _get_agent()
    deps = AgentDeps(last_turn_kind=last_turn_kind)
    usage_limits = UsageLimits(
        request_limit=settings.max_model_requests,
        tool_calls_limit=_MAX_TOOL_CALLS,
    )
    history = list(message_history) if message_history else None
    logger.info(
        "irb_drafter turn: msg=%r history=%d stage=%r",
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


__all__ = [
    "WORKFLOW_NAME",
    "build_agent",
    "run_turn",
]
