"""Watch-triage agent — invoked by the scheduler, not by the dispatcher.

Unlike the user-facing specialists, this agent does NOT participate in the
dispatcher's classify/run_turn cycle. It runs in the background as part of
the scheduled watch_runner loop:

  for each new PMID since last run:
      → triage(pico, paper) → PaperTriage
  → significance_summary(triages) → WatchRunSummary

No tools. The agent works entirely from passed-in text (PICO + per-paper
title/abstract). This keeps it cheap and predictable — one Bedrock call
per new paper plus one for the summary.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ...config import get_settings
from ...domain.living_review import WatchRunSummary
from ...domain.meta_analysis import PicoTable
from ..deps import AgentDeps
from ..model import build_bedrock_model

logger = logging.getLogger(__name__)


# ── System prompt ────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are a clinical-research triage agent. You evaluate whether newly \
indexed papers are material to a saved literature watch — a PICO + study \
design profile a researcher is monitoring.

You receive:
  • the saved PICO (population, intervention, comparison, outcomes, \
inclusion/exclusion, study_types, age_range)
  • a list of new papers (PMID, title, abstract, journal, year)

For each paper, return a `PaperTriage` with:
  - relevance:   high / moderate / low / off-topic
  - design_fit:  matches / partial / no
  - materiality: 0.0 to 1.0 — probability this paper could change the \
conclusions of a meta-analysis on the saved PICO
  - note:        ONE sentence justification

Materiality anchors:
  0.0  → off-topic or already-superseded; ignore
  0.3  → in-scope but small / underpowered / preliminary
  0.6  → high-quality study (e.g. multicenter RCT) directly addressing the PICO
  0.9  → landmark / practice-changing trial likely to dominate the pooled estimate

Be conservative. The default action when uncertain is materiality 0.4 — \
in-scope but inconclusive — NOT 0.6+. Only assign 0.6+ when the abstract \
gives clear evidence the study is well-powered, well-designed, and \
directly addresses the saved PICO.

After triaging every paper, write `significance_summary` (1–2 sentences):
  - lead with the count of papers above the threshold
  - name the most material finding (citing the paper by author/year if visible)
  - be honest about uncertainty — do NOT inflate impact

Set `notify=true` if at least one triage's materiality >= the watch's \
configured threshold (passed in the user message). Otherwise `notify=false` \
and the runner will skip notification creation.

ABSOLUTE RULES:
- NEVER fabricate findings. Read only what the abstract says.
- NEVER quote effect sizes (OR, RR, HR, MD) unless they appear verbatim \
in the abstract.
- The PMID and title in each PaperTriage MUST match the input — do not \
substitute or invent.
- If the new-papers list is empty, return `triages: []`, \
`significance_summary: "No new papers since last run."`, and \
`notify: false`.
"""


_agent: Agent[AgentDeps, WatchRunSummary] | None = None


def build_agent() -> Agent[AgentDeps, WatchRunSummary]:
    agent: Agent[AgentDeps, WatchRunSummary] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=WatchRunSummary,
        retries=2,
        output_retries=2,
    )
    # No tools — the agent is purely text-in / structured-out.
    logger.info("watch_triage agent built")
    return agent


def _get_agent() -> Agent[AgentDeps, WatchRunSummary]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def triage_run(
    *,
    pico: PicoTable,
    new_papers: list[dict[str, Any]],
    triage_threshold: float,
) -> tuple[WatchRunSummary, dict[str, Any]]:
    """Triage a batch of new papers against a saved PICO.

    `new_papers` is a list of dicts with at least pmid/title/abstract;
    extra fields (journal, year, mesh_headings) are forwarded as context.

    Returns `(summary, usage)`. The runner persists both — usage feeds
    the same monthly-token tracking the rest of the app uses.
    """
    settings = get_settings()
    agent = _get_agent()
    deps = AgentDeps()

    # Triage agent registers no tools — keep the limit explicit and tiny
    # so any future tool addition fails loudly rather than silently
    # inheriting a general-purpose cap.
    usage_limits = UsageLimits(
        request_limit=settings.max_model_requests,
        tool_calls_limit=5,
    )

    # Empty-input short circuit — no point spending a Bedrock call.
    if not new_papers:
        return (
            WatchRunSummary(
                triages=[],
                significance_summary="No new papers since last run.",
                notify=False,
            ),
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "requests": 0,
                "tool_calls": 0,
            },
        )

    user_message = (
        "Triage these new papers against the saved PICO. The watch's "
        f"triage_threshold is {triage_threshold:.2f} — set notify=true only if "
        "at least one paper's materiality >= this threshold.\n\n"
        "## Saved PICO\n"
        f"{pico.model_dump_json(indent=2)}\n\n"
        "## New papers\n"
        f"{json.dumps(new_papers, ensure_ascii=False, indent=2)}"
    )

    logger.info("watch_triage: %d new papers, threshold=%.2f", len(new_papers), triage_threshold)
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
    }
    return result.output, meta
