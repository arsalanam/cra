"""General Q&A specialist.

Handles non-clinical questions, conversational exchanges, and meta-questions
about the tool itself ("what is PICO?", "how does this assistant work?").
The dispatcher routes anything that doesn't match a workflow keyword to
this specialist.

Tool subset: `GENERAL_TOOLS` plus the two lightweight data-science tools
(`calculator`, `python_repl`). The clinical tool set and `sandbox_exec`
are deliberately NOT registered — clinical tools keep this specialist
from drifting into evidence synthesis, and `sandbox_exec`'s Docker
dependency is overkill for general Q&A. `python_repl` exists here
specifically so math-heavy questions (subset-sums, iterative search,
list aggregations) can be done in one tool call instead of dozens of
`calculator` round-trips that bloat context and burn input tokens.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ModelMessage

from ...domain.common import Answer, ClarificationRequest
from ...tools import GENERAL_TOOLS, describe_image, fetch_document
from ...tools.clinical import rag_search
from ...tools.data_science import calculator, python_repl
from ..deps import AgentDeps
from ..model import build_bedrock_model
from ._runner import run_agent_turn, turn_meta

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "general_qa"

# Exploration-heavy: web research, definitional lookups, math iterations.
# Highest cap of any specialist because the question shape is open-ended.
_MAX_TOOL_CALLS = 80


_SYSTEM_PROMPT = """\
You are a general research assistant. The dispatcher routed this turn to \
you because the user's message is non-clinical, conversational, or a \
meta-question about this tool.

Respond with one of:

  • answer        — short, direct, factual response
  • clarification — only if the question is genuinely ambiguous

Default to `answer`. Use `clarification` sparingly.

You can call `web_search` or `wikipedia` for facts you don't already \
know. When you cite something from those tools, populate `Answer.references` \
with the URLs.

TOOL CHOICE — math and iteration:

For ANY math beyond a single quick arithmetic expression, prefer \
`python_repl` over chained `calculator` calls. Each tool call is a \
separate Bedrock round-trip that re-sends the whole conversation \
context — 30 `calculator` additions cost roughly 30× the input tokens \
of one `python_repl` script doing the same work, and growing context \
can blow the per-turn token budget.

Use `python_repl` when the question involves:
  • Combinations, subsets, or subset-sum (e.g. "which states' GDPs sum \
    to X")
  • Iterative search, optimization, or "find the closest"
  • Summing, averaging, sorting, or filtering more than ~5 values
  • Any loop you would otherwise unroll into repeated `calculator` calls

Use `calculator` ONLY for a single arithmetic expression you would type \
into a phone calculator (e.g. "what's 17% of 4200?").

LOCAL LIBRARY SEARCH — `rag_search`:

`rag_search` searches the user's LOCAL library of papers they have already \
fetched (cached abstracts + full text). Use it when the user asks what \
their library / saved papers cover, or to ground an answer in evidence \
they have already collected — NOT the live PubMed API.

The results are real cached passages, so you MAY reference them. But the \
clinical-synthesis rule below STILL APPLIES to your prose. Therefore:
  • Keep `Answer.text` qualitative — describe what the library contains \
    ("several RCTs on X report a benefit") WITHOUT quoting effect sizes, \
    CIs, p-values, or PMIDs in the prose.
  • Put the specific citations in `Answer.references` instead, e.g. \
    "PMID 12345678 — <title> (<journal>, <year>)". The references list is \
    the right place for PMIDs and titles.
  • If the user wants pooled effect sizes / a rigorous synthesis, redirect \
    them to the meta-analysis workflow.

ABSOLUTE RULE — clinical synthesis is forbidden in this specialist:

If the user asks "what does the literature say about X?" or wants \
specific clinical effect sizes, the dispatcher will route them to the \
meta-analysis specialist on the NEXT turn. For THIS turn, give a brief \
honest answer that does not quote effect sizes, p-values, confidence \
intervals, PMIDs, or guideline recommendations from your training data.

If you find yourself wanting to write "OR 0.5", "95% CI", "p<0.05", \
"PMID …", "ESC guideline", or "Smith et al 2023" — STOP. Instead say: \
"For a rigorous evidence review of this question, start a clinical \
meta-analysis workflow by asking it as a research question."

Definitional answers about clinical concepts (e.g. "what is an odds \
ratio?", "explain a forest plot") are fine — those don't make any \
specific claim about the literature.

RETRY BUDGET — read carefully:

If the validator rejects your answer for clinical synthesis, you get \
exactly ONE retry. On that retry, do NOT rephrase the same claim with \
different wording — the regex patterns will trip again and the entire \
turn will fail. Instead:

  • Drop the offending content ENTIRELY (no effect sizes, no CIs, no \
    p-values, no PMIDs, no guideline citations, no author-year refs).
  • Replace it with a one-sentence redirect to the meta-analysis \
    workflow.
  • Keep any background / definitional content that did not trigger \
    the validator.

