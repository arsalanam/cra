"""Async SQLAlchemy engine, session factory, and DB initialisation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import get_settings
from .models import (
    DEFAULT_ACCOUNT_NAME,
    DEFAULT_USER_ID,
    Account,
    AccountMember,
    Base,
    ClinicalTrial,
    EcrfStudy,
    RoleAssignment,
    SourceConfig,
    User,
)

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine_and_factory() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        logger.info("Creating database engine: %s", settings.database_url)
        _engine = create_async_engine(settings.database_url, echo=False)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    assert _engine is not None
    assert _session_factory is not None
    return _engine, _session_factory


_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    # Hand-rolled additive migrations for SQLite. Each entry is
    # `<table>: {column_name: SQL ALTER fragment}`. We check the existing
    # columns at startup and ADD any that are missing — never drop or
    # rename. Move to Alembic if migrations get more complex.
    "threads": {
        "user_id": "TEXT REFERENCES users(id) ON DELETE SET NULL",
        "workflow": "TEXT",
        # Sprint A2.5: chat-handoff seeds carry trial context. NULL = thread
        # is not associated with a Trial (legacy / general Q&A / analysis
        # work without a parent trial). Set when the thread is spawned from
        # the /accounts.html "Draft <kind>" CTA or inherited via _runHandoff.
        "trial_id": "TEXT REFERENCES clinical_trials(id) ON DELETE SET NULL",
    },
    "users": {
        # Phase B: Cognito identity binding. SQLite can't ADD COLUMN with a
        # UNIQUE constraint inline, so uniqueness is enforced separately via
        # _INDEX_MIGRATIONS below (portable across SQLite + Postgres).
        "cognito_sub": "TEXT",
    },
    "passages": {
        # R2 embeddings on a table that already existed from R0. These ALTERs
        # only ever run against the live Postgres (test DBs are created fresh
        # by create_all, which adds these columns directly), so Postgres
        # syntax is safe here. `vector` needs the pgvector extension (enabled
        # in init.sql). Must match models.EMBEDDING_DIM.
        "embedding": "vector(1024)",
        "embedding_model": "TEXT",
        "embedded_at": "TIMESTAMPTZ",
    },
    "notifications": {
        # P2 #4 group-level living-review subscriptions. NULL for personal
        # watch notifications (legacy shape); set when the row is fanned out
        # from a LiteratureWatchSubscription quorum-clear event.
        "subscription_id": "TEXT REFERENCES literature_watch_subscriptions(id) ON DELETE CASCADE",
    },
    "ecrf_studies": {
        # Sprint A1 account layer. NULL = pre-account legacy. Backfilled by
        # init_db._backfill_account_layer to point at a Default Account's
        # auto-generated ClinicalTrial wrapper.
        "trial_id": "TEXT REFERENCES clinical_trials(id) ON DELETE SET NULL",
    },
    "pending_invitations": {
        # Sprint U1 — user-admin module. Scope-aware grants alongside the
        # legacy flat roles_json. Matcher consumes whichever is set;
        # assignments_json takes precedence.
        "assignments_json": "TEXT",
    },
}

# Index DDL applied after column migrations. Each must be idempotent
# (CREATE ... IF NOT EXISTS) — works on both SQLite and Postgres, and both
# treat NULLs as distinct so multiple NULL cognito_sub rows are allowed.
_INDEX_MIGRATIONS: list[str] = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_cognito_sub ON users (cognito_sub)",
]

# Columns removed from the model that must be dropped from pre-existing
# databases. `<table>: [column, ...]`. Existence-guarded via the inspector
# (SQLite ≥3.35 + Postgres both support ALTER TABLE DROP COLUMN). Phase D
# dropped `users.is_admin` in favour of role-based auth (user_roles).
_DROP_COLUMNS: dict[str, list[str]] = {
    "users": ["is_admin"],
}

# DDL that only makes sense on Postgres (pgvector). Skipped on SQLite (tests),
# whose `create_all` happily creates the VECTOR column as an inert type but
# can't build an HNSW index. Run after create_all so the table exists. The
# `vector` extension itself is enabled by deploy/compose/init.sql.
_PG_ONLY_DDL: list[str] = [
    "CREATE INDEX IF NOT EXISTS ix_passages_embedding_hnsw "
    "ON passages USING hnsw (embedding vector_cosine_ops)",
]


async def _apply_pg_only_ddl(engine: Any) -> None:
    """Create pgvector indexes — Postgres only, no-op elsewhere."""
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        for ddl in _PG_ONLY_DDL:
            await conn.exec_driver_sql(ddl)


async def _apply_additive_migrations(engine: Any) -> None:
    """ALTER TABLE ADD/DROP COLUMN + CREATE INDEX to reconcile an existing DB."""

    def _migrate(sync_conn: Any) -> None:
        insp = inspect(sync_conn)
        for table, columns in _COLUMN_MIGRATIONS.items():
            if not insp.has_table(table):
                # create_all will have made it with the new columns already.
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in columns.items():
                if col not in existing:
                    logger.info("Migrating: ALTER %s ADD COLUMN %s", table, col)
                    sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # Columns exist now; add any missing indexes.
        for ddl in _INDEX_MIGRATIONS:
            sync_conn.exec_driver_sql(ddl)
        # Drop removed columns from databases that still have them.
        for table, cols in _DROP_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for col in cols:
                if col in existing:
                    logger.info("Migrating: ALTER %s DROP COLUMN %s", table, col)
                    sync_conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {col}")

    async with engine.begin() as conn:
        await conn.run_sync(_migrate)


async def init_db() -> None:
    """Create all tables if they don't exist; seed the default user."""
    engine, factory = _get_engine_and_factory()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Apply lightweight schema upgrades for existing databases that pre-date
    # the User/Thread.user_id/Thread.workflow additions.
    await _apply_additive_migrations(engine)

    # pgvector indexes (Postgres only; no-op on SQLite test DBs).
    await _apply_pg_only_ddl(engine)

    # Seed a default user so existing single-user installs can keep
    # creating threads without auth wired up. When auth lands the
    # default-user row stays, but new threads will be tied to logged-in
    # users instead.
    async with factory() as session:
        existing = await session.get(User, DEFAULT_USER_ID)
        if existing is None:
            session.add(User(id=DEFAULT_USER_ID, name="Default User"))
            await session.commit()
            logger.info("Seeded default user %r", DEFAULT_USER_ID)

        # Backfill: existing threads get the default user.
        await session.execute(
            text("UPDATE threads SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": DEFAULT_USER_ID},
        )
        await session.commit()

        # Seed paper-source configs. Idempotent: existing rows are left
        # alone so admin edits survive restarts.
        for defaults in _SOURCE_CONFIG_DEFAULTS:
            existing_src = await session.get(SourceConfig, defaults["id"])
            if existing_src is None:
                session.add(SourceConfig(**defaults))
                logger.info("Seeded source_config %r", defaults["id"])
        await session.commit()

        # RBAC-1: backfill `role_assignments` from any legacy `user_roles`
        # rows so pre-RBAC-1 databases keep working without operator action.
        # Idempotent: skips users that already have a RoleAssignment for the
        # same (role, global). `user_roles` rows are left in place — they
        # become read-only history.
        await _backfill_role_assignments(session)

        # Sprint A1 account layer: seed a Default Account + wrap legacy
        # EcrfStudy rows in ClinicalTrial under it. Idempotent.
        await _backfill_account_layer(session)

    logger.info("Database tables initialised")


