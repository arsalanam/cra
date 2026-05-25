"""
python_repl tool — lightly sandboxed Python execution.

TEACHING NOTE: This gives the agent an escape hatch for complex data
processing. For production, use a proper sandbox (e2b, modal.com, etc.)
"""

from __future__ import annotations

import asyncio
import builtins as _builtins
import io
import json
import math
import traceback
from contextlib import redirect_stdout
from typing import Any

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from .._emit import emit_run

_DENY = {"open", "exec", "eval", "compile", "input", "breakpoint", "exit", "quit"}
_ALLOWED_IMPORTS = {
    "math",
    "json",
    "random",
    "statistics",
    "itertools",
    "functools",
    "collections",
    "re",
    "datetime",
    "decimal",
    "fractions",
    "string",
}


def _build_namespace() -> dict[str, Any]:
    safe_builtins = {k: v for k, v in vars(_builtins).items() if k not in _DENY}
    real_import = _builtins.__import__

    def guarded_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        root = name.split(".")[0]
        if root not in _ALLOWED_IMPORTS:
            raise ImportError(
                f"Import of {name!r} is not allowed. Allowed: {sorted(_ALLOWED_IMPORTS)}"
            )
        return real_import(name, globals, locals, fromlist, level)

    safe_builtins["__import__"] = guarded_import

    return {
        "__builtins__": safe_builtins,
        "__name__": "__main__",
        "math": math,
        "json": json,
    }


async def _impl(code: str) -> str:
    """Execute `code` in a sandboxed namespace and return stdout (or error)."""
    namespace = _build_namespace()
    stdout_buffer = io.StringIO()

    def _exec() -> None:
        with redirect_stdout(stdout_buffer):
            exec(code, namespace)  # noqa: S102 — sandboxed via guarded builtins

    try:
        await asyncio.to_thread(_exec)
        output = stdout_buffer.getvalue()
        if output:
            return f"Output:\n{output.rstrip()}"
        return "Code executed successfully (no output). Add print() to see results."
    except Exception:
        tb = traceback.format_exc(limit=5)
        return f"Execution error:\n{tb}"


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def python_repl(ctx: RunContext[AgentDeps], code: str) -> str:
        """
        Execute Python code and return the output. Great for: data processing,
        algorithms, list/dict manipulation, statistical calculations, anything
        requiring loops or complex logic. Always use print() to show results.
        Allowed imports: math, json (pre-imported). No file I/O or network access.
        """
        return await emit_run(
            ctx,
            tool="python_repl",
            icon="🐍",
            args={"code": code},
            description="Running Python code...",
            impl=lambda: _impl(code),
        )
