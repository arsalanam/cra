"""eCRF design specialist (E3) — drafts CRFs from a study protocol.

Takes free-text protocol content and returns a `StudyDraft`: a set of
CDASH-aligned CRF `FormDefinition`s plus a visit schedule, for a designer to
review/edit and publish through the normal lifecycle. It never saves or
publishes anything itself — it only proposes a draft.

Unlike the evidence-synthesis specialists, there's no anti-hallucination
concern here: it structures a data-collection instrument, it doesn't cite
literature. Protocol text is metadata (no PHI), so Bedrock use is fine (D6).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ...config import get_settings
from ...domain.ecrf import StudyDraft
from ..model import build_bedrock_model

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "ecrf_design"

_SYSTEM_PROMPT = """\
You are a clinical data manager who designs electronic Case Report Forms \
(eCRFs) from a study protocol. Given protocol text, propose a `StudyDraft`: \
the set of CRFs needed to collect the protocol's data, plus a visit schedule.

Follow these rules:
- Produce one `FormDefinition` per distinct data-collection need (e.g. \
Demographics, Medical History, Vital Signs, Concomitant Medications, the \
primary/secondary efficacy outcomes, Adverse Events). Don't invent forms the \
protocol doesn't imply.
- Use CDASH conventions: standard form/item naming, `cdash_var` set where a \
standard variable applies (e.g. AGE, SEX, VSORRES). Item ids are short, \
lowercase, unique within a form.
- Pick the right `data_type` per item; give categorical items a `code_list` \
and reference it via `code_list_ref`.
- Add `edit_checks` ONLY where clearly warranted (plausible numeric ranges, \
date sanity, simple cross-field rules). Use `severity="soft"` for \
"please confirm" warnings and `severity="hard"` only for truly invalid data. \
Edit-check `expression`s are boolean and must hold for VALID data; you may \
reference item ids and the helpers is_blank(x), present(x), matches(regex,x), \
len(x), abs(x). Allow blanks (e.g. "is_blank(age) or (age >= 0 and age < 120)").
- Build a `visit_schedule` from the protocol's visit structure and map each \
form to the events where it is collected.
- Put any assumptions, ambiguities, or gaps the reviewer should check in \
`notes`. This is a DRAFT for human review — be conservative, not creative.
"""

_agent: Agent[None, StudyDraft] | None = None


def build_agent() -> Agent[None, StudyDraft]:
    agent: Agent[None, StudyDraft] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        output_type=StudyDraft,
        retries=2,
        output_retries=2,
    )
    logger.info("eCRF-design specialist built")
    return agent


def _get_agent() -> Agent[None, StudyDraft]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def draft_from_protocol(
    protocol_text: str, instructions: str | None = None
) -> tuple[StudyDraft, dict[str, Any]]:
    """Draft a study's CRFs from protocol text. Returns (draft, usage meta)."""
    settings = get_settings()
    agent = _get_agent()
    instr = instructions.strip() if instructions else "Draft the core CRFs for this study."
    prompt = f"Protocol:\n{protocol_text.strip()}\n\nInstructions: {instr}"
    result = await asyncio.wait_for(
        agent.run(prompt, usage_limits=UsageLimits(request_limit=settings.max_model_requests)),
        timeout=settings.agent_timeout_seconds,
    )
    usage = result.usage()
    meta = {
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "requests": usage.requests,
        }
    }
    return result.output, meta
