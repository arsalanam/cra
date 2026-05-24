"""Common interface for paper-source backends.

Each source (PubMed, Scholar, Embase, …) implements `PaperSource` so the
agent-facing tools (`pubmed_search`, future `search_papers`) can be source-
agnostic. Source-specific extensions (PubMed's MeSH lookup, PMC full-text)
live as additional methods on the concrete class.
"""

from __future__ import annotations

from typing import Protocol


class PaperSource(Protocol):
    """Protocol every paper-source backend must satisfy."""

    name: str  # short id used in logs and the future admin UI ("pubmed", …)

    async def search(self, query: str, max_results: int) -> str:
        """Run a search and return a JSON string.

        Implementations decide their own result shape but must include at
        minimum: a `studies` array with `pmid` (or source-specific id),
        `doi`, `title`, `journal`, `year`, `authors`, `abstract`,
        `publication_types`, `mesh_headings`. The clinical tool layer
        re-emits this verbatim to the agent.
        """
        ...
