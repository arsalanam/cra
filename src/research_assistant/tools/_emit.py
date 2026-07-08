"""Shared helpers for tool event emission.

Every tool follows the same pattern: emit a `tool_start` event, run its
implementation, then emit a `tool_end` event with a truncated preview.
This module centralises that boilerplate.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext

from ..agent.deps import AgentDeps, ToolErrorBudgetExceeded


def truncate(text: str, limit: int = 400) -> str:
    """Return text, suffixed with `...` if it exceeds `limit` characters."""
    return text if len(text) <= limit else text[:limit] + "..."


def _is_error_result(result: str) -> bool:
    """True when a tool returned a JSON object carrying a truthy `error` key.

    Tools signal failure by returning `{"error": ...}` (they don't raise).
    Plain-text / markdown results and normal `{"available": false}` PMC
    responses are not errors — only genuine tool failures count toward the
    per-turn error budget.
    """
    stripped = result.lstrip()
    if not stripped.startswith("{"):
        return False
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("error"))


async def emit_run(
    ctx: RunContext[AgentDeps],
    *,
    tool: str,
    icon: str,
    args: dict[str, Any],
    description: str,
    impl: Callable[[], Awaitable[str]],
    preview_len: int = 400,
    preview: Callable[[str], str] | None = None,
) -> str:
    """Emit `tool_start`, await `impl()`, emit `tool_end`, return the result."""
    await ctx.deps.event_queue.put(
        {
            "type": "tool_start",
            "tool": tool,
            "icon": icon,
            "args": args,
            "description": description,
        }
    )

    result = await impl()
    preview_text = preview(result) if preview else truncate(result, preview_len)

    await ctx.deps.event_queue.put(
        {
            "type": "tool_end",
            "tool": tool,
            "result_preview": preview_text,
        }
    )

    # Circuit breaker: a tool that keeps failing (unreachable source, erroring
    # fetches) shouldn't be allowed to grind through the whole 5-min / 100-call
    # budget. Count genuine error results and abort the run once the per-turn
    # budget is spent — dispatch turns this into a graceful message.
    if _is_error_result(result):
        ctx.deps.error_count += 1
        if ctx.deps.error_count >= ctx.deps.max_tool_errors:
            raise ToolErrorBudgetExceeded(
                f"Aborted after {ctx.deps.error_count} failed tool calls in one "
                f"turn (budget {ctx.deps.max_tool_errors}); the most recent "
                f"failure was from '{tool}'."
            )
    return result
