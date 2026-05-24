"""fetch_pmc_fulltext agent tool — thin wrapper over `PubmedSource.fetch_pmc_fulltext`."""

from __future__ import annotations

import json
import logging

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...persistence.library import cache_fulltext
from .._emit import emit_run, truncate
from .sources import get_pubmed_source

logger = logging.getLogger(__name__)


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def fetch_pmc_fulltext(ctx: RunContext[AgentDeps], pmid: str) -> str:
        """
        Try to fetch the open-access full text from PubMed Central for a given
        PMID. Most journals are NOT open access — expect this to return
        `available: false` for ~70% of clinical trials.

        Use ONLY when the abstract from `pubmed_search` lacks numbers needed
        for meta-analysis (event counts, means / SDs, sample sizes per arm).
        When unavailable, set `extraction_source: "abstract"` with
        `is_complete: false` and explain in `extraction_notes` what's missing
        — the user will fetch the PDF themselves and paste the values in.

        Returns JSON: { pmid, pmc_id, available, body?, truncated?, url? }.
        Body is capped at ~60k characters; if `truncated: true` you have the
        intro/methods/results — usually enough for extraction.
        """
        source = await get_pubmed_source()
        if source is None:
            return json.dumps(
                {
                    "error": (
                        "PubMed source is disabled — PMC full text needs the PubMed "
                        "backend enabled. Update source_configs or the admin UI."
                    ),
                    "pmid": pmid,
                }
            )

        async def _fetch_and_cache() -> str:
            raw = await source.fetch_pmc_fulltext(pmid)
            # Write the body through to the local cache when one came back.
            # Best-effort: never let a cache hiccup change the tool result.
            try:
                payload = json.loads(raw)
                if payload.get("available") and payload.get("body"):
                    await cache_fulltext(pmid, payload["body"])
            except Exception:
                logger.warning("Caching PMC full text for %s failed", pmid, exc_info=True)
            return raw

        return await emit_run(
            ctx,
            tool="fetch_pmc_fulltext",
            icon="📄",
            args={"pmid": pmid},
            description=f"Fetching PMC full text for PMID {truncate(pmid, 20)}",
            impl=_fetch_and_cache,
        )
