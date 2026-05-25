"""
calculator tool — safe arithmetic evaluation.

TEACHING NOTE: Never use eval() directly with user input. We parse with the
ast module and only allow whitelisted node types and function names.
"""

from __future__ import annotations

import ast
import asyncio
import math

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from .._emit import emit_run

_SAFE_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Num,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.FloorDiv,
        ast.USub,
        ast.UAdd,
        ast.Call,
        ast.Name,
        ast.Load,
    }
)

_SAFE_NAMES: dict[str, object] = {
    "pi": math.pi,
    "e": math.e,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "ceil": math.ceil,
    "floor": math.floor,
    "abs": abs,
    "round": round,
    "pow": pow,
}


def _safe_calculate(expression: str) -> str:
    """Safely evaluate a mathematical expression using an AST whitelist."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")

        for node in ast.walk(tree):
            if type(node) not in _SAFE_NODES:
                return f"Unsafe expression: {type(node).__name__} not allowed."
            if isinstance(node, ast.Name) and node.id not in _SAFE_NAMES:
                return f"Unknown name: {node.id!r}. Allowed: {list(_SAFE_NAMES.keys())}"
            if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
                return "Only simple function calls allowed."

        result = eval(  # noqa: S307 — intentionally safe via AST whitelist
            compile(tree, "<calc>", "eval"),
            {"__builtins__": {}},
            _SAFE_NAMES,
        )

        if isinstance(result, float):
            if result == int(result):
                return str(int(result))
            return f"{result:.10g}"
        return str(result)

    except SyntaxError as e:
        return f"Syntax error in expression: {e}"
    except ZeroDivisionError:
        return "Error: Division by zero."
    except Exception as e:
        return f"Calculation error: {e}"


async def _impl(expression: str) -> str:
    """Async wrapper — arithmetic is CPU-trivial, but we keep the API uniform."""
    return await asyncio.to_thread(_safe_calculate, expression)


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def calculator(ctx: RunContext[AgentDeps], expression: str) -> str:
        """
        Evaluate a mathematical expression safely. Supports: +, -, *, /, **, %,
        //, and math functions: sqrt, log, log10, sin, cos, tan, ceil, floor, abs,
        round, pow. Constants: pi, e. Example: "2 * pi * (6371 + 408) / 92 * 60"
        """
        return await emit_run(
            ctx,
            tool="calculator",
            icon="🔢",
            args={"expression": expression},
            description=f"Computing: {expression}",
            impl=lambda: _impl(expression),
            preview=lambda r: f"{expression} = {r}",
        )
