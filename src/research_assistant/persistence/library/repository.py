"""Repository for the local publication cache (R0).

Pure async functions over `Publication` / `Passage`, each taking an explicit
`AsyncSession` so they're trivially unit-testable. The fire-and-forget
write-through wrapper the search tools actually call (opens its own session,
never raises) lives in `writethrough.py`.

`publication_id` defines the content-addressable identity used both as the
primary key and for the `cached` flag:

    pmid:<pmid>  →  doi:<lowercased doi>  →  sha256:<hash of source:source_id>

A paper with a PMID always collapses to the same `id` regardless of which
source returned it, which is what makes the cross-source cache coherent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Passage, Publication


def _clean(value: Any) -> str | None:
    """Trim a value to a non-empty string, or None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — refined when R2 embeds."""
    return max(1, len(text) // 4)


def publication_id(study: Mapping[str, Any]) -> str:
    """Compute the content-addressable id for a study dict.

    Priority mirrors cross-source dedupe: PMID, then DOI, then a hash of
    ``source:source_id``. Stable across sources and repeat searches.
    """
    pmid = _clean(study.get("pmid"))
    if pmid:
        return f"pmid:{pmid}"
    doi = _clean(study.get("doi"))
    if doi:
        return f"doi:{doi.lower()}"
    source = _clean(study.get("source")) or "unknown"
    source_id = _clean(study.get("source_id")) or "unknown"
    digest = hashlib.sha256(f"{source}:{source_id}".encode()).hexdigest()
    return f"sha256:{digest}"


async def filter_cached_ids(session: AsyncSession, ids: Iterable[str]) -> set[str]:
    """Return the subset of ``ids`` that already exist in `publications`."""
    wanted = [i for i in ids if i]
    if not wanted:
        return set()
    rows = await session.scalars(select(Publication.id).where(Publication.id.in_(wanted)))
    return set(rows.all())


async def upsert_publication(session: AsyncSession, study: Mapping[str, Any]) -> str:
    """Insert or refresh a Publication from a StudyRecord-shaped dict.

    Idempotent: a repeat call for the same `id` preserves `first_cached_at`
    and only back-fills fields that were previously empty (e.g. an abstract
    that a richer source now provides). Returns the publication id.
    """
    pid = publication_id(study)
    authors = json.dumps(list(study.get("authors") or []), ensure_ascii=False)
    mesh = json.dumps(list(study.get("mesh_headings") or []), ensure_ascii=False)
    pubtypes = json.dumps(list(study.get("publication_types") or []), ensure_ascii=False)

    existing = await session.get(Publication, pid)
    if existing is None:
        session.add(
            Publication(
                id=pid,
                pmid=_clean(study.get("pmid")),
                doi=(_clean(study.get("doi")) or "").lower() or None,
                source=_clean(study.get("source")) or "unknown",
                source_id=_clean(study.get("source_id")),
                title=_clean(study.get("title")) or "(untitled)",
                journal=_clean(study.get("journal")),
                year=_int_or_none(study.get("year")),
                authors_json=authors,
                mesh_terms_json=mesh,
                publication_types_json=pubtypes,
                abstract=_clean(study.get("abstract")),
            )
        )
    else:
        # Back-fill identifiers / abstract if a later source supplies them.
        if not existing.pmid and _clean(study.get("pmid")):
            existing.pmid = _clean(study.get("pmid"))
        if not existing.doi and _clean(study.get("doi")):
            existing.doi = (_clean(study.get("doi")) or "").lower() or None
        if not existing.abstract and _clean(study.get("abstract")):
            existing.abstract = _clean(study.get("abstract"))
        # Prefer non-empty MeSH / pub-type lists (PubMed's are richer).
        if mesh != "[]" and existing.mesh_terms_json in ("[]", "", None):
            existing.mesh_terms_json = mesh
        if pubtypes != "[]" and existing.publication_types_json in ("[]", "", None):
            existing.publication_types_json = pubtypes

    await session.flush()
    return pid


async def store_abstract_passage(
    session: AsyncSession, publication_id_: str, abstract: str | None
) -> None:
    """Store the abstract as a single passage. Idempotent on repeat search."""
    text = _clean(abstract)
    if not text:
        return
    already = await session.scalar(
        select(Passage.id)
        .where(Passage.publication_id == publication_id_, Passage.section == "abstract")
        .limit(1)
    )
    if already:
        return
    session.add(
        Passage(
            publication_id=publication_id_,
            section="abstract",
            ordinal=0,
            text=text,
            token_count=_approx_tokens(text),
        )
    )
    await session.flush()


async def attach_fulltext(
    session: AsyncSession,
    publication_id_: str,
    raw_path: str,
    content_sha256: str,
) -> None:
    """Point a publication at its stored raw full-text blob.

    Chunking the body into section passages is deferred to R2; R0 only
    records that the raw bytes exist on disk.
    """
    pub = await session.get(Publication, publication_id_)
    if pub is None:
        return
    pub.raw_path = raw_path
    pub.content_sha256 = content_sha256
    await session.flush()


async def materialize_fulltext_passages(
    session: AsyncSession, publication_id_: str, body: str
) -> int:
    """Chunk a full-text body into section passages. Returns the count added.

    Idempotent: a no-op if non-abstract passages already exist for the
    publication (so re-fetching the same full text doesn't duplicate rows).
    Ordinals start at 1, leaving 0 for the abstract passage.
    """
    if not body or not body.strip():
        return 0
    already = await session.scalar(
        select(Passage.id)
        .where(Passage.publication_id == publication_id_, Passage.section != "abstract")
        .limit(1)
    )
    if already:
        return 0

    # Local import: keeps the (boto3-pulling) rag package out of this module's
    # import path, since chunking itself is pure.
    from ...rag.chunking import chunk_text

    chunks = chunk_text(body)
    for i, ch in enumerate(chunks, start=1):
        session.add(
            Passage(
                publication_id=publication_id_,
                section=ch.section,
                ordinal=i,
                text=ch.text,
                token_count=ch.token_count,
            )
        )
    await session.flush()
    return len(chunks)


# ── Read queries for the library UI (R4) ──────────────────────────────────────


async def library_stats(session: AsyncSession) -> dict[str, Any]:
    """Aggregate counts for the library dashboard."""
    pubs = await session.scalar(select(func.count()).select_from(Publication)) or 0
    passages = await session.scalar(select(func.count()).select_from(Passage)) or 0
    embedded = (
        await session.scalar(
            select(func.count()).select_from(Passage).where(Passage.embedding.isnot(None))
        )
        or 0
    )
    fulltext = (
        await session.scalar(
            select(func.count()).select_from(Publication).where(Publication.raw_path.isnot(None))
        )
        or 0
    )
    by_source = (
        await session.execute(select(Publication.source, func.count()).group_by(Publication.source))
    ).all()
    return {
        "publications": pubs,
        "passages": passages,
        "embedded_passages": embedded,
        "fulltext_publications": fulltext,
        "by_source": {src: count for src, count in by_source},
    }


async def list_publications(
    session: AsyncSession,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[Publication, int, int]]:
    """List publications (newest first) with (publication, passage_count, embedded_count).

    `q` filters case-insensitively on title or abstract.
    """
    counts = (
        select(
            Passage.publication_id.label("pid"),
            func.count().label("n"),
            func.count(Passage.embedding).label("emb"),
        )
        .group_by(Passage.publication_id)
        .subquery()
    )
    stmt = select(
        Publication,
        func.coalesce(counts.c.n, 0),
        func.coalesce(counts.c.emb, 0),
    ).outerjoin(counts, counts.c.pid == Publication.id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Publication.title.ilike(like), Publication.abstract.ilike(like)))
    stmt = stmt.order_by(Publication.first_cached_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).all()
    return [(pub, int(n), int(emb)) for pub, n, emb in rows]


async def get_publication_detail(
    session: AsyncSession, publication_id_: str
) -> tuple[Publication, list[Passage]] | None:
    """Fetch a publication and its passages (ordered), or None if absent."""
    pub = await session.get(Publication, publication_id_)
    if pub is None:
        return None
    passages = list(
        (
            await session.scalars(
                select(Passage)
                .where(Passage.publication_id == publication_id_)
                .order_by(Passage.ordinal)
            )
        ).all()
    )
    return pub, passages


async def delete_publication(session: AsyncSession, publication_id_: str) -> bool:
    """Delete a publication (cascades to passages). Returns True if it existed.

    The raw blob on disk is intentionally left in place — it's
    content-addressable and may be shared; reference-counting blobs is out of
    scope for v1.
    """
    pub = await session.get(Publication, publication_id_)
    if pub is None:
        return False
    await session.delete(pub)
    await session.flush()
    return True
