"""search_papers — multi-source fan-out tool.

Runs every enabled PaperSource in parallel, merges + dedupes the results,
and returns a single JSON envelope to the agent. Replaces the old
`pubmed_search` tool in `CLINICAL_TOOLS`.

Source order matters for dedup: PubMed records have richer Medline
metadata (canonical MeSH descriptors, structured PublicationType list),
so they are placed first in the merged list. `dedupe_studies` keeps the
first occurrence — so a PubMed/Europe PMC duplicate keeps the PubMed
copy.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...persistence.library import cache_search_results, publication_id
from .._emit import emit_run, truncate
from .sources import PaperSource, dedupe_studies, get_enabled_sources

logger = logging.getLogger(__name__)


# Preferred dedup order — PubMed wins ties because Medline metadata is
# richer (canonical MeSH, structured pub types).
_SOURCE_PRIORITY = ["pubmed", "europepmc"]


async def _run_one(source: PaperSource, query: str, max_results: int) -> tuple[str, str]:
    """Run one source; never raise — return (name, raw JSON string)."""
    try:
        raw = await source.search(query, max_results)
        return source.name, raw
    except Exception as e:
        logger.error("Source %r failed: %s", source.name, e, exc_info=True)
        return source.name, json.dumps({"error": f"{source.name} failed: {e}", "studies": []})


async def _fan_out(query: str, max_results: int) -> str:
    sources = await get_enabled_sources()
    if not sources:
        return json.dumps(
            {
                "error": (
                    "No paper sources are enabled. Enable at least one in the "
                    "admin UI (/admin.html) or in the source_configs table."
                ),
                "studies": [],
            }
        )

    raw_pairs = await asyncio.gather(*(_run_one(s, query, max_results) for s in sources))
    by_source: dict[str, str] = dict(raw_pairs)

    # Process in priority order so PubMed records appear before Europe PMC
    # in the pre-dedupe list.
    ordered = [n for n in _SOURCE_PRIORITY if n in by_source] + [
        n for n in by_source if n not in _SOURCE_PRIORITY
    ]

    all_studies: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    errors: list[str] = []
    sources_used: list[str] = []
    for name in ordered:
        raw = by_source[name]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            errors.append(f"{name}: invalid JSON response")
            continue
        if "error" in payload:
            errors.append(f"{name}: {payload['error']}")
            continue
        sources_used.append(name)
        try:
            totals[name] = int(payload.get("total", 0))
        except (TypeError, ValueError):
            totals[name] = 0
        all_studies.extend(payload.get("studies", []) or [])

    deduped = dedupe_studies(all_studies)
    logger.info(
        "search_papers: sources=%s pre-dedupe=%d post=%d",
        sources_used,
        len(all_studies),
        len(deduped),
    )

    # Write-through to the local publication cache, and flag which results
    # were ALREADY in the library before this search. The snapshot is taken
    # inside cache_search_results before upserting, so `cached` reflects the
    # pre-search state. This call never raises — a cache outage degrades to
    # every study reading `cached: false`.
    already_cached = await cache_search_results(deduped)
    for study in deduped:
        study["cached"] = publication_id(study) in already_cached

    envelope: dict[str, Any] = {
        "query": query,
        "sources_used": sources_used,
        "totals_by_source": totals,
        "returned": len(deduped),
        "studies": deduped,
    }
    if errors:
        envelope["errors"] = errors
    return json.dumps(envelope, ensure_ascii=False)


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def search_papers(
        ctx: RunContext[AgentDeps],
        query: str,
        max_results: int = 20,
    ) -> str:
        """
        Search every enabled paper source (PubMed, Europe PMC, …) in parallel
        and return a deduplicated unified result. Use PubMed boolean syntax
        with field tags — Europe PMC understands a close-but-not-identical
        dialect; the query is forwarded verbatim to each source.

            "Proton Pump Inhibitors"[MeSH] AND "Acute Coronary Syndrome"[MeSH]
            AND randomized controlled trial[pt] AND English[la] AND humans[mh]

        Returns JSON:
          { query,
            sources_used: ["pubmed", "europepmc", ...],
            totals_by_source: {pubmed: N, europepmc: M},
            returned: K,
            studies: [{ source, source_id, pmid, doi, title, journal, year,
                         authors, abstract, publication_types, mesh_headings,
                         cached }],
            errors?: [ ... per-source failures, not fatal ... ] }

        Records are deduped across sources by (pmid → doi → source_id), so a
        paper indexed in both PubMed and Europe PMC appears once — the PubMed
        copy is preferred for its richer MeSH metadata.

        `cached: true` means the paper was already in the user's local library
        from an earlier search/fetch (every result is also written through to
        the library on this call). Treat it as a UI/provenance hint, not a
        ranking signal.

        Use this as the SOLE source of truth for study citations. Every PMID
        (or `source_id` when no PMID exists) you reference in a `search_results`
        turn must come from a call to this tool in the current conversation.
        `max_results` is per-source, capped at 50.
        """
        return await emit_run(
            ctx,
            tool="search_papers",
            icon="📚",
            args={"query": query, "max_results": max_results},
            description=f"Searching enabled sources: {truncate(query, 80)}",
            impl=lambda: _fan_out(query, max_results),
        )
