"""One-shot data migration from the local SQLite (threads.db) to Postgres.

Usage:
    # Source defaults to ./threads.db; target defaults to the compose Postgres.
    uv run python scripts/migrate_sqlite_to_postgres.py

    # Or with explicit URLs:
    uv run python scripts/migrate_sqlite_to_postgres.py \\
        --source sqlite+aiosqlite:///./threads.db \\
        --target postgresql+asyncpg://cra:cra@localhost:5432/cra

Behavior:
  • Both DBs must already have the schema (run init_db on the target first —
    `docker compose up postgres` then a brief `agent` start will do it).
  • For each table, rows already present in the target (by primary key) are
    SKIPPED — so the script is safe to re-run after a partial copy.
  • Tables are copied in FK-dependency order. Stream events depend on
    messages; messages on threads; threads/watches/notifications on users.

What it does NOT do:
  • Schema diffs or migrations — both ends must match.
  • Sequence/identity fixups — all PKs in this codebase are app-generated
    UUID strings, so there are no sequences to advance.
  • Vector data — pgvector tables don't exist yet.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from research_assistant.persistence.models import (
    Base,
    LiteratureWatch,
    Message,
    Notification,
    SourceConfig,
    StreamEvent,
    Thread,
    User,
    WatchRun,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate")


# FK dependency order. Sources have no FKs; users come next; then everything
# that references users; then stream_events / watch_runs / notifications.
_COPY_ORDER: list[type[DeclarativeBase]] = [
    SourceConfig,
    User,
    Thread,
    Message,
    StreamEvent,
    LiteratureWatch,
    WatchRun,
    Notification,
]


def _row_to_dict(row: DeclarativeBase) -> dict[str, Any]:
    """Pull mapped column values into a plain dict (no relationships)."""
    mapper = row.__class__.__mapper__
    return {col.key: getattr(row, col.key) for col in mapper.column_attrs}


async def _copy_table(
    source_factory: async_sessionmaker[Any],
    target_factory: async_sessionmaker[Any],
    model: type[DeclarativeBase],
) -> tuple[int, int]:
    """Copy one table. Returns (copied, skipped)."""
    copied = 0
    skipped = 0
    async with source_factory() as src:
        result = await src.execute(select(model))
        src_rows = list(result.scalars().all())

    if not src_rows:
        logger.info("%s: source empty", model.__tablename__)
        return 0, 0

    pk_col = next(iter(model.__mapper__.primary_key))
    pk_name = pk_col.key

    async with target_factory() as tgt:
        # Fetch existing PKs in one query.
        existing_result = await tgt.execute(select(pk_col))
        existing_ids: set[Any] = set(existing_result.scalars().all())

        for src_row in src_rows:
            data = _row_to_dict(src_row)
            if data.get(pk_name) in existing_ids:
                skipped += 1
                continue
            tgt.add(model(**data))
            copied += 1

        await tgt.commit()

    logger.info(
        "%s: copied=%d skipped=%d total_src=%d",
        model.__tablename__,
        copied,
        skipped,
        len(src_rows),
    )
    return copied, skipped


async def migrate(source_url: str, target_url: str) -> None:
    logger.info("Source: %s", source_url)
    logger.info("Target: %s", target_url)

    src_engine = create_async_engine(source_url, echo=False)
    tgt_engine = create_async_engine(target_url, echo=False)
    src_factory = async_sessionmaker(src_engine, expire_on_commit=False)
    tgt_factory = async_sessionmaker(tgt_engine, expire_on_commit=False)

    # Sanity: the target must already have the schema. We don't create
    # tables here — agent's init_db owns that.
    async with tgt_engine.begin() as conn:

        def _check(sync_conn: Any) -> None:
            from sqlalchemy import inspect

            insp = inspect(sync_conn)
            missing = [t for t in (m.__tablename__ for m in _COPY_ORDER) if not insp.has_table(t)]
            if missing:
                raise RuntimeError(
                    f"Target DB is missing tables: {missing}. "
                    "Run `docker compose up agent` first so init_db creates them."
                )

        await conn.run_sync(_check)

    totals_copied = 0
    totals_skipped = 0
    for model in _COPY_ORDER:
        copied, skipped = await _copy_table(src_factory, tgt_factory, model)
        totals_copied += copied
        totals_skipped += skipped

    logger.info("=" * 60)
    logger.info("Done. Total copied=%d, skipped=%d", totals_copied, totals_skipped)

    await src_engine.dispose()
    await tgt_engine.dispose()


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--source",
        default="sqlite+aiosqlite:///./threads.db",
        help="Source SQLAlchemy URL (default: ./threads.db)",
    )
    p.add_argument(
        "--target",
        default="postgresql+asyncpg://cra:cra@localhost:5432/cra",
        help="Target SQLAlchemy URL (default: localhost compose Postgres)",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    # Suppress the unused-import warning on Base — it's needed for SQLAlchemy
    # registry side-effects even though we reference models via _COPY_ORDER.
    _ = Base
    asyncio.run(migrate(args.source, args.target))
