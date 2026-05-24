"""rag_search — semantic + keyword search over the local publication library.

Retrieves real passages from previously-cached papers (abstracts and
full-text chunks) using hybrid dense+sparse retrieval, with full citation
provenance. Because every hit is genuine fetched text — not training-data
recall — it's safe to surface as grounded evidence.

Per-specialist usage rules live in the specialist prompts (general_qa cites
in `Answer.references` and keeps prose qualitative; meta_analysis uses it for
PICO-stage context only, never as the included-study set).
"""

from __future__ import annotations

import json

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...rag.retrieval import hybrid_search
from .._emit import emit_run, truncate


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def rag_search(ctx: RunContext[AgentDeps], query: str, top_k: int = 8) -> str:
        """
        Search the user's LOCAL library — papers already cached by earlier
        `search_papers` / `fetch_pmc_fulltext` calls — using hybrid semantic
        (vector) + keyword retrieval. Use this to ground answers in evidence
        the user has already collected, or to check what the library already
        covers before running a fresh external search.

        Returns JSON:
          { query,
            results: [{ passage_id, publication_id, section, snippet,
                         pmid, doi, title, journal, year, score }],
            note? }

        Each `snippet` is verbatim cached text with citation metadata — safe
        to cite. An empty `results` list means the library has nothing
        relevant yet (or embeddings are disabled); fall back to telling the
        user, or to `search_papers` where that tool is available. This
        searches ONLY the local cache, never the live PubMed/Europe PMC APIs.
        """

        async def _impl() -> str:
            results = await hybrid_search(query, top_n=top_k)
            payload: dict[str, object] = {
                "query": query,
                "results": [r.as_dict() for r in results],
            }
            if not results:
                payload["note"] = (
                    "No matching passages in the local library "
                    "(it may be empty, still embedding, or embeddings are disabled)."
                )
            return json.dumps(payload, ensure_ascii=False)

        return await emit_run(
            ctx,
            tool="rag_search",
            icon="🔎",
            args={"query": query, "top_k": top_k},
            description=f"Searching local library: {truncate(query, 80)}",
            impl=_impl,
        )
