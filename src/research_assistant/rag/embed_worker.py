"""Background embedding drain (R2).

Embedding is in-process by design — see architecture.md's "Celery + Redis
rejected for v1" decision record. The work is I/O-bound (Bedrock calls) and
fully recoverable from DB state: `embedding IS NULL` *is* the queue, so a
crash mid-pass just means those rows get re-embedded next time. No broker,
no dual-write, no lost work.

Execution reuses the existing `AsyncIOScheduler` (the watch runner's): an
`interval` job drains the queue periodically, and write-through fires an
ad-hoc nudge so freshly-cached passages embed within seconds. Rows are
claimed with `FOR UPDATE SKIP LOCKED` so multiple agent replicas (HPA) never
double-embed the same passage.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select

from ..config import get_settings
from ..persistence.database import get_db_session
from ..persistence.models import Passage
from .embedder import embedding_version, get_embedder

logger = logging.getLogger(__name__)

_DRAIN_JOB_ID = "embed-drain"
_DRAIN_INTERVAL_SECONDS = 30
_DEFAULT_BATCH = 256


async def embed_pending_passages(limit: int = _DEFAULT_BATCH) -> int:
    """Embed one batch of un-embedded passages. Returns how many were embedded.

    Postgres-only (vector writes + SKIP LOCKED); a no-op on SQLite so unit
    tests and the in-memory fixture never touch Bedrock. Never raises —
    failures are logged and the rows stay pending for the next pass.
    """
    settings = get_settings()
    if not settings.embedding_enabled:
        return 0

    version = embedding_version(settings)
    try:
        async with get_db_session() as session:
            if session.bind is None or session.bind.dialect.name != "postgresql":
                return 0

            # Claim rows that are unembedded or embedded by a different
            # model/dim. SKIP LOCKED lets concurrent replicas take disjoint
            # batches without blocking each other.
            stmt = (
                select(Passage)
                .where(
                    or_(
                        Passage.embedding.is_(None),
                        Passage.embedding_model.is_(None),
                        Passage.embedding_model != version,
                    )
                )
                .order_by(Passage.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            passages = list((await session.scalars(stmt)).all())
            if not passages:
                return 0

            embedder = get_embedder()
            vectors = await embedder.embed_documents([p.text for p in passages])
            now = datetime.now(UTC)
            for passage, vector in zip(passages, vectors, strict=True):
                passage.embedding = vector
                passage.embedding_model = version
                passage.embedded_at = now
            # T1 spend quota: bill the batch's Titan tokens (shared cache →
            # Default Account). Same transaction as the embedding writes.
            tokens = getattr(embedder, "total_input_tokens", 0)
            if tokens:
                from ..services.spend import record_embedding_spend

                await record_embedding_spend(session, tokens=tokens, model_id=embedder.model_id)
            logger.info("Embedded %d passage(s) with %s", len(passages), version)
            return len(passages)
    except Exception:
        logger.warning("Embedding drain pass failed; rows remain pending", exc_info=True)
        return 0


async def drain_all(batch: int = _DEFAULT_BATCH, max_batches: int = 10_000) -> int:
    """Loop `embed_pending_passages` until the queue is empty (backfill)."""
    total = 0
    for _ in range(max_batches):
        n = await embed_pending_passages(batch)
        total += n
        if n == 0:
            break
    return total


def register_embedding_drain() -> None:
    """Register the periodic drain on the shared scheduler (lifespan startup).

    Safe + best-effort: if embeddings are disabled or the scheduler isn't
    running (tests/scripts), this is a no-op.
    """
    if not get_settings().embedding_enabled:
        return
    try:
        from ..services.scheduler import get_scheduler

        get_scheduler().add_job(
            embed_pending_passages,
            trigger="interval",
            seconds=_DRAIN_INTERVAL_SECONDS,
            id=_DRAIN_JOB_ID,
            replace_existing=True,
            max_instances=1,  # never stack drain passes
            coalesce=True,
        )
        logger.info("Registered embedding drain (every %ds)", _DRAIN_INTERVAL_SECONDS)
    except Exception:
        logger.warning("Could not register embedding drain", exc_info=True)


def nudge_embedding() -> None:
    """Fire an immediate, out-of-interval drain pass (write-through hook).

    Mirrors the watch runner's `trigger_now`. Best-effort: silently skipped
    when embeddings are disabled or the scheduler isn't running.
    """
    if not get_settings().embedding_enabled:
        return
    try:
        from ..services.scheduler import get_scheduler

        get_scheduler().add_job(
            embed_pending_passages,
            id="embed-nudge",
            replace_existing=True,  # collapse bursts into one pending pass
            max_instances=1,
            coalesce=True,
        )
    except Exception:
        logger.debug("Embedding nudge skipped (scheduler unavailable)", exc_info=True)
