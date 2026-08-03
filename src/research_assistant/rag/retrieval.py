"""Hybrid retrieval over the embedded publication cache (R3).

Combines a dense channel (pgvector cosine over passage embeddings, via the
HNSW index) and a sparse channel (Postgres full-text search) with Reciprocal
Rank Fusion, then applies light section weighting. Every returned passage is
real cached text joined to its publication's citation metadata — so answers
built from these results are grounded, not hallucinated.

Postgres-only (vectors + FTS). Returns an empty list on SQLite (tests) or
when the library has no current-version embeddings yet, so callers degrade
gracefully instead of erroring.

The sparse channel computes `to_tsvector` on the fly — fine at the current
corpus size. A stored, GIN-indexed tsvector column is the upgrade path when
the library grows (see project_rag_plan).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text

from ..config import get_settings
from ..persistence.database import get_db_session
from .embedder import embedding_version, get_embedder

logger = logging.getLogger(__name__)

# Standard RRF constant — damps the contribution of low-ranked items so the
# fusion is dominated by items near the top of either channel.
RRF_K = 60

# Passages from these sections are mild-boosted: they carry the headline
# evidence most queries are after.
_SECTION_WEIGHTS: dict[str, float] = {
    "abstract": 1.10,
    "results": 1.10,
    "conclusion": 1.05,
}

_SNIPPET_CHARS = 320

_SELECT_COLUMNS = (
    "p.id AS passage_id, p.publication_id, p.section, p.text, "
    "pub.pmid, pub.doi, pub.title, pub.journal, pub.year"
)


@dataclass(frozen=True)
class RetrievedPassage:
    passage_id: str
    publication_id: str
    section: str
    snippet: str
    pmid: str | None
    doi: str | None
    title: str | None
    journal: str | None
    year: int | None
    score: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def rrf_fuse(
    dense: Sequence[Mapping[str, Any]],
    sparse: Sequence[Mapping[str, Any]],
    *,
    top_n: int,
    k: int = RRF_K,
) -> list[tuple[Mapping[str, Any], float]]:
    """Reciprocal Rank Fusion of two ranked channels, keyed by passage_id.

    Pure + DB-free for testability. Adds 1/(k+rank) from each channel a
    passage appears in, then multiplies by its section weight, and returns
    the top_n (row, score) pairs descending.
    """
    scores: dict[str, float] = {}
    rows: dict[str, Mapping[str, Any]] = {}
    for channel in (dense, sparse):
        for rank, row in enumerate(channel):
            pid = row["passage_id"]
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
            rows.setdefault(pid, row)

    for pid, row in rows.items():
        scores[pid] *= _SECTION_WEIGHTS.get(row.get("section", ""), 1.0)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [(rows[pid], score) for pid, score in ranked]


def _to_passage(row: Mapping[str, Any], score: float) -> RetrievedPassage:
    body = (row.get("text") or "").strip()
    snippet = body if len(body) <= _SNIPPET_CHARS else body[:_SNIPPET_CHARS] + "…"
    return RetrievedPassage(
        passage_id=row["passage_id"],
        publication_id=row["publication_id"],
        section=row["section"],
        snippet=snippet,
        pmid=row.get("pmid"),
        doi=row.get("doi"),
        title=row.get("title"),
        journal=row.get("journal"),
        year=row.get("year"),
        score=round(score, 6),
    )


async def hybrid_search(
    query: str,
    *,
    top_n: int = 8,
    channel_k: int = 20,
) -> list[RetrievedPassage]:
    """Dense + sparse + RRF retrieval over the cached library. Never raises."""
    settings = get_settings()
    if not settings.embedding_enabled or not query.strip():
        return []
    try:
        async with get_db_session() as session:
            if session.bind is None or session.bind.dialect.name != "postgresql":
                return []

            version = embedding_version(settings)
            qvec = await get_embedder().embed_query(query)
            qstr = "[" + ",".join(str(x) for x in qvec) + "]"

            dense_result = await session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} "  # noqa: S608 — interpolates only the module-level column constant; user input is bound params
                    "FROM passages p JOIN publications pub ON pub.id = p.publication_id "
                    "WHERE p.embedding IS NOT NULL AND p.embedding_model = :ver "
                    "ORDER BY p.embedding <=> (:q)::vector LIMIT :k"
                ),
                {"ver": version, "q": qstr, "k": channel_k},
            )
            dense = [dict(m) for m in dense_result.mappings().all()]

            sparse_result = await session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} "  # noqa: S608 — interpolates only the module-level column constant; user input is bound params
                    "FROM passages p JOIN publications pub ON pub.id = p.publication_id "
                    "WHERE to_tsvector('english', p.text) "
                    "@@ websearch_to_tsquery('english', :q) "
                    "ORDER BY ts_rank("
                    "to_tsvector('english', p.text), "
                    "websearch_to_tsquery('english', :q)) DESC "
                    "LIMIT :k"
                ),
                {"q": query, "k": channel_k},
            )
            sparse = [dict(m) for m in sparse_result.mappings().all()]

            fused = rrf_fuse(dense, sparse, top_n=top_n)
            logger.info(
                "rag_search: dense=%d sparse=%d fused=%d for %r",
                len(dense),
                len(sparse),
                len(fused),
                query[:60],
            )
            return [_to_passage(row, score) for row, score in fused]
    except Exception:
        logger.warning("Hybrid retrieval failed; returning no results", exc_info=True)
        return []
