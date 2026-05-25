"""Shared helpers for tool event emission.

Every tool follows the same pattern: emit a `tool_start` event, run its
implementation, then emit a `tool_end` event with a truncated preview.
This module centralises that boilerplate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext

from ..agent.deps import AgentDeps


def truncate(text: str, limit: int = 400) -> str:
    """Return text, suffixed with `...` if it exceeds `limit` characters."""
    return text if len(text) <= limit else text[:limit] + "..."


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
    return result
