"""Local publication cache (R0).

The foundation of the clinical library: everything `search_papers` and
`fetch_pmc_fulltext` retrieve is persisted here (metadata + abstracts in
Postgres, raw full text in a content-addressable blob store) so later phases
can chunk, embed (R2), and semantically retrieve (R3) cached evidence.

Public surface:
- `cache_search_results` / `cache_fulltext` — fire-and-forget write-through
  hooks the tools call.
- `publication_id` — content-addressable identity, also used for the
  `cached` flag in search results.
"""

from __future__ import annotations

from .repository import (
    delete_publication,
    get_publication_detail,
    library_stats,
    list_publications,
    publication_id,
)
from .writethrough import cache_fulltext, cache_search_results, cache_uploaded_document

__all__ = [
    "cache_fulltext",
    "cache_search_results",
    "cache_uploaded_document",
    "delete_publication",
    "get_publication_detail",
    "library_stats",
    "list_publications",
    "publication_id",
]