async def _backfill_role_assignments(session: AsyncSession) -> None:
    """Project every legacy `user_roles` row to a global-scoped `role_assignments`
    row. Idempotent — re-running on an already-migrated DB is a no-op.
    """
    legacy_rows = (await session.execute(text("SELECT user_id, role FROM user_roles"))).all()
    if not legacy_rows:
        return
    # Existing global-scope assignments — used to dedupe so re-runs don't
    # insert duplicates (the unique constraint would block them anyway, but
    # we'd rather not even attempt the insert).
    existing_rows = (
        await session.execute(
            text(
                "SELECT user_id, role FROM role_assignments "
                "WHERE scope_type = 'global' AND scope_id IS NULL"
            )
        )
    ).all()
    existing: set[tuple[str, str]] = {(r[0], r[1]) for r in existing_rows}
    inserted = 0
    for user_id, role in legacy_rows:
        if (user_id, role) in existing:
            continue
        session.add(
            RoleAssignment(
                user_id=user_id,
                role=role,
                scope_type="global",
                scope_id=None,
            )
        )
        inserted += 1
    if inserted:
        await session.commit()
        logger.info("Backfilled %d role_assignments from user_roles", inserted)


_SOURCE_CONFIG_DEFAULTS: list[dict[str, Any]] = [
    {
        "id": "pubmed",
        "display_name": "PubMed (NCBI E-utilities)",
        "enabled": True,
        # api_key intentionally empty — falls back to settings.ncbi_api_key.
        "api_key": None,
        "contact_email": None,
    },
    {
        "id": "europepmc",
        "display_name": "Europe PMC",
        "enabled": True,
        "api_key": None,
        "contact_email": None,
    },
]


