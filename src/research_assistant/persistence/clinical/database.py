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


async def init_clinical_db() -> None:
    """Create the clinical-store tables if they don't exist."""
    engine, _ = _get_engine_and_factory()
    async with engine.begin() as conn:
        await conn.run_sync(ClinicalBase.metadata.create_all)
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
