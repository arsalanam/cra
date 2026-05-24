"""mesh_lookup agent tool — thin wrapper over `PubmedSource.lookup_mesh`."""

from __future__ import annotations

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from .._emit import emit_run
from .sources import get_pubmed_source


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def mesh_lookup(ctx: RunContext[AgentDeps], term: str) -> str:
        """
        Look up the canonical MeSH (Medical Subject Headings) descriptor for a
        clinical concept and return its entry terms — the synonyms PubMed
        indexes records under. Use this BEFORE constructing a PubMed search
        string so you can OR all synonyms for each PICO concept.

        Returns JSON: { term, results: [{ uid, descriptor, entry_terms,
        record_type, scope_note }] }.

        Example: term='proton pump inhibitor' returns descriptor
        'Proton Pump Inhibitors' with entry_terms like 'Omeprazole',
        'Lansoprazole', 'Pantoprazole'. Use those as `[tiab]` fallbacks in
        your boolean query.
        """
        source = await get_pubmed_source()
        if source is None:
            import json

            return json.dumps(
                {
                    "error": (
                        "PubMed source is disabled. Enable it in the admin UI "
                        "(/admin.html) or update the source_configs table."
                    ),
                }
            )
        return await emit_run(
            ctx,
            tool="mesh_lookup",
            icon="🔎",
            args={"term": term},
            description=f"Looking up MeSH: {term}",
            impl=lambda: source.lookup_mesh(term),
        )
