"""Shared turn runner for workflow specialists.

Every specialist's ``run_turn`` used to hand-roll the same boilerplate:
build ``AgentDeps``, build ``UsageLimits``, wrap ``agent.run`` in
``asyncio.wait_for``, then assemble the usage/tool-usage meta dict. That
duplication let per-turn policy drift silently between specialists (and
meant any circuit-breaker change had to be edited 15 times).

This module centralises the loop:

- ``run_agent_turn`` — one bounded agent run (request limit, per-specialist
  tool-call cap, wall-clock timeout) returning the raw result plus the deps
  so callers can post-process artifacts.
- ``turn_meta`` — the standard ``{"usage": …, "tool_usage": …}`` meta dict
  persisted into the ``done`` stream event.

Per-specialist knobs stay in the specialist module (``_MAX_TOOL_CALLS``,
``retries=``/``output_retries=`` at agent construction); the runtime policy
lives here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits

from ...config import get_settings
from ..deps import AgentDeps, drain_tool_usage

logger = logging.getLogger(__name__)


async def gate_attachment_tools(
    ctx: RunContext[AgentDeps],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    """Hide vision tools on turns without a user-attached image.

    `describe_image` reads bytes from ``deps.image_content`` (populated by
    /api/turn when the user attaches an image). On turns without an
    attachment the tool is pointless — and historically the URL-based
    variant sent the model fishing for web images — so it's hidden
    entirely. Usable directly as ``prepare_tools`` or composed inside a
    specialist's own gate (see meta_analysis._gate_workflow_tools).
    """
    if ctx.deps.image_content is not None:
        return tool_defs
    return [td for td in tool_defs if td.name != "describe_image"]


async def run_agent_turn[OutputT](
    agent: Agent[AgentDeps, OutputT],
    user_message: str,
    *,
    log_name: str,
    max_tool_calls: int,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
    deps: AgentDeps | None = None,
) -> tuple[AgentRunResult[OutputT], AgentDeps]:
    """Run one bounded specialist turn and return ``(result, deps)``.

    ``last_turn_kind`` drives per-stage tool gating (``prepare_tools``) in
    workflow specialists. ``deps`` may be supplied by callers that need to
    thread state across multiple runs in one turn (e.g. lay_summary's
    readability retry); by default a fresh ``AgentDeps`` is created.
    """
    settings = get_settings()
    if deps is None:
        deps = AgentDeps(last_turn_kind=last_turn_kind)

    usage_limits = UsageLimits(
        request_limit=settings.max_model_requests,
        tool_calls_limit=max_tool_calls,
    )

    history = list(message_history) if message_history else None
    logger.info(
        "%s turn: msg=%r history=%d stage=%r",
        log_name,
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
    return result, deps


def turn_meta(result: AgentRunResult[Any], deps: AgentDeps) -> dict[str, Any]:
    """Build the standard per-turn meta dict from a finished run."""
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
    # T1 spend quota: describe_image's converse call is a separate Bedrock
    # request the main usage() never sees — surface it so the spend ledger
    # can price it against settings.vision_model_id.
    if deps.vision_input_tokens or deps.vision_output_tokens:
        meta["vision_usage"] = {
            "input_tokens": deps.vision_input_tokens,
            "output_tokens": deps.vision_output_tokens,
        }
    return meta
