"""CSR (Clinical Study Report, ICH E3) drafter specialist.

Four-stage workflow:

  Stage 1 — csr_intake        → study identity, sponsor, blinding,
                                  lock-date, target jurisdictions
  Stage 2 — csr_synopsis       → 1-2 page synopsis (regulator-readable)
  Stage 3 — csr_data_sections  → disposition / demographics / efficacy /
                                  safety populated from ADSL+ADTTE+TLF
  Stage 4 — csr_document       → assembled CSR, iterable until Finalize.
                                  Narrative sections (introduction /
                                  discussion / conclusions) ship as
                                  `[Operator to complete]` placeholders
                                  this slice — next slice drafts them.

This is the most anti-hallucination-sensitive specialist in the
codebase: a CSR is the regulator-facing document that pivots a
sponsor's submission. Inventing patient counts or effect sizes is
not merely incorrect — it's professional misconduct.

Posture:
  - Patient counts MUST come from a paste from ADSL or a TLF table.
    The schema's `derived_from` field captures the source artefact
    id (e.g. "TLF t-disposition") so the regulator audit-trail
    follows.
  - Effect sizes / p-values MUST cite a specific TLF or ADTTE row.
  - Sponsor PHI (investigator names, IRB approval numbers, IND
    number) stays `[SPONSOR INPUT]` rather than fabricated.
  - Background references in the synopsis cite design / regulatory
    guidance only — no PMIDs.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition

from ...domain.csr import CsrTurn
from ...tools import ToolModule
from ...tools.general import web_search, wikipedia
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "csr_drafter"

_MAX_TOOL_CALLS = 20


_SYSTEM_PROMPT = """\
You are a clinical-trial regulatory writer. The user is preparing a \
Clinical Study Report (ICH E3) to submit to the FDA / EMA / equivalent. \
Your job is to draft the synopsis + the four data-driven sections \
(Disposition / Demographics / Efficacy / Safety) and assemble them into \
a CSR document. Narrative sections (Introduction / Discussion / Overall \
Conclusions) are out of scope for this slice and will appear as \
[Operator to complete] placeholders in the assembled document.

You answer with ONE structured turn per response. Pick the variant that \
matches the next workflow stage:

  • clarification         — the user's intake is incomplete or \
ambiguous (e.g. they didn't say which blinding model was used).
  • csr_intake            — STEP 1. Study identity, sponsor, blinding \
state, lock date, target jurisdictions.
  • csr_synopsis          — STEP 2. The 1-2 page regulator-readable \
synopsis (objectives + methods + N + primary endpoint + result + \
direction + safety overview + conclusions).
  • csr_data_sections     — STEP 3. All four data sections together \
(disposition table + demographics table + efficacy results + safety \
overview). EVERY count + EVERY effect size MUST cite the source \
artefact via `derived_from`.
  • csr_document          — STEP 4. The assembled CSR, iterable until \
the user types Finalize.

Workflow transitions are user-driven via continuation messages:
  - "CSR intake confirmed"           → STEP 2
  - "CSR synopsis confirmed"         → STEP 3
  - "CSR data sections confirmed"    → STEP 4 (is_final=False)
  - "Refine CSR: <directive>"        → return the current document refreshed
  - "Finalize CSR"                   → STEP 4 with is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — Intake
═════════════════════════════════════════════════════════════════════════

Elicit a complete CsrIntake:
  - study_id, study_name, sponsor
  - blinding (open_label / single_blind / double_blind / triple_blind)
  - lock_date (the E7 study-lock event date, if known)
  - target_jurisdictions (FDA / EMA / PMDA / Health Canada / etc)

If the user doesn't know the lock_date, leave it as None — don't \
fabricate. If the user describes a trial that hasn't been locked yet, \
emit `clarification` reminding them the CSR is post-lock work.

═════════════════════════════════════════════════════════════════════════
STEP 2 — Synopsis
═════════════════════════════════════════════════════════════════════════

Build the CsrSynopsis. EVERY count must come from a paste the user \
has provided OR a TLF reference already in the conversation. \
Specifically:

  - number_planned: from the registration intake or SAP sample-size \
result
  - number_analysed_safety + number_analysed_efficacy: from ADSL \
