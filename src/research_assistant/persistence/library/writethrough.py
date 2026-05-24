"""Fire-and-forget write-through for the publication cache (R0).

Called by `search_papers` (abstracts + metadata) and `fetch_pmc_fulltext`
(raw body). These helpers open their own DB session, swallow + log every
error, and NEVER raise: caching is a side effect and must not break or slow
a search if the database or blob store is unavailable. The whole path is
gated by ``settings.library_cache_enabled``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from ...config import get_settings
from ..database import get_db_session
from ..models import Publication
from .blobstore import store_blob
from .repository import (
    attach_fulltext,
    filter_cached_ids,
    materialize_fulltext_passages,
    publication_id,
    store_abstract_passage,
    upsert_publication,
)

logger = logging.getLogger(__name__)


async def cache_search_results(studies: Sequence[Mapping[str, Any]]) -> set[str]:
    """Persist search hits write-through; return ids ALREADY cached before now.

    The membership snapshot is taken BEFORE upserting so the returned set
    feeds the `cached` flag with the pre-search state (otherwise everything
    would read as freshly cached). On any failure returns an empty set —
    callers then mark every study `cached: false`, which is the safe default.
    """
    if not get_settings().library_cache_enabled or not studies:
        return set()
    try:
        async with get_db_session() as session:
            ids = [publication_id(s) for s in studies]
            already_cached = await filter_cached_ids(session, ids)
            for study in studies:
                pid = await upsert_publication(session, study)
                await store_abstract_passage(session, pid, study.get("abstract"))
        _nudge_embedding()
        return already_cached
    except Exception:
        logger.warning("Publication cache write-through failed; continuing", exc_info=True)
        return set()


async def cache_fulltext(
    pmid: str,
    body: str,
    *,
    source: str = "pubmed",
) -> None:
    """Store a fetched full-text body in the blob store and link it.

    Creates a stub Publication if the full text was fetched without a prior
    search hit (rare, but keeps the cache self-consistent). Never raises.
    """
    if not get_settings().library_cache_enabled or not body or not body.strip():
        return
    try:
        async with get_db_session() as session:
            pid = publication_id({"pmid": pmid, "source": source, "source_id": pmid})
            if await session.get(Publication, pid) is None:
                session.add(
                    Publication(
                        id=pid,
                        pmid=str(pmid),
                        source=source,
                        source_id=str(pmid),
                        title="(full text)",
                    )
                )
                await session.flush()
            sha, rel = store_blob(body.encode("utf-8"))
            await attach_fulltext(session, pid, rel, sha)
            await materialize_fulltext_passages(session, pid, body)
        _nudge_embedding()
    except Exception:
        logger.warning("Full-text cache write-through failed; continuing", exc_info=True)


async def cache_uploaded_document(
    *,
    pdf_bytes: bytes,
    text: str,
    title: str,
    doi: str | None = None,
) -> dict[str, Any]:
    """Ingest a user-uploaded PDF (R1): store blob, chunk text, embed.

    Content-addressable by file SHA-256 (or DOI if supplied), so re-uploading
    the same file dedupes onto one publication. Returns a summary dict. Raises
    on failure (unlike the search write-through paths) so the upload endpoint
    can report errors to the user.
    """
    sha, rel = store_blob(pdf_bytes)
    study: dict[str, Any] = {
        "source": "upload",
        "source_id": sha,  # file SHA — stable identity, dedupes re-uploads
        "title": title,
        "doi": doi,
    }
    pid = publication_id(study)
    async with get_db_session() as session:
        already = await session.get(Publication, pid) is not None
        await upsert_publication(session, study)
        await attach_fulltext(session, pid, rel, sha)
        passages_added = await materialize_fulltext_passages(session, pid, text)
    _nudge_embedding()
    return {
        "publication_id": pid,
        "title": title,
        "doi": doi,
        "already_cached": already,
        "passages_created": passages_added,
    }


def _nudge_embedding() -> None:
    """Fire the background embedding pass (best-effort, never raises).

    Local import keeps the rag package off this module's import path.
    """
    try:
        from ...rag.embed_worker import nudge_embedding

        nudge_embedding()
    except Exception:
        logger.debug("Embedding nudge skipped", exc_info=True)
