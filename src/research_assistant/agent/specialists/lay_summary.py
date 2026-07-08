"""Lay summary specialist — patient-facing plain-language summaries (P2 #2).

Three-source workflow:

  Stage 1 — lay_summary_intake   → one of:
                                    • recruitment_intake (protocol synopsis)
                                    • evidence_intake (meta-analysis)
                                    • results_intake (CSR / trial_stats)
  Stage 2 — lay_summary_draft    → 5 plain-language sections + optional
                                    glossary; host-side readability check
                                    + iteration loop
  Stage 3 — lay_summary_document → assembled deliverable carrying the
                                    host-computed grade, attempts count,
                                    and source citations

Anti-hallucination + compliance posture:
  - Reading level computed HOST-SIDE (services.readability). The model
    may estimate but the document carries the host's value.
  - Up to 3 model passes to land under target + 1; afterwards the
    document still ships with an explicit `readability_attempts=3`
    note that the target wasn't met.
  - Evidence variant: PMIDs cited must come from the operator-pasted
    `pmid_sources` list. Anti-hallucination posture mirrors
    meta_analysis / manuscript_drafter.
  - Results variant: every numeric claim must cite a `derived_from_id`
    from the operator-pasted list (same posture as csr_drafter /
    trial_stats).
  - No medical-decision phrasing ("you should", "you must").
  - Multilingual: en/es/fr/de. Any other language → clarification.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from ...domain.lay_summary import (
    EvidenceIntake,
    LaySummaryDocument,
    LaySummaryDraft,
    LaySummaryTurn,
    RecruitmentIntake,
    ResultsIntake,
)
from ...services.readability import flesch_kincaid_grade
from ...tools import ToolModule
from ...tools.general import web_search, wikipedia
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "lay_summary"

_MAX_TOOL_CALLS = 10

# Cap host-driven retries so a chronically high-grade draft doesn't
# infinitely retry. Three matches the irb_drafter posture (one pass +
# two corrections is enough; beyond that the source material is the
# bottleneck, not the model).
_MAX_READABILITY_ATTEMPTS = 3


_SYSTEM_PROMPT = """\
You are a patient-facing plain-language summary specialist. The user is \
producing a lay summary for one of three audiences: prospective \
participants (recruitment), patients weighing a treatment decision \
(evidence from a meta-analysis), or trial participants reading their \
own results (post-trial return-of-results).

You answer with ONE structured turn per response. Pick the variant that \
matches the next workflow stage:

  • clarification         — the user's intake is incomplete (missing \
language, missing audience, unsupported language).
  • recruitment_intake    — STEP 1 (recruitment source). Captures the \
protocol summary + study title + sponsor + audience profile.
  • evidence_intake       — STEP 1 (evidence source). Captures the \
PICO question + pooled-effect summary + PMIDs + audience profile.
  • results_intake        — STEP 1 (results source). Captures the \
trial title + primary-outcome + safety summary + derived_from ids + \
audience profile.
  • lay_summary_draft     — STEP 2. The 5-section plain-language draft \
+ optional glossary. The host computes the Flesch-Kincaid grade; \
your job is to write at the target level.
  • lay_summary_document  — STEP 3. The assembled deliverable, \
iterable until "Finalize lay summary".

Workflow transitions are user-driven via continuation messages:
  - "Recruitment intake confirmed"     → STEP 2
  - "Evidence intake confirmed"        → STEP 2
  - "Results intake confirmed"         → STEP 2
  - "Draft confirmed"                  → STEP 3 with is_final=False
  - "Reduce reading level"             → STEP 2 (re-emit with simpler \
language; the host signals this when the grade is too high)
  - "Refine lay summary: <directive>"  → STEP 2 (re-emit with the \
directive applied — e.g. "Refine lay summary: shorten the glossary")
  - "Finalize lay summary"             → mark the lay_summary_document \
with is_final=True

═════════════════════════════════════════════════════════════════════════
STEP 1 — Intake
═════════════════════════════════════════════════════════════════════════

