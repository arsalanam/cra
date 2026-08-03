"""Clinical research tools — multi-source paper search, MeSH lookup, PMC full text.

Each module exposes `register(agent)` and is appended to the meta-analysis
specialist's tool list. Backend logic for the search tool lives in
`tools/clinical/sources/`, behind a shared `PaperSource` protocol. Adding
a new paper source does not change the agent's tool surface — it's a new
class wired into `sources/registry.py` and seeded as a `SourceConfig` row.
"""

from . import fetch_pmc_fulltext, mesh_lookup, search_papers

__all__ = ["fetch_pmc_fulltext", "mesh_lookup", "search_papers"]
