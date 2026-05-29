"""SR-screening AI-assist agent — pre-classifies abstracts against PICO + criteria.

Not dispatcher-routable — invoked directly by the batch endpoint
`POST /api/sr/projects/{id}/ai-suggest`. Same shape as `watch_triage`:
pure text-in / structured-out, no tools, one Bedrock round-trip per
abstract.

The prediction is persistent ADVICE only — the reviewer always makes
the final call. `AiSuggestion` rows record predictions separately from
the `ScreeningDecision` rows that count.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ...config import get_settings
from ...domain.sr_screening import SrScreeningPrediction
from ..deps import AgentDeps
from ..model import build_bedrock_model

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are a systematic-review screening assistant. You predict whether a \
paper meets the inclusion criteria for a specific SR project, given the \
project's PICO, inclusion criteria, exclusion criteria, and the paper's \
title + abstract.

Your prediction is ADVISORY — a human reviewer always makes the final \
call. Default to caution: when the abstract is ambiguous, return \
`maybe` with a confidence around 0.4, not a confident `include` or \
`exclude`.

Decision rules:
  • `include` — the abstract clearly matches every inclusion criterion \
and no exclusion criterion fires.
  • `exclude` — the abstract clearly fails at least one criterion (e.g. \
wrong population, wrong design, preclinical, duplicate, language). When \
excluding, populate `predicted_reason_code` with the PRIMARY failing \
criterion.
  • `maybe` — the abstract is in-scope but missing detail needed to \
decide (e.g. design type unclear, outcomes not specified). Default for \
ambiguity.

Confidence anchors:
  0.3 — abstract gives little to no signal; reviewer must read in full
  0.5 — partial signal; the prediction is reasonable but contestable
  0.75 — abstract directly states the decisive criterion (e.g. \
"randomised double-blind trial of [intervention] in [population]")
  0.9 — explicit, unambiguous match or mismatch

Rationale rules:
  - One paragraph.
  - Quote or paraphrase the specific phrase(s) from the abstract that \
drove the decision.
  - Reference the criterion you matched / failed by name.
  - DO NOT speculate beyond what the abstract states.

ABSOLUTE RULES:
- NEVER fabricate. Read only what the abstract says.
- NEVER cite effect sizes; this is a screening pass, not a synthesis.
- If the abstract is empty or unintelligible, return `maybe` with \
confidence 0.2 and a rationale explaining the abstract is missing.
- Always populate `predicted_reason_code` when `predicted_decision` is \
`exclude`; leave it null otherwise.
"""


_agent: Agent[AgentDeps, SrScreeningPrediction] | None = None


def build_agent() -> Agent[AgentDeps, SrScreeningPrediction]:
    agent: Agent[AgentDeps, SrScreeningPrediction] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=SrScreeningPrediction,
        retries=2,
        output_retries=2,
    )
    # No tools — the agent is purely text-in / structured-out.
    logger.info("sr_screening_assist agent built")
    return agent


def _get_agent() -> Agent[AgentDeps, SrScreeningPrediction]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def classify_abstract(
    *,
    pico: dict[str, Any] | None,
    inclusion_criteria: list[str],
    exclusion_criteria: list[str],
    paper_title: str,
    paper_abstract: str | None,
) -> tuple[SrScreeningPrediction, dict[str, Any]]:
    """Classify a single abstract; return (prediction, usage)."""
    settings = get_settings()
    agent = _get_agent()
    deps = AgentDeps()
    usage_limits = UsageLimits(
        request_limit=settings.max_model_requests,
        tool_calls_limit=5,
    )
    user_message = (
        "Classify this paper for the SR project. Return a structured "
        "SrScreeningPrediction.\n\n"
        f"## PICO\n{json.dumps(pico or {}, indent=2, ensure_ascii=False)}\n\n"
        "## Inclusion criteria\n"
        + ("\n".join(f"- {c}" for c in inclusion_criteria) or "(none specified)")
        + "\n\n"
        "## Exclusion criteria\n"
        + ("\n".join(f"- {c}" for c in exclusion_criteria) or "(none specified)")
        + "\n\n"
        f"## Paper\nTitle: {paper_title}\n\nAbstract:\n"
        + (paper_abstract or "(no abstract available)")
    )
    result = await asyncio.wait_for(
        agent.run(user_message, deps=deps, usage_limits=usage_limits),
        timeout=settings.agent_timeout_seconds,
    )
    usage = result.usage()
    meta: dict[str, Any] = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.input_tokens + usage.output_tokens,
        "requests": usage.requests,
        "tool_calls": usage.tool_calls,
        "model_id": settings.bedrock_model_id,
    }
    return result.output, meta


__all__ = ["build_agent", "classify_abstract"]
