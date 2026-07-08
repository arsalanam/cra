"""LLM fallback classifier for unmatched first turns.

Step 9 of the agent-loop review (docs/agent-loop-review.md): the regex
cascade in `dispatcher.classify_route` is the fast path, but a first-turn
message with no keyword signal ("I want to pool the outcomes from several
trials of statins") lands on rule="default" → general_qa. When the
`dispatcher_llm_fallback_enabled` setting is on, the dispatcher consults
this module for those turns only: one cheap structured Bedrock call (the
session model — Haiku by default) picks a workflow from the catalogue.

Fail-open by design: ANY failure (timeout, throttle, malformed output)
returns None and the dispatcher keeps the regex default, so this path is
never worse than the pre-step-9 behaviour. It is also never consulted for
slash commands, continuations, handoffs, pinned threads, or keyword
matches — only genuine no-signal turns.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ..config import get_settings
from .model import build_bedrock_model

logger = logging.getLogger(__name__)

# Mirrors agent/specialists/__init__.SPECIALISTS. A Literal (not a runtime
# derivation) so the model's output schema enumerates the choices and
# pydantic rejects anything else at the tool-call layer.
WorkflowChoice = Literal[
    "meta_analysis",
    "nma",
    "ipd",
    "search_strategy",
    "sr_protocol",
    "risk_of_bias",
    "sap_drafter",
    "trial_stats",
    "registration_drafter",
    "irb_drafter",
    "csr_drafter",
    "grade_drafter",
    "manuscript_drafter",
    "lay_summary",
    "general_qa",
]


class WorkflowClassification(BaseModel):
    """The single specialist workflow best suited to the user's message."""

    workflow: WorkflowChoice = Field(
        description="The workflow that should handle this message. "
        "Use general_qa when no specialist clearly fits."
    )


_SYSTEM_PROMPT = """\
You route a clinical-research user's FIRST message to the correct \
specialist workflow. Output only the classification.

Workflow catalogue:

- meta_analysis — pairwise evidence synthesis: "does X improve Y?", \
pooled effect sizes, PICO research questions, two-arm comparisons
- nma — network meta-analysis: comparing or ranking THREE OR MORE \
interventions, league tables, indirect comparisons
- ipd — individual-patient-data meta-analysis: pooling subject-level / \
patient-level data across trials
- search_strategy — building literature search queries (PubMed / Embase \
/ boolean strings / MeSH terms)
- sr_protocol — systematic-review protocols (PRISMA-P, PROSPERO)
- risk_of_bias — RoB 2 / ROBINS-I / Newcastle-Ottawa quality assessment \
of studies
- sap_drafter — prospective trial design: sample size, power \
calculations, statistical analysis plans
- trial_stats — post-lock trial analyses: Kaplan-Meier, Cox PH, MMRM, \
subgroup forests, ITT vs PP
- registration_drafter — trial registration records (ClinicalTrials.gov \
PRS, EU CTR / CTIS)
- irb_drafter — IRB / ethics-committee packets, protocol synopses, \
informed-consent forms
- csr_drafter — ICH E3 clinical study reports
- grade_drafter — GRADE certainty ratings, Summary-of-Findings tables, \
PRISMA 2020 checklists
- manuscript_drafter — journal manuscripts (IMRaD), reviewer responses, \
cover letters
- lay_summary — plain-language / patient-facing summaries
- general_qa — everything else: definitions, conversation, general \
knowledge, questions about this tool

Rules:
- Pick exactly one workflow.
- Prefer general_qa when unsure — it is the safe default and the user \
can always be routed onward next turn.
- Definitional or "explain …" questions are general_qa even when they \
mention clinical concepts.
"""


_agent: Agent[None, WorkflowClassification] | None = None


def _get_agent() -> Agent[None, WorkflowClassification]:
    global _agent
    if _agent is None:
        _agent = Agent(
            model=build_bedrock_model(),
            system_prompt=_SYSTEM_PROMPT,
            output_type=WorkflowClassification,
            retries=1,
        )
    return _agent


async def classify_with_llm(user_message: str) -> str | None:
    """Return the workflow id the LLM picks, or None on any failure.

    Callers treat None as "keep the regex default" — this function must
    never raise.
    """
    settings = get_settings()
    try:
        result = await asyncio.wait_for(
            _get_agent().run(
                user_message[:2000],
                usage_limits=UsageLimits(request_limit=2),
            ),
            timeout=settings.dispatcher_llm_fallback_timeout_seconds,
        )
    except Exception:
        logger.warning(
            "LLM fallback classifier failed — keeping regex default",
            exc_info=True,
        )
        return None
    return result.output.workflow