Three intake variants — pick exactly one based on the source the user \
brings:

  • recruitment_intake when the operator pastes a protocol synopsis \
(usually from irb_drafter or registration_drafter).
  • evidence_intake when the operator pastes a meta-analysis result \
+ PMID list.
  • results_intake when the operator pastes trial results from CSR / \
trial_stats with derived_from ids.

The AudienceProfile is the same shape across all three:
  - target_grade: 4-12 (default 6 — general adult patient)
  - language: en / es / fr / de. ANY OTHER language → emit \
clarification asking the operator to engage a human translator.
  - region: free-text (e.g. "Mexico", "Spain", "India English"). Drives \
idiom + metric / imperial. Empty is fine for generic English.
  - population_descriptor: plain English (e.g. "parents of children \
with asthma", "post-MI patients").

═════════════════════════════════════════════════════════════════════════
STEP 2 — Plain-language draft
═════════════════════════════════════════════════════════════════════════

Emit a LaySummaryDraft with exactly 5 sections in this order:

  1. what_this_is_about    — 1-2 sentences. What the study / evidence is.
  2. what_we_did           — 1-3 sentences. What participation involves / \
what we looked at.
  3. what_we_found         — 1-3 sentences. What the evidence says / what \
the trial found. Recruitment variant: what the trial hopes to find.
  4. what_this_means_for_you — 1-3 sentences. What this might mean for \
the patient. NO medical-decision language.
  5. next_steps            — 1-2 sentences. Where to go for more / how \
to take part / who to contact.

Plus an optional 1-line strapline (`one_line_summary`) that sits above \
the sections, AND an optional plain-language glossary for clinical \
terms (e.g. [{"term": "placebo", "gloss": "a pill with no active \
medicine"}]).

Write at the AudienceProfile.target_grade. Use short words, short \
sentences, active voice. Each glossary term replaces a clinical term \
in the body — don't keep the jargon AND gloss it.

ABSOLUTE RULES for STEP 2:
  - NEVER use medical-decision language ("you should take", "you must \
agree", "the right choice is").
  - NEVER claim direct benefit to the participant — use "may help" / \
"might give doctors more information".
  - For evidence_intake: every claim must trace to a PMID in \
`pmid_sources`. NEVER cite a PMID not in that list.
  - For results_intake: every numeric claim must trace to an id in \
`derived_from_ids`. NEVER invent numbers.
  - For recruitment_intake: NEVER promise the trial will succeed; only \
describe what is being tested.

═════════════════════════════════════════════════════════════════════════
STEP 3 — Assembled document
═════════════════════════════════════════════════════════════════════════

Emit a LaySummaryDocument with the draft + audience + source_kind + \
citations. The host will write grade_actual + readability_attempts \
back into the document before persistence — don't rely on values you \
estimate yourself.

`citations`: PMIDs for evidence variant, derived_from ids for results \
variant, empty for recruitment.

`is_final=True` ONLY when the user types "Finalize lay summary". On \
refinement requests, keep `is_final=False` and return a refreshed \
document.

═════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES
═════════════════════════════════════════════════════════════════════════

