"""SQLAlchemy ORM models for conversation thread persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Embedding vector width. MUST match settings.embedding_dimensions; changing
# it is a schema change that requires re-embedding the whole corpus
# (scripts/backfill_embeddings.py --reembed). Kept as a module constant rather
# than read from settings so the ORM schema is static at import time.
EMBEDDING_DIM = 1024

DEFAULT_USER_ID = "default-user"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    """A researcher account.

    Currently auto-seeded with one row (`DEFAULT_USER_ID`) in `init_db`
    so existing single-user installs keep working. When the auth UI
    lands, login will pick the matching user and `Thread.user_id` will
    be enforced as non-null.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    email: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cognito identity binding (Phase B). NULL for the legacy default-user
    # and any pre-auth rows; set on first login by the Phase C matcher.
    cognito_sub: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    # Authorization is role-based (Phase D) — see UserRole. The old boolean
    # is_admin column was dropped; the additive migration removes it from
    # pre-existing databases.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
    )

    threads: Mapped[list[Thread]] = relationship(back_populates="user")
    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserRole(Base):
    """An authorization role granted to a user (Phase B).

    Cognito handles identity only; app authorization is driven by these
    rows (Phase D). Replaced the boolean `User.is_admin`.
    """

    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role", name="uq_user_roles_user_role"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    role: Mapped[str] = mapped_column(Text, doc="e.g. 'admin' | 'researcher'.")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped[User] = relationship(back_populates="roles")


class PendingInvitation(Base):
    """An admin-issued invitation awaiting the invitee's first login (Phase B).

    Created when an admin invites an email (Cognito `AdminCreateUser`).
    On first successful login, the Phase C matcher binds the invitee's
    Cognito `sub` to a `User` row, grants the roles listed in
    `roles_json`, and sets `consumed_at`.
    """

    __tablename__ = "pending_invitations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(Text, unique=True)
    roles_json: Mapped[str] = mapped_column(
        Text,
        default='["researcher"]',
        doc="JSON list of roles to grant on first login.",
    )
    invited_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=DEFAULT_USER_ID,
    )
    workflow: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Which specialist this thread is pinned to (set by the dispatcher).",
    )
    title: Mapped[str] = mapped_column(Text, default="New conversation")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User | None] = relationship(back_populates="threads")
    messages: Mapped[list[Message]] = relationship(
        back_populates="thread", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text)  # "user" or "assistant"
    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    thread: Mapped[Thread] = relationship(back_populates="messages")
    stream_events: Mapped[list[StreamEvent]] = relationship(
        back_populates="message", cascade="all, delete-orphan", order_by="StreamEvent.sequence_num"
    )


class StreamEvent(Base):
    __tablename__ = "stream_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(Text)
    data: Mapped[str] = mapped_column(Text)  # JSON blob
    sequence_num: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    message: Mapped[Message] = relationship(back_populates="stream_events")


class LiteratureWatch(Base):
    """A saved 'living review' watch.

    Snapshot of a validated search (typically from a finalised
    `strategy_result` turn) that the scheduler re-runs on a schedule.
    Each run diffs the current PMID set against `baseline_pmids_json`,
    triages new hits via the watch_triage agent, and creates a
    Notification when at least one hit clears `triage_threshold`.

    `status` controls scheduling — paused watches stay in the DB but
    don't fire.
    """

    __tablename__ = "literature_watches"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=DEFAULT_USER_ID,
    )
    name: Mapped[str] = mapped_column(Text)
    pico_json: Mapped[str] = mapped_column(
        Text,
        doc="JSON snapshot of the PicoTable at watch creation time.",
    )
    search_query: Mapped[str] = mapped_column(
        Text,
        doc="The validated PubMed Boolean string the runner re-executes.",
    )
    sources_json: Mapped[str] = mapped_column(
        Text,
        default='["pubmed"]',
        doc="JSON list of source ids to query (typically pubmed + europepmc).",
    )
    schedule_cron: Mapped[str] = mapped_column(
        Text,
        doc=(
            "APScheduler-compatible cron expression "
            "(minute hour day month dow). e.g. '0 9 * * 1' = Mondays 09:00."
        ),
    )
    triage_threshold: Mapped[float] = mapped_column(
        Float,
        default=0.6,
        doc="Min materiality score (0–1) for a new paper to trigger notification.",
    )
    baseline_pmids_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
        doc="JSON list of PMIDs already known. Updated after each successful run.",
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="active",
        doc="'active' | 'paused'.",
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_run_status: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="'success' | 'no_change' | 'error' | 'quota_exceeded'.",
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User | None] = relationship()
    runs: Mapped[list[WatchRun]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        order_by="WatchRun.started_at.desc()",
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        order_by="Notification.created_at.desc()",
    )


