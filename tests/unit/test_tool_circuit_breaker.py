"""Circuit-breaker v2 behaviour in tools/_emit.emit_run.

Three escalating layers (see AgentDeps):
  1. identical-call short-circuit — repeat (tool, args) served from cache
  2. per-tool disable after max_errors_per_tool failures
  3. global degradation once error_count reaches max_tool_errors, with
     ToolErrorBudgetExceeded only as the keep-calling backstop
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from research_assistant.agent.deps import AgentDeps, ToolErrorBudgetExceeded
from research_assistant.tools._emit import emit_run

_ERROR_RESULT = '{"error": "upstream 500"}'
_OK_RESULT = '{"count": 3, "results": ["a", "b", "c"]}'
_NOT_AVAILABLE = '{"available": false}'


def _ctx() -> Any:
    """emit_run only touches ctx.deps — a namespace stub is enough."""
    return SimpleNamespace(deps=AgentDeps())


class _Impl:
    """Callable impl that records how many times it actually ran."""

    def __init__(self, result: str = _OK_RESULT) -> None:
        self.result = result
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return self.result


async def _run(ctx: Any, impl: _Impl, tool: str = "web_search", **args: Any) -> str:
    return await emit_run(
        ctx,
        tool=tool,
        icon="🔎",
        args=args,
        description="test call",
        impl=impl,
    )


# ── Layer 1: identical-call short-circuit ────────────────────────────────


async def test_identical_call_served_from_cache() -> None:
    ctx = _ctx()
    impl = _Impl()
    first = await _run(ctx, impl, query="statins")
    second = await _run(ctx, impl, query="statins")

    assert impl.calls == 1  # second call must NOT re-execute
    assert first == _OK_RESULT
    assert "REPEATED CALL" in second
    assert _OK_RESULT in second  # cached result still available to the model


async def test_different_args_execute_normally() -> None:
    ctx = _ctx()
    impl = _Impl()
    await _run(ctx, impl, query="statins")
    result = await _run(ctx, impl, query="DOACs")

    assert impl.calls == 2
    assert "REPEATED CALL" not in result


async def test_third_identical_attempt_burns_error_budget() -> None:
    ctx = _ctx()
    impl = _Impl()
    await _run(ctx, impl, query="statins")  # executes
    await _run(ctx, impl, query="statins")  # cached, free nudge
    assert ctx.deps.error_count == 0
    await _run(ctx, impl, query="statins")  # 3rd attempt — counts as failure
    assert ctx.deps.error_count == 1
    assert impl.calls == 1


# ── Layer 2: per-tool disable ────────────────────────────────────────────


async def test_tool_disabled_after_per_tool_threshold() -> None:
    ctx = _ctx()
    impl = _Impl(result=_ERROR_RESULT)
    # 3 distinct failing calls to the same tool
    for i in range(3):
        result = await _run(ctx, impl, query=f"q{i}")
    assert "disabled" in result  # 3rd failure carries the disable note
    assert "web_search" in ctx.deps.disabled_tools

    blocked = await _run(ctx, impl, query="q-next")
    assert impl.calls == 3  # 4th call never executed
    assert "disabled for the rest of this turn" in blocked


async def test_other_tools_keep_working_after_one_tool_disabled() -> None:
    ctx = _ctx()
    failing = _Impl(result=_ERROR_RESULT)
    for i in range(3):
        await _run(ctx, failing, tool="fetch_pmc_fulltext", pmid=str(i))
    assert "fetch_pmc_fulltext" in ctx.deps.disabled_tools

    healthy = _Impl()
    result = await _run(ctx, healthy, tool="web_search", query="statins")
    assert healthy.calls == 1
    assert result.startswith(_OK_RESULT)


async def test_not_available_does_not_count_as_error() -> None:
    ctx = _ctx()
    impl = _Impl(result=_NOT_AVAILABLE)
    for i in range(4):
        await _run(ctx, impl, pmid=str(i))
    assert ctx.deps.error_count == 0
    assert not ctx.deps.disabled_tools


# ── Layer 3: global degradation + backstop ───────────────────────────────


async def _exhaust_global_budget(ctx: Any) -> None:
    """5 failures spread over tools (2+2+1) so no per-tool disable hides them."""
    for tool, n in (("tool_a", 2), ("tool_b", 2), ("tool_c", 1)):
        impl = _Impl(result=_ERROR_RESULT)
        for i in range(n):
            await _run(ctx, impl, tool=tool, q=f"{tool}-{i}")


async def test_global_budget_soft_disables_all_tools() -> None:
    ctx = _ctx()
    await _exhaust_global_budget(ctx)
    assert ctx.deps.all_tools_disabled

    # Turn is NOT aborted — the model gets told to answer instead.
    impl = _Impl()
    result = await _run(ctx, impl, tool="tool_d", q="anything")
    assert impl.calls == 0
    assert "ALL TOOLS ARE DISABLED" in result


async def test_backstop_raises_after_post_disable_allowance() -> None:
    ctx = _ctx()
    await _exhaust_global_budget(ctx)
    impl = _Impl()
    for i in range(ctx.deps.max_post_disable_calls):
        await _run(ctx, impl, tool="tool_d", q=f"retry-{i}")
    with pytest.raises(ToolErrorBudgetExceeded):
        await _run(ctx, impl, tool="tool_d", q="one-too-many")
    assert impl.calls == 0


async def test_budget_exhaustion_note_appended_to_failing_result() -> None:
    ctx = _ctx()
    ctx.deps.error_count = 4  # one failure away from the budget
    impl = _Impl(result=_ERROR_RESULT)
    result = await _run(ctx, impl, q="final straw")
    assert ctx.deps.all_tools_disabled
    assert "tool-error budget" in result


# ── Events still emitted for every attempt ───────────────────────────────


async def test_short_circuited_calls_still_emit_events() -> None:
    ctx = _ctx()
    impl = _Impl()
    await _run(ctx, impl, query="statins")
    await _run(ctx, impl, query="statins")  # cached
    events = []
    while not ctx.deps.event_queue.empty():
        events.append(ctx.deps.event_queue.get_nowait())
    starts = [e for e in events if e["type"] == "tool_start"]
    ends = [e for e in events if e["type"] == "tool_end"]
    assert len(starts) == 2
    assert len(ends) == 2
