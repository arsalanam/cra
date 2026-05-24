"""One-shot embedding backfill for the local publication cache (R2).

Embeds every passage that has no current-version embedding yet — e.g. the
abstracts cached by R0 before embeddings existed. Run after deploying R2:

    uv run python scripts/backfill_embeddings.py
    docker compose -f deploy/compose/docker-compose.yml exec agent \
        python scripts/backfill_embeddings.py

Pass --reembed to NULL existing vectors first (use after changing the
embedding model or dimension); the drain then re-embeds them with the new
version tag.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import update

from research_assistant.persistence.database import get_db_session
from research_assistant.persistence.models import Passage
from research_assistant.rag import drain_all

logger = logging.getLogger(__name__)


async def _clear_embeddings() -> int:
    async with get_db_session() as session:
        result = await session.execute(
            update(Passage).values(embedding=None, embedding_model=None, embedded_at=None)
        )
        return result.rowcount or 0


async def main(reembed: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if reembed:
        cleared = await _clear_embeddings()
        logger.info("Cleared %d existing embedding(s) for re-embed", cleared)
    total = await drain_all()
    logger.info("Backfill complete — embedded %d passage(s)", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill passage embeddings.")
    parser.add_argument(
        "--reembed",
        action="store_true",
        help="Clear existing vectors first (after a model/dimension change).",
    )
    args = parser.parse_args()
    asyncio.run(main(args.reembed))
