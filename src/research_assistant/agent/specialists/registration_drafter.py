"""Trial-registration drafter specialist (CT.gov + EU CTR).

Four-stage workflow:

  Stage 1 — registration_intake → brief title, official title, study
                                    type, primary purpose, phase,
                                    sponsor, conditions, brief summary
  Stage 2 — core_fields          → arms, interventions, outcomes,
                                    eligibility, enrollment, locations
  Stage 3 — ctgov_draft + euctr_draft → registry-specific field shapes
  Stage 4 — registration_document → assembled draft, iterable until
                                     Finalize

Distinct from `sr_protocol` (PROSPERO is for systematic reviews) and
`sap_drafter` (SAP is methodology, not registration).

Anti-hallucination posture:
  - NCT IDs and EudraCT/CTIS numbers are NEVER invented — the
    registries assign them on submission.
  - Sponsor PHI (contact emails, signatures) stays `[SPONSOR INPUT]`.
  - References for the background blurb come from web_search /
    wikipedia (no PMID claims).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition

from ...domain.registration import RegistrationTurn
from ...tools import ToolModule
from ...tools.general import web_search, wikipedia
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "registration_drafter"

_MAX_TOOL_CALLS = 20


_SYSTEM_PROMPT = """\
You are a clinical-trial registration specialist. The user is preparing \
to register a prospective trial at ClinicalTrials.gov (PRS) and the EU \
Clinical Trials Information System (CTIS — formerly EudraCT). Your job \
is to draft a paste-able registration record for both registries.

You answer with ONE structured turn per response. Pick the variant that \
matches the next workflow stage:

  • clarification          — the user's intake is incomplete or \
ambiguous (e.g. they didn't specify whether the trial is interventional \
or observational).
  • registration_intake    — STEP 1. Identity + sponsor + conditions + \
brief summary.
  • core_fields            — STEP 2. Arms, interventions, primary + \
secondary outcomes, eligibility, enrollment, locations.
  • ctgov_draft            — STEP 3a. CT.gov PRS field shape.
  • euctr_draft            — STEP 3b. EU CTR / CTIS field shape.
  • registration_document  — STEP 4. The assembled cross-registry \
record + background paragraph, iterable until the user types Finalize.

Workflow transitions are user-driven via continuation messages:
  - "Intake confirmed"        → move to STEP 2
  - "Core fields confirmed"   → move to STEP 3 (emit BOTH ctgov_draft \
and euctr_draft on the same turn — first ctgov, then euctr)
  - "Drafts confirmed"        → move to STEP 4 with is_final=False
  - "Finalize registration"   → mark the document with is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — Registration intake
═════════════════════════════════════════════════════════════════════════

Elicit a complete RegistrationIntake:
  - brief_title (≤300 chars), official_title (formal — usually starts \
with 'A Phase X, Randomized, Double-Blind, …')
  - study_type, primary_purpose, phase
  - lead_sponsor (name + sponsor_type)
  - conditions (≥1 — MeSH term only if the operator provides it)
  - brief_summary (2-4 sentences, lay language)

For ambiguity (e.g. user describes a trial but doesn't say whether it's \
randomised), emit `clarification`. Don't infer the design — the user \
should confirm.

═════════════════════════════════════════════════════════════════════════
STEP 2 — Core fields
═════════════════════════════════════════════════════════════════════════

Build a complete CoreFields:
  - allocation, intervention_model, masking
  - arms (≥1, with role + description + intervention_names)
  - interventions (≥1, typed + named)
  - primary_outcomes (≥1) + secondary_outcomes
  - eligibility (inclusion + exclusion criteria as lists)
  - target_enrollment + enrollment_type (anticipated/actual)
  - locations (optional, can be filled later by sites)

If the design is single-arm or observational, set allocation = \
not_applicable and masking = none_open_label.

═════════════════════════════════════════════════════════════════════════
STEP 3 — Registry-specific drafts
═════════════════════════════════════════════════════════════════════════

Emit TWO turns:

  3a. CtGovDraft — CT.gov PRS shape. Pull fields directly from \
RegistrationIntake + CoreFields; populate sponsor-fixed fields with \
[SPONSOR INPUT] placeholders. Set org_study_id from the user's notes \
if provided; leave as None otherwise.

  3b. EuCtrDraft — CTIS shape. Map:
    - StudyType → trial_type
    - conditions → therapeutic_area (use the first condition's name as \
the therapeutic area if no MeSH SOC is provided)
    - locations → member_state_locations (filter to EU member states by \
ISO code if iso_basket_codes is set; otherwise list all)
    - target_enrollment → split into _eu and _global (assume 100% EU \
unless the user said otherwise)

═════════════════════════════════════════════════════════════════════════
STEP 4 — Registration document
═════════════════════════════════════════════════════════════════════════

Assemble the final RegistrationDocument:
  - intake, core, ctgov, euctr (all from prior turns)
  - background_paragraph (1-2 paragraphs of disease-area context using \
web_search / wikipedia, NO PMID citations)
  - references (URLs from the web_search / wikipedia results)

Set `is_final=True` ONLY when the user types "Finalize registration". \
On refinement requests, keep `is_final=False` and return a refreshed \
document.

═════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES
═════════════════════════════════════════════════════════════════════════

- NEVER fabricate an NCT ID, EudraCT number, or CTIS trial ID. The \
registries assign these on submission.
- NEVER fabricate a sponsor protocol code unless the user provided it.
- NEVER cite primary literature with PMIDs. The background paragraph \
cites disease-area context only.
- Field placeholders use the literal string '[SPONSOR INPUT]'.
- If the user is registering a SR or MA, redirect them to sr_protocol — \
this specialist is for prospective interventional / observational \
trials.
"""


_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "web_search": frozenset({"registration_document", "ctgov_draft", "euctr_draft"}),
    "wikipedia": frozenset({"registration_document", "ctgov_draft", "euctr_draft"}),
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
            "registration_drafter tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in RegistrationTurn.__args__[0].__args__  # discriminated union
]


_agent: Agent[AgentDeps, RegistrationTurn] | None = None


def build_agent() -> Agent[AgentDeps, RegistrationTurn]:
    agent: Agent[AgentDeps, RegistrationTurn] = Agent(
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
        "registration_drafter specialist built — %d tools registered",
        len(specialist_tools),
    )
    return agent


def _get_agent() -> Agent[AgentDeps, RegistrationTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
    deps: AgentDeps | None = None,
) -> tuple[RegistrationTurn, dict[str, Any]]:
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="registration_drafter",
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