def reset_engine() -> None:
    """Tear down the cached engine/factory. Used by tests to force re-init."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an async session; commits on success, rolls back on error."""
    _, factory = _get_engine_and_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            logger.error("Database session error — rolling back", exc_info=True)
            await session.rollback()
            raise


async def _backfill_account_layer(session: AsyncSession) -> None:
    """Seed the Default Account + wrap legacy EcrfStudy in ClinicalTrial.

    Idempotent: the Default Account is keyed by name; a re-run that
    finds it skips the seed. Existing studies that already carry a
    `trial_id` are left alone. Newly-created Trial wrappers inherit
    the EcrfStudy's status (draft → 'design', active → 'deployed',
    closed → 'archived').
    """
    from sqlalchemy import select

    existing_account = (
        await session.scalars(select(Account).where(Account.name == DEFAULT_ACCOUNT_NAME))
    ).first()
    if existing_account is None:
        account = Account(
            name=DEFAULT_ACCOUNT_NAME,
            description=(
                "Auto-created on first migration to the account layer. Holds "
                "every legacy EcrfStudy that pre-dated Sprint A1."
            ),
            owner_user_id=DEFAULT_USER_ID,
            status="active",
        )
        session.add(account)
        await session.flush()
        # Add the default user as an owner-member so the membership table
        # is consistent with the owner_user_id pointer.
        session.add(
            AccountMember(
                account_id=account.id,
                user_id=DEFAULT_USER_ID,
                role="owner",
                invited_by_user_id=DEFAULT_USER_ID,
            )
        )
        logger.info("Seeded Default Account %r", account.id)
    else:
        account = existing_account
        # If the owner-member row got lost, restore it.
        owner_member = (
            await session.scalars(
                select(AccountMember).where(
                    AccountMember.account_id == account.id,
                    AccountMember.user_id == DEFAULT_USER_ID,
                )
            )
        ).first()
        if owner_member is None:
            session.add(
                AccountMember(
                    account_id=account.id,
                    user_id=DEFAULT_USER_ID,
                    role="owner",
                    invited_by_user_id=DEFAULT_USER_ID,
                )
            )

    # Wrap every legacy EcrfStudy that doesn't yet have a trial.
    _STATUS_MAP = {"draft": "design", "active": "deployed", "closed": "archived"}
    legacy_studies = (
        await session.scalars(select(EcrfStudy).where(EcrfStudy.trial_id.is_(None)))
    ).all()
    if not legacy_studies:
        await session.commit()
        return
    wrapped = 0
    for study in legacy_studies:
        trial = ClinicalTrial(
            account_id=account.id,
            title=study.name,
            protocol_id=study.protocol_id,
            status=_STATUS_MAP.get(study.status, "design"),
            created_by_user_id=DEFAULT_USER_ID,
        )
        session.add(trial)
        await session.flush()
        study.trial_id = trial.id
        wrapped += 1
    if wrapped:
        await session.commit()
        logger.info("Wrapped %d legacy EcrfStudy row(s) in ClinicalTrial", wrapped)
