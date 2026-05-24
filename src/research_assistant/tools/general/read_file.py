"""
read_file tool — parse uploaded text / CSV / JSON files.
"""

from __future__ import annotations

import asyncio
import json

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from .._emit import emit_run


def _parse(content: str, filename: str) -> str:
    """Parse uploaded file content (text, CSV, or JSON) and return a preview."""
    try:
        if filename.endswith(".json") or content.strip().startswith("{"):
            data = json.loads(content)
            preview = json.dumps(data, indent=2)[:1000]
            return f"JSON file parsed successfully:\n{preview}\n..."

        if filename.endswith(".csv") or "," in content[:200]:
            lines = content.strip().split("\n")
            preview_lines = lines[:10]
            row_count = len(lines) - 1
            return (
                f"CSV file with {row_count} data rows.\n"
                f"Preview (first 10 lines):\n" + "\n".join(preview_lines)
            )

        word_count = len(content.split())
        preview = content[:1000]
        return (
            f"Text file ({word_count} words).\n"
            f"Content preview:\n{preview}"
            + ("..." if len(content) > 1000 else "")
        )

    except Exception as e:
        return f"File parsing error: {e}"


async def _impl(content: str, filename: str = "uploaded_file") -> str:
    """Async wrapper around the pure parser (uniform API with other tools)."""
    return await asyncio.to_thread(_parse, content, filename)


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def read_file(ctx: RunContext[AgentDeps], filename: str) -> str:
        """
        Read and parse the uploaded file. Supports .txt, .csv, and .json formats.
        Returns the file content with format-specific parsing. Use ONLY when the
        user has uploaded a file — check before calling.
        """
        if ctx.deps.file_content is None:
            return "No file was uploaded. This tool is only available when a file is attached."

        return await emit_run(
            ctx,
            tool="read_file",
            icon="📄",
            args={"filename": ctx.deps.file_name},
            description=f"Reading file: {ctx.deps.file_name}",
            impl=lambda: _impl(ctx.deps.file_content or "", ctx.deps.file_name),
            preview_len=300,
        )