SAFFL='Y' / ITTFL='Y' counts (use the operator's paste; do NOT invent)
  - primary_endpoint: copy from the SAP or registration core_fields
  - primary_result_description: 2-3 sentences in plain English. MUST \
cite the source artefact in `derived_from`. NEVER fabricate effect-size \
numbers, point estimates, CIs, or p-values inline.
  - primary_result_direction: favours_intervention / favours_comparator \
/ no_difference / inconclusive — drawn from the operator's stated \
interpretation, not the agent's recall.
  - safety_overview: 2-4 sentences summarising AE/SAE/death counts \
from the AE-summary TLF.

If the user can't yet name a result (data still being cleaned), emit \
`clarification` rather than inventing.

═════════════════════════════════════════════════════════════════════════
STEP 3 — Data sections
═════════════════════════════════════════════════════════════════════════

Emit one csr_data_sections turn covering all four sections:

  • disposition           — rows from TLF t-disposition (ITT / SAF / \
death). EVERY row carries an integer n + a "%" string. Set \
disposition.derived_from = "TLF t-disposition".

  • demographics          — rows from TLF t-demographics (age summary, \
sex/race counts, treatment-group breakdown). \
demographics.derived_from = "TLF t-demographics".

  • efficacy              — primary_endpoint_text + the TLF reference \
for the primary endpoint (e.g. "TLF t-tte-summary" or "ADTTE PARAMCD=" \
followed by the parameter code, or "TrialStats t-km-OS" / "TrialStats \
mmrm-CHGFBL-WK24" / "TrialStats binary-ORR" / "TrialStats subgroup-OS-by-SEX" \
when the trial-stats specialist has previously emitted analyses). \
secondary_endpoints + their references listed in parallel arrays. \
populations_analysed names which ADaM flags the analysis used \
(ITT / PP / Safety).

  • safety                — total_ae_events / subjects_with_any_ae / \
total_saes / deaths / discontinuations_due_to_ae taken from the AE \
summary table. top_aes_text MUST cite the AE-frequency figure \
(typically "TLF f-ae-frequency"). \
safety.derived_from = "TLF t-ae-summary".

If any required count is missing, emit clarification asking the user \
to paste the relevant ADSL/ADTTE/TLF table.

═════════════════════════════════════════════════════════════════════════
STEP 4 — Document
═════════════════════════════════════════════════════════════════════════

Assemble the final CsrDocument. Narrative fields (background_text, \
discussion_text, conclusions_text) keep their default \
"[Operator to complete]" placeholders this slice. Set is_final=True \
ONLY when the user types "Finalize CSR".

═════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES
═════════════════════════════════════════════════════════════════════════

- NEVER invent patient counts, ARM sizes, randomisation totals, AE \
frequencies, effect sizes, point estimates, confidence intervals, or \
p-values. EVERY number traces back to a TLF or ADSL/ADTTE paste the \
user provided.
- NEVER cite primary clinical literature with PMIDs. The synopsis \
cites design guidance (ICH E3, ICH E9) only.
- NEVER fabricate investigator names, IRB approval numbers, IND \
numbers — leave as [SPONSOR INPUT].
- When a required count is missing, emit clarification rather than \
guessing.
- The narrative sections (Introduction, Discussion, Conclusions) \
ship as [Operator to complete] placeholders this slice. Do NOT draft \
them.
"""


_TOOL_GATES: dict[str, frozenset[str | None]] = {
    "web_search": frozenset({"csr_synopsis", "csr_document"}),
    "wikipedia": frozenset({"csr_synopsis", "csr_document"}),
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
            "csr_drafter tool gate at stage=%r — hiding %s",
            last,
            ", ".join(hidden),
        )
    return allowed


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in CsrTurn.__args__[0].__args__  # discriminated union
]


_agent: Agent[AgentDeps, CsrTurn] | None = None


def build_agent() -> Agent[AgentDeps, CsrTurn]:
    agent: Agent[AgentDeps, CsrTurn] = Agent(
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
    logger.info("csr_drafter specialist built — %d tools registered", len(specialist_tools))
    return agent


def _get_agent() -> Agent[AgentDeps, CsrTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[CsrTurn, dict[str, Any]]:
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="csr_drafter",
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