class WatchRun(Base):
    """One execution of a LiteratureWatch."""

    __tablename__ = "watch_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    watch_id: Mapped[str] = mapped_column(
        ForeignKey("literature_watches.id", ondelete="CASCADE"),
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="running",
        doc="'running' | 'success' | 'no_change' | 'error'.",
    )
    total_hits: Mapped[int] = mapped_column(Integer, default=0)
    new_pmids_json: Mapped[str] = mapped_column(Text, default="[]")
    removed_pmids_json: Mapped[str] = mapped_column(Text, default="[]")
    triage_results_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="JSON [{pmid, relevance, design_fit, materiality, note}] from the triage agent.",
    )
    significance_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    watch: Mapped[LiteratureWatch] = relationship(back_populates="runs")
    notification: Mapped[Notification | None] = relationship(back_populates="run", uselist=False)


class Notification(Base):
    """An in-app alert raised when a WatchRun produces material new evidence."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=DEFAULT_USER_ID,
    )
    watch_id: Mapped[str] = mapped_column(
        ForeignKey("literature_watches.id", ondelete="CASCADE"),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("watch_runs.id", ondelete="CASCADE"),
    )
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    new_paper_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    watch: Mapped[LiteratureWatch] = relationship(back_populates="notifications")
    run: Mapped[WatchRun] = relationship(back_populates="notification")


class SourceConfig(Base):
    """Per-paper-source runtime config (admin-editable).

    Overlays the static `.env` defaults in `config.settings`. Empty fields
    fall back to env values via `config.service._rate_config_for`. Seeded
    with `pubmed` and `europepmc` rows by `init_db`.
    """

    __tablename__ = "source_configs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # "pubmed", "europepmc"
    display_name: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    contact_email: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    backoff_base_sec: Mapped[float] = mapped_column(Float, default=1.0)
    backoff_cap_sec: Mapped[float] = mapped_column(Float, default=10.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class Publication(Base):
    """A cached publication — the foundation of the local library (R0).

    Populated write-through: `search_papers` caches abstract + metadata for
    every result, and `fetch_pmc_fulltext` attaches the raw full-text body.
    `id` is content-addressable so the same paper from different sources or
    repeat searches collapses onto one row (see
    `persistence.library.repository.publication_id`):

        pmid:<pmid>  →  doi:<lowercased doi>  →  sha256:<hash of source:source_id>

    Embeddings (R2) and semantic `rag_search` (R3) build on this table; the
    `Passage` rows are what eventually get chunked + embedded. No vector
    column yet — that lands in R2.
    """

    __tablename__ = "publications"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    pmid: Mapped[str | None] = mapped_column(Text, nullable=True, index=True, default=None)
    doi: Mapped[str | None] = mapped_column(Text, nullable=True, index=True, default=None)
    source: Mapped[str] = mapped_column(Text, doc="Origin: 'pubmed' | 'europepmc' | 'upload' | …")
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    title: Mapped[str] = mapped_column(Text)
    journal: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    authors_json: Mapped[str] = mapped_column(Text, default="[]")
    mesh_terms_json: Mapped[str] = mapped_column(Text, default="[]")
    publication_types_json: Mapped[str] = mapped_column(Text, default="[]")
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Content-addressable raw store (full-text XML, uploaded PDF). Path is
    # relative to settings.library_raw_dir so the blob store can be remounted
    # (e.g. onto a shared k8s volume). NULL when only the abstract is cached.
    raw_path: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    content_sha256: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    first_cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    passages: Mapped[list[Passage]] = relationship(
        back_populates="publication",
        cascade="all, delete-orphan",
        order_by="Passage.ordinal",
    )


class Passage(Base):
    """A unit of text from a cached publication.

    R0 stores the abstract as a single passage (section="abstract",
    ordinal 0). PDF ingestion (R1) and section-aware chunking + embeddings
    (R2) add more rows per publication. The `vector` column is intentionally
    absent until R2; `page`/`bbox` stay NULL for API-sourced text and carry
    layout coordinates only for parsed PDFs.
    """

    __tablename__ = "passages"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    publication_id: Mapped[str] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE"), index=True
    )
    section: Mapped[str] = mapped_column(Text, default="abstract")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    bbox: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Embeddings (R2). NULL until embedded; the background drain
    # (rag.embed_worker) fills these. `embedding_model` stamps the
    # `<model_id>@<dims>` version so re-embeds and mixed-model corpora are
    # detectable. The HNSW index on `embedding` is Postgres-only and created
    # in init_db (not via create_all, so SQLite tests keep working).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True, default=None
    )
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    publication: Mapped[Publication] = relationship(back_populates="passages")
