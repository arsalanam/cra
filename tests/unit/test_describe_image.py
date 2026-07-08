"""Unit tests for the upload-only describe_image tool.

The tool reads the user-attached image from AgentDeps (no URL parameter —
the old web-image-fishing path is gone) and is hidden by prepare_tools on
turns without an attachment.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic_ai.tools import ToolDefinition

from research_assistant.agent.deps import AgentDeps
from research_assistant.agent.specialists._runner import gate_attachment_tools
from research_assistant.tools.general.describe_image import format_from_media_type

# ── MIME type → Bedrock format token ─────────────────────────────────────


def test_format_jpeg() -> None:
    assert format_from_media_type("image/jpeg") == "jpeg"


def test_format_jpg_alias() -> None:
    assert format_from_media_type("image/jpg") == "jpeg"


def test_format_png() -> None:
    assert format_from_media_type("image/png") == "png"


def test_format_gif() -> None:
    assert format_from_media_type("image/gif") == "gif"


def test_format_webp() -> None:
    assert format_from_media_type("image/webp") == "webp"


def test_format_with_charset_suffix() -> None:
    assert format_from_media_type("image/jpeg; charset=utf-8") == "jpeg"


def test_format_unknown_returns_none() -> None:
    assert format_from_media_type("application/octet-stream") is None


def test_format_none_returns_none() -> None:
    assert format_from_media_type(None) is None


# ── prepare_tools gate: describe_image hidden without an attachment ──────


def _tool_defs() -> list[ToolDefinition]:
    return [
        ToolDefinition(name="web_search"),
        ToolDefinition(name="describe_image"),
        ToolDefinition(name="calculator"),
    ]


def _ctx(deps: AgentDeps) -> Any:
    return SimpleNamespace(deps=deps)


async def test_gate_hides_describe_image_without_attachment() -> None:
    result = await gate_attachment_tools(_ctx(AgentDeps()), _tool_defs())
    assert [td.name for td in result] == ["web_search", "calculator"]


async def test_gate_keeps_describe_image_with_attachment() -> None:
    deps = AgentDeps(image_content=b"\x89PNG...", image_media_type="image/png")
    result = await gate_attachment_tools(_ctx(deps), _tool_defs())
    assert [td.name for td in result] == ["web_search", "describe_image", "calculator"]


async def test_gate_leaves_other_tools_untouched() -> None:
    defs = [ToolDefinition(name="web_search")]
    result = await gate_attachment_tools(_ctx(AgentDeps()), defs)
    assert result == defs
