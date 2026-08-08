"""Engine / session / init for the clinical-data (PHI) store (eCRF E1).

A second SQLAlchemy engine, separate from `persistence.database` (decision
D2). Mirrors that module's lazy-engine pattern but for `ClinicalBase`; there
are no migrations or seeds — the schema is new, so `create_all` suffices.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ...config import get_settings
from .models import ClinicalBase

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine_and_factory() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        logger.info("Creating clinical-data engine: %s", settings.clinical_database_url)
        _engine = create_async_engine(settings.clinical_database_url, echo=False)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    assert _engine is not None
    assert _session_factory is not None
    return _engine, _session_factory


# Postgres-only DDL enforcing the audit trail's append-only guarantee at the
# engine (eCRF E6). Skipped on SQLite (tests). Idempotent.
_AUDIT_IMMUTABLE_DDL = """
CREATE OR REPLACE FUNCTION audit_entries_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_entries is append-only; UPDATE/DELETE is not permitted';
END;
$$ LANGUAGE plpgsql;
"""

_AUDIT_IMMUTABLE_TRIGGER = """
CREATE TRIGGER trg_audit_entries_immutable
    BEFORE UPDATE OR DELETE ON audit_entries
    FOR EACH ROW EXECUTE FUNCTION audit_entries_immutable();
"""


# Additive column migrations for the clinical store. `create_all` only
# creates MISSING tables — it never adds a column to a table that already
# exists — so a column added to an existing model needs an explicit ALTER
# for a deployment whose Postgres volume predates it.
#
# This is Postgres-only ON PURPOSE: every SQLite database the app touches
# (all tests, and any fresh dev DB) is created from scratch by `create_all`
# and therefore already has every column — there is nothing to migrate. It
# also matters mechanically: the test in-memory aiosqlite engine is reused
# across tests without an explicit dispose(), so an extra DDL/reflection
# round-trip on it leaks a connection-worker thread that races the
# already-closed event loop at GC ("Event loop is closed" warnings). Keeping
# the migration off the SQLite path avoids that entirely. `ADD COLUMN IF NOT
# EXISTS` (Postgres 9.6+) is idempotent without reflection, so a fresh
# Postgres deploy (column already made by create_all) is a clean no-op.
_CLINICAL_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "adverse_events": {
        # SUSAR detection — expectedness vs the Reference Safety Information.
        "expectedness": "TEXT NOT NULL DEFAULT 'unknown'",
    },
}


async def _apply_clinical_additive_migrations(engine: AsyncEngine) -> None:
    """ADD COLUMN for pre-existing Postgres tables. No-op on SQLite (see the
    _CLINICAL_COLUMN_MIGRATIONS comment)."""
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        for table, columns in _CLINICAL_COLUMN_MIGRATIONS.items():
            for col, ddl in columns.items():
                logger.info("Clinical migrating: ALTER %s ADD COLUMN IF NOT EXISTS %s", table, col)
                await conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}"
                )


async def _apply_clinical_pg_ddl(engine: AsyncEngine) -> None:
    """Install the append-only audit trigger — Postgres only, no-op elsewhere."""
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_AUDIT_IMMUTABLE_DDL)
        await conn.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_audit_entries_immutable ON audit_entries"
        )
        await conn.exec_driver_sql(_AUDIT_IMMUTABLE_TRIGGER)


async def init_clinical_db() -> None:
    """Create the clinical-store tables if they don't exist; harden the audit trail."""
    engine, _ = _get_engine_and_factory()
    async with engine.begin() as conn:
        await conn.run_sync(ClinicalBase.metadata.create_all)
    await _apply_clinical_additive_migrations(engine)
    await _apply_clinical_pg_ddl(engine)
    logger.info("Clinical-data tables initialised")


def reset_clinical_engine() -> None:
    """Tear down the cached engine/factory. Used by tests to force re-init."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None


@asynccontextmanager
async def get_clinical_session() -> AsyncIterator[AsyncSession]:
    """Yield a clinical-store session; commits on success, rolls back on error."""
    _, factory = _get_engine_and_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            logger.error("Clinical-data session error — rolling back", exc_info=True)
            await session.rollback()
            raise
