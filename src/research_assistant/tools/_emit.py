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


def _call_signature(tool: str, args: dict[str, Any]) -> str:
    """Canonical `(tool, args)` key for the identical-call short-circuit."""
    return f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"


def _register_tool_error(deps: AgentDeps, tool: str) -> str:
    """Count one genuine tool failure against the per-tool + global budgets.

    Returns a note to append to the result (empty when no breaker state
    changed) so the model learns about a disable the moment it happens.
    """
    deps.error_count += 1
    per_tool = deps.tool_error_counts.get(tool, 0) + 1
    deps.tool_error_counts[tool] = per_tool

    notes: list[str] = []
    if per_tool >= deps.max_errors_per_tool and tool not in deps.disabled_tools:
        deps.disabled_tools.add(tool)
        notes.append(
            f"NOTE: '{tool}' has failed {per_tool} times and is now disabled "
            f"for the rest of this turn. Do not call it again — use a "
            f"different tool or work with what you already have."
        )
    if deps.error_count >= deps.max_tool_errors and not deps.all_tools_disabled:
        deps.all_tools_disabled = True
        notes.append(
            f"NOTE: this turn's tool-error budget ({deps.max_tool_errors} "
            f"failures) is exhausted. ALL tools are now disabled for the rest "
            f"of this turn. Stop calling tools and answer the user with the "
            f"information you already gathered, stating clearly what you "
            f"could not retrieve."
        )
    return "\n\n".join(notes)


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
    """Emit `tool_start`, await `impl()`, emit `tool_end`, return the result.

    Also the enforcement point for the per-turn tool circuit breaker (see
    ``AgentDeps``): identical-call short-circuit, per-tool disable after
    repeated failures, and global degradation once the error budget is
    spent. Every model-initiated call attempt emits events — including
    short-circuited ones — so the Usage page reflects what the model did.
    """
    deps = ctx.deps
    await deps.event_queue.put(
        {
            "type": "tool_start",
            "tool": tool,
            "icon": icon,
            "args": args,
            "description": description,
        }
    )

    async def _finish(result: str) -> str:
        preview_text = preview(result) if preview else truncate(result, preview_len)
        await deps.event_queue.put(
            {
                "type": "tool_end",
                "tool": tool,
                "result_preview": preview_text,
            }
        )
        return result

    # Layer 3 backstop — the budget died, all tools are disabled, and the
    # model was told to answer. A few more attempts are tolerated (it may
    # already have a call queued); past that, abort the run.
    if deps.all_tools_disabled:
        deps.post_disable_calls += 1
        if deps.post_disable_calls > deps.max_post_disable_calls:
            raise ToolErrorBudgetExceeded(
                f"Aborted after {deps.error_count} failed tool calls in one "
                f"turn (budget {deps.max_tool_errors}): the model kept "
                f"attempting tool calls ('{tool}') after all tools were "
                f"disabled instead of producing an answer."
            )
        return await _finish(
            "ALL TOOLS ARE DISABLED for the rest of this turn — the "
            "tool-error budget is exhausted. Do not call any more tools. "
            "Answer the user now with the information you already have, "
            "stating clearly what you could not retrieve."
        )

    # Layer 2 — this specific tool failed repeatedly and is disabled.
    if tool in deps.disabled_tools:
        return await _finish(
            f"Tool '{tool}' is disabled for the rest of this turn after "
            f"{deps.tool_error_counts.get(tool, 0)} failures. Do not call it "
            f"again — use a different tool or work with what you already have."
        )

    # Layer 1 — identical (tool, args) repeat: serve the cached result
    # instead of re-executing. Changing the observation ("you already did
    # this") is what breaks a repeat loop; re-running the call verbatim
    # just feeds it. Persistent repeats start burning the error budget.
    sig = _call_signature(tool, args)
    if sig in deps.call_results:
        repeats = deps.call_repeats.get(sig, 0) + 1
        deps.call_repeats[sig] = repeats
        note = ""
        if repeats >= 2:
            # 3rd+ identical attempt — the nudge isn't landing; treat each
            # further repeat as a failure so the breaker escalates.
            note = _register_tool_error(deps, tool)
        header = (
            f"[REPEATED CALL] You already called '{tool}' with these exact "
            f"arguments this turn; the result has not changed and is repeated "
            f"below. Do not make this call again — use this result, change "
            f"the arguments, or try a different approach."
        )
        body = deps.call_results[sig]
        return await _finish(f"{header}\n\n{body}" + (f"\n\n{note}" if note else ""))

    result = await impl()
    deps.call_results[sig] = result

    # Error accounting: count genuine {"error": ...} results and surface any
    # breaker escalation to the model inline with the failing result.
    if _is_error_result(result):
        note = _register_tool_error(deps, tool)
        if note:
            result = f"{result}\n\n{note}"

    return await _finish(result)