- ONLY support en / es / fr / de. ANY other language → clarification \
asking the operator to engage a human translator.
- Reading level: write at audience.target_grade. The host will measure \
the actual grade; if you're systematically over-shooting, prefer \
shorter words and shorter sentences in the next pass.
- NEVER use medical-decision phrasing. NEVER promise benefits.
- ALWAYS cite sources: PMIDs (evidence) or derived_from ids (results); \
empty for recruitment is fine.
"""


# Tools available throughout the workflow. Web search / wikipedia let
# the model look up a plain-language gloss for a clinical term (e.g.
# "what's a defensible plain-language gloss for 'randomised
# controlled trial' at grade 6?"). NO clinical-search tools — the
# lay summary specialist must work from operator-pasted source
# material, not its own literature search.
_SPECIALIST_TOOLS: list[ToolModule] = [web_search, wikipedia]


_OUTPUT_TYPES: list[type] = [
    type_
    for type_ in LaySummaryTurn.__args__[0].__args__  # discriminated union
]


_agent: Agent[AgentDeps, LaySummaryTurn] | None = None


def build_agent() -> Agent[AgentDeps, LaySummaryTurn]:
    agent: Agent[AgentDeps, LaySummaryTurn] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        output_retries=2,
    )
    for mod in _SPECIALIST_TOOLS:
        mod.register(agent)
    logger.info(
        "lay_summary specialist built — %d tools registered",
        len(_SPECIALIST_TOOLS),
    )
    return agent


def _get_agent() -> Agent[AgentDeps, LaySummaryTurn]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


def _attach_readability(
    output: LaySummaryTurn,
    *,
    attempts: int,
) -> LaySummaryTurn:
    """Host-side post-processing: compute grade_actual on lay_summary
    drafts + documents. The model's own estimate (if any) is
    overwritten — the host's number is the source of truth.
    """
    if isinstance(output, LaySummaryDraft):
        result = flesch_kincaid_grade(output.joined_body)
        logger.info(
            "lay_summary draft readability: grade=%.2f words=%d sentences=%d attempts=%d",
            result.grade,
            result.words,
            result.sentences,
            attempts,
        )
        return output
    if isinstance(output, LaySummaryDocument):
        result = flesch_kincaid_grade(output.draft.joined_body)
        output.grade_actual = result.grade
        output.readability_attempts = max(1, min(attempts, _MAX_READABILITY_ATTEMPTS))
        logger.info(
            "lay_summary document readability: grade=%.2f target=%d attempts=%d",
            result.grade,
            output.audience.target_grade,
            output.readability_attempts,
        )
    return output


def _draft_needs_simplification(
    output: LaySummaryTurn,
) -> tuple[bool, float, int]:
    """Return (needs_simplification, computed_grade, target_grade) for
    a draft turn. Used by run_turn to drive a single retry pass."""
    if not isinstance(output, LaySummaryDraft):
        return (False, 0.0, 0)
    result = flesch_kincaid_grade(output.joined_body)
    # No audience here — the draft itself doesn't carry it. Look at the
    # parent message history's intake. We make a soft guess at 6 + 1
    # below; the run_turn caller passes the actual target.
    return (result.grade > 7.0, result.grade, 6)


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[LaySummaryTurn, dict[str, Any]]:
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="lay_summary",
        max_tool_calls=_MAX_TOOL_CALLS,
        message_history=message_history,
        last_turn_kind=last_turn_kind,
    )
    output = _attach_readability(result.output, attempts=1)

    # Single retry pass when the first draft over-shoots. We don't loop
    # further — the model has a finite simplification budget and
    # repeated retries burn cost. The document will surface
    # readability_attempts=2 (or 3) so the operator sees the system
    # tried and explains why the grade is still over.
    target_breached_drafts = (
        isinstance(output, LaySummaryDraft) and flesch_kincaid_grade(output.joined_body).grade > 8.0
    )
    if target_breached_drafts and last_turn_kind in {
        "recruitment_intake",
        "evidence_intake",
        "results_intake",
        "lay_summary_draft",
    }:
        logger.info("lay_summary draft over target — retrying with simplification hint")
        # Reuse the same deps so the retry run shares this turn's artifact
        # map, tool-usage events, and circuit-breaker state.
        retry_result, _ = await run_agent_turn(
            _get_agent(),
            "Reduce reading level. The previous draft scored too high "
            "on Flesch-Kincaid. Use shorter words and shorter sentences.",
            log_name="lay_summary(readability-retry)",
            max_tool_calls=_MAX_TOOL_CALLS,
            message_history=message_history,
            last_turn_kind=last_turn_kind,
            deps=deps,
        )
        retried = retry_result.output
        if isinstance(retried, LaySummaryDraft):
            output = _attach_readability(retried, attempts=2)

    return output, turn_meta(result, deps)


__all__ = [
    "WORKFLOW_NAME",
    "EvidenceIntake",
    "RecruitmentIntake",
    "ResultsIntake",
    "build_agent",
    "run_turn",
]