Rephrasing "OR = 0.5" as "an odds ratio of about 0.5" will fail. \
Rephrasing it as "the effect size has been studied in randomized \
trials" will also fail (still synthesis). Just don't quote it.
"""


# Patterns that indicate the agent is asserting concrete clinical claims
# from training. Strict regex — false positives would just push the user
# back to general_qa retry, which costs model requests.
_CLINICAL_RED_FLAGS: tuple[re.Pattern[str], ...] = (
    # Effect-size shorthand with explicit operator or decimal.
    re.compile(r"\b(?:OR|RR|HR|MD|SMD)\s*[=:]\s*-?\d"),
    re.compile(r"\b(?:OR|RR|HR|MD|SMD)\s+of\s+-?\d"),
    re.compile(r"\b(?:OR|RR|HR|MD|SMD)\s+\d+\.\d"),
    # 95% CI / confidence interval — only a CONCRETE NUMERIC claim, not the
    # bare concept. Explaining what a forest plot / CI *is* must be allowed
    # (those answers unavoidably say "confidence interval"); quoting a specific
    # interval like "95% CI 0.34-0.58" is what's forbidden. So require an
    # adjacent number.
    re.compile(r"\b95\s*%\s*CI\b[\s:]*[\[(]?\s*-?\d", re.I),
    re.compile(r"\bconfidence interval\b(?:\s+(?:of|from|is|was))?[\s:]*[\[(]?\s*-?\d", re.I),
    # I² with operator + digit.
    re.compile(r"\bI[²2]\s*[=:]\s*\d"),
    # p-value with operator + decimal.
    re.compile(r"\bp\s*[<=>]\s*0?\.\d", re.I),
    # PMID citation.
    re.compile(r"\bPMID[:\s]*\d", re.I),
    # Large sample-size assertions (n = 8524).
    re.compile(r"\bn\s*[=~]\s*\d{3,}\b", re.I),
    # Numbered RCT citations from memory.
    re.compile(r"\b\d+\s+(?:landmark\s+)?RCTs?\b"),
    # Guideline-grade recommendations.
    re.compile(r"\bguidelines? recommend\b", re.I),
    re.compile(r"\b(ESC|ACC|AHA|NICE|USPSTF)\s+guideline", re.I),
    # Author-year citations (Smith et al 2023, (Saeed 2025)).
    re.compile(r"\b[A-Z][a-z]+\s+et\s+al\.?\s*,?\s*(?:19|20)\d{2}\b"),
    re.compile(r"\(\s*[A-Z][a-z]+\s+(?:19|20)\d{2}\s*\)"),
)


def _looks_like_clinical_synthesis(text: str) -> str | None:
    for pat in _CLINICAL_RED_FLAGS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


_OUTPUT_TYPES: list[type] = [Answer, ClarificationRequest]


_agent: Agent[AgentDeps, Answer | ClarificationRequest] | None = None


def build_agent() -> Agent[AgentDeps, Answer | ClarificationRequest]:
    agent: Agent[AgentDeps, Answer | ClarificationRequest] = Agent(
        model=build_bedrock_model(),
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
        output_type=_OUTPUT_TYPES,
        retries=2,
        # output_retries=1 caps a validator-rejected turn at 2 Bedrock calls
        # (initial + 1 retry). The _reject_clinical_synthesis validator can
        # otherwise triple the cost of one unlucky turn.
        output_retries=1,
    )

    # GENERAL_TOOLS (minus the web-resource "fishing" tools) + the two
    # lightweight math tools (no sandbox_exec — see module docstring) +
    # rag_search over the library. general_qa is a TEXT Q&A specialist.
    #
    # fetch_document AND describe_image are EXCLUDED: web_search (Tavily)
    # already returns an AI summary + per-result extracted page content, so
    # the model answers from that + its own knowledge + wikipedia. Left in,
    # the model chases result URLs / forest-plot images off the web — usually
    # paywalled or bot-blocked (403), DNS-dead, or (for images) requiring the
    # vision model — which wastes tool calls and trips the error budget.
    _EXCLUDED = (fetch_document, describe_image)
    general_tools = [m for m in GENERAL_TOOLS if m not in _EXCLUDED]
    tools = [*general_tools, calculator, python_repl, rag_search]
    for tool_module in tools:
        tool_module.register(agent)

    @agent.output_validator
    def _reject_clinical_synthesis(
        output: Answer | ClarificationRequest,
    ) -> Answer | ClarificationRequest:
        if isinstance(output, Answer):
            match = _looks_like_clinical_synthesis(output.text)
            if match:
                logger.warning(
                    "General-QA Answer rejected — clinical synthesis (%r)",
                    match,
                )
                raise ModelRetry(
                    f"Your answer contains clinical synthesis ({match!r}). "
                    "Quoting effect sizes, p-values, CIs, PMIDs or "
                    "guideline citations from your training data is "
                    "forbidden in the general_qa specialist. Tell the "
                    "user that a rigorous answer requires running the "
                    "clinical meta-analysis workflow, and rewrite the "
                    "answer without the offending phrasing."
                )
        return output

    logger.info(
        "General-QA specialist built — %d tools registered",
        len(tools),
    )
    return agent


def _get_agent() -> Agent[AgentDeps, Answer | ClarificationRequest]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_turn(
    user_message: str,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
) -> tuple[Answer | ClarificationRequest, dict[str, Any]]:
    """Run one general-QA turn.

    `last_turn_kind` is accepted for interface symmetry with other
    specialists but isn't used here — general_qa has no multi-step
    workflow to gate.
    """
    result, deps = await run_agent_turn(
        _get_agent(),
        user_message,
        log_name="General-QA",
        max_tool_calls=_MAX_TOOL_CALLS,
        message_history=message_history,
        last_turn_kind=last_turn_kind,
    )
    return result.output, turn_meta(result, deps)
