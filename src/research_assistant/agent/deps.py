"""
deps.py
────────────────────────────────────────────────────────────────────────────
TEACHING NOTE: RunContext[T] lets every tool access shared per-run state.
Here we pass an asyncio.Queue so tools can emit SSE events to the frontend.
This is the cleanest way to get real-time tool events — no global state needed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


class ToolErrorBudgetExceeded(RuntimeError):
    """Raised when a single turn accumulates too many failed tool calls.

    Tools report failure by returning a JSON result with a truthy ``error``
    key (they don't raise). The shared emit wrapper (tools/_emit.py) counts
    those; once the per-turn budget (``AgentDeps.max_tool_errors``) is reached
    it raises this to abort the run — so a repeatedly-failing tool (an
    unreachable source, erroring fetches) can't burn the whole 5-min /
    100-tool-call budget. Normal ``{"available": false}`` results are NOT
    errors and don't count. Surfaced to the user as a graceful message by
    web/dispatch._classify_agent_error.
    """


def drain_tool_usage(deps: AgentDeps) -> dict[str, int]:
    """Drain `deps.event_queue` and count `tool_start` events by tool name.

    Used at the end of every specialist run to pull a per-tool invocation
    count out of the in-memory event queue. The queue was originally for
    SSE streaming; the dispatcher refactor dropped the SSE consumer but
    the tools still emit start/end events, so this is the cleanest place
    to recover a per-tool breakdown for the Usage page.
    """
    counts: dict[str, int] = {}
    while True:
        try:
            evt = deps.event_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(evt, dict) and evt.get("type") == "tool_start":
            name = str(evt.get("tool") or "unknown")
            counts[name] = counts.get(name, 0) + 1
    return counts


@dataclass
class AgentDeps:
    """
    Shared state passed to every tool via RunContext[AgentDeps].

    event_queue: Tools push events here; the SSE endpoint reads from it.
    artifacts:   Maps filename → data-URI for binary outputs (e.g., forest plots
                 from sandbox_exec). The clinical endpoint's post-processor
                 substitutes these URIs into MetaAnalysisResults so the frontend
                 can render images inline.
    file_content: Uploaded file content (None if no file was uploaded).
    file_name:    Original filename for format detection.
    """

    event_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    artifacts: dict[str, str] = field(default_factory=dict)
    file_content: str | None = None
    file_name: str = "upload.txt"
    # Per-turn tool-error circuit breaker. Tools return {"error": ...} on
    # failure (they don't raise); the emit wrapper counts those and aborts the
    # run once error_count reaches max_tool_errors — so a repeatedly-failing
    # tool can't consume the whole 5-min / 100-call budget. A "not available"
    # result (e.g. paywalled full text) is not an error and doesn't count.
    error_count: int = 0
    max_tool_errors: int = 5
    # Most recent assistant turn `kind` (e.g. "pico", "search_results"). None
    # before any assistant turn exists. Used by the clinical agent's
    # `prepare_tools` to gate pubmed_search / fetch_pmc_fulltext / sandbox_exec
    # behind the appropriate workflow stage.
    last_turn_kind: str | None = None
