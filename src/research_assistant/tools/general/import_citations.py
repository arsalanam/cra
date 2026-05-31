"""import_citations tool — parse BibTeX/RIS and surface to the agent.

Registered on the manuscript_drafter and sr_protocol specialists so the
agent can ingest references mid-draft when the operator pastes a
BibTeX / RIS dump (or drops a file via the frontend).
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...services.citations import detect_format, parse
from .._emit import emit_run

logger = logging.getLogger(__name__)


CitationFormat = Literal["bibtex", "ris", "auto"]


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def import_citations(
        ctx: RunContext[AgentDeps],
        format: CitationFormat,
        content: str,
    ) -> str:
        """
        Parse a BibTeX or RIS dump pasted by the operator + return the
        canonical citation list as JSON.

        `format`:
          • "bibtex" — explicit BibTeX (.bib) input.
          • "ris"    — explicit RIS (.ris) input. Endnote and Mendeley
                        export to this by default.
          • "auto"   — sniff from content (works ~99% of the time for
                        non-trivial inputs).

        `content` is the raw file text (≤ 1 MB practical limit;
        agents should refuse larger inputs).

        Returns JSON: `{"format_detected": "bibtex"|"ris",
        "count": <int>, "citations": [{cite_key, entry_type, title,
        authors, year, journal, volume, issue, pages, publisher,
        doi, pmid, url, abstract}, ...]}`.

        Use the returned citations to populate references in the
        manuscript / sr_protocol turn — but ONLY cite references that
        actually appeared in the parsed list (no fabrication).
        """

        async def _impl() -> str:
            try:
                if format == "auto":
                    detected = detect_format(content)
                    if detected is None:
                        return json.dumps(
                            {
                                "error": (
                                    "Could not detect citation format. "
                                    "Try format=bibtex or format=ris."
                                )
                            }
                        )
                else:
                    detected = format
                citations = parse(content, fmt=detected)
            except ValueError as e:
                return json.dumps({"error": str(e)})
            except Exception as e:
                logger.exception("import_citations failed")
                return json.dumps({"error": f"{type(e).__name__}: {e}"})
            return json.dumps(
                {
                    "format_detected": detected,
                    "count": len(citations),
                    "citations": [c.to_dict() for c in citations],
                }
            )

        return await emit_run(
            ctx,
            tool="import_citations",
            icon="📚",
            args={"format": format, "n_chars": len(content)},
            description=f"Parsing {format} citation dump",
            impl=_impl,
        )


__all__ = ["register"]
