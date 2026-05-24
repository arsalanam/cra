"""Tool registry, organised by category.

Each subpackage exposes a list of registrable tool modules. Specialists
compose the subset they want by selecting from these constants:

    from ..tools import GENERAL_TOOLS, CLINICAL_TOOLS, DATA_SCIENCE_TOOLS

    for mod in GENERAL_TOOLS + CLINICAL_TOOLS + DATA_SCIENCE_TOOLS:
        mod.register(agent)

The legacy `TOOLS` constant (everything in one list) is preserved for
backward compatibility while the dispatcher refactor is in flight.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from .clinical import fetch_pmc_fulltext, mesh_lookup, search_papers
from .data_science import calculator, python_repl, sandbox_exec
from .general import describe_image, fetch_document, read_file, web_search, wikipedia

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from ..agent.deps import AgentDeps


class ToolModule(Protocol):
    """Each tool module exposes a `register(agent)` function."""

    # Any output type — both the streaming agent (output=str) and structured
    # specialists register the same tool modules.
    def register(self, agent: Agent[AgentDeps, Any]) -> None: ...


GENERAL_TOOLS: list[ToolModule] = [
    web_search,
    wikipedia,
    fetch_document,
    read_file,
    describe_image,
]

CLINICAL_TOOLS: list[ToolModule] = [
    search_papers,
    mesh_lookup,
    fetch_pmc_fulltext,
]

DATA_SCIENCE_TOOLS: list[ToolModule] = [
    calculator,
    python_repl,
    sandbox_exec,
]

# Legacy aggregate — kept for any callers that haven't migrated yet.
TOOLS: list[ToolModule] = CLINICAL_TOOLS + DATA_SCIENCE_TOOLS + GENERAL_TOOLS


__all__ = [
    "CLINICAL_TOOLS",
    "DATA_SCIENCE_TOOLS",
    "GENERAL_TOOLS",
    "TOOLS",
    "ToolModule",
]
