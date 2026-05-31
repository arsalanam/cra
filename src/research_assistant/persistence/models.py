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
    role_assignments: Mapped[list[RoleAssignment]] = relationship(
        cascade="all, delete-orphan",
        foreign_keys="RoleAssignment.user_id",
    )


class UserRole(Base):
    """An authorization role granted to a user (Phase B — pre-RBAC-1).

    Superseded by `RoleAssignment` below, which carries the scope columns
    that `rbac-design.md` §4.2 requires. The model stays around so
    pre-RBAC-1 databases still inspect/migrate cleanly — `init_db` reads
    these rows on startup and projects them to global-scoped
    `RoleAssignment` rows, then leaves them in place (no destructive
    drop). New writes go to `RoleAssignment` only.
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


class RoleAssignment(Base):
    """A scoped role grant (RBAC-1, replaces flat `UserRole`).

    Per `rbac-design.md` §4.2: every grant is `(user, role, scope)` where
    scope is `global` (whole platform), `study:<id>`, or `site:<id>`.
    Broader scope satisfies narrower checks (§4.1: global ⊃ study ⊃ site).

    `scope_id` is by-value — the eCRF clinical store lives in a different
    database, so a true FK across DBs would be wrong. Integrity is
    enforced at write time by the admin grant API.

    No cascade FK to `users` here even though `user_id` references it,
    because the dialect-agnostic ALTER paths used in `_apply_additive_
    migrations` can't add a CASCADE FK after the fact; CASCADE on user
    delete is fine for the new-row case (set up by `create_all`) and the
    UserRepository revoke path handles the explicit case.
    """

    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role",
            "scope_type",
            "scope_id",
            name="uq_role_assignments_user_role_scope",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(
        Text,
        doc="Canonical role name from auth.rbac.Role (admin | researcher | student | …).",
    )
    scope_type: Mapped[str] = mapped_column(
        Text,
        default="global",
        doc="'global' | 'study' | 'site' (auth.rbac.ScopeType).",
    )
    scope_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "NULL for scope_type='global'; the study or site id otherwise. "
            "By-value reference into the clinical store (no cross-DB FK)."
        ),
    )
    granted_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


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
    """An in-app alert raised when a WatchRun produces material new evidence.

    Two flavours share the table:
      • Personal — `subscription_id` is NULL. The watch's owner is the
        recipient (`user_id`). Created by `services.watch_runner` when a
        run trips the watch's own `triage_threshold`.
      • Group — `subscription_id` is set. Created by
        `services.watch_subscriptions` when quorum on a LiteratureWatch-
        Subscription clears for a given run. One Notification row is
        fanned out to each subscription member (each row carries that
        member's `user_id`).
    """

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
    # Set when the notification is a group-level alert fanned out from a
    # LiteratureWatchSubscription quorum-clear event. NULL for personal
    # watch alerts (the legacy shape).
    subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("literature_watch_subscriptions.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
        index=True,
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


# ── Group-level living-review subscriptions (P2 #4) ─────────────────────


class LiteratureWatchSubscription(Base):
    """A group wrapper around a LiteratureWatch.

    Ad-hoc membership (operator invites users by email or local sub).
    When a WatchRun lands, each member can cast a yes/no/abstain vote
    on whether the run is practice-changing enough to trip a group
    alert. Quorum is configurable: yes-votes ≥ max(min_votes,
    ceil(min_fraction × n_members)) — matching the HTA / guideline-
    committee convention "N votes OR % of members, whichever is
    greater".

    Notifications are fanned out per-member when quorum clears (one
    Notification row per member, all sharing the same subscription_id +
    run_id). This keeps the existing per-user `/api/notifications` feed
    working unchanged.
    """

    __tablename__ = "literature_watch_subscriptions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    watch_id: Mapped[str] = mapped_column(
        ForeignKey("literature_watches.id", ondelete="CASCADE"),
        index=True,
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        doc="Creator of the subscription. Implicit voter; can manage members.",
    )
    min_votes: Mapped[int] = mapped_column(
        Integer,
        default=2,
        doc=(
            "Absolute floor on yes-votes for quorum. Default 2 — a "
            "single yes-vote shouldn't fire a group alert."
        ),
    )
    min_fraction: Mapped[float] = mapped_column(
        Float,
        default=0.5,
        doc=(
            "Fraction of members whose yes-votes are required (0.0 – "
            "1.0). Quorum cleared when yes ≥ max(min_votes, "
            "ceil(min_fraction × n_members))."
        ),
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="active",
        doc="'active' | 'paused'. Paused subscriptions ignore new WatchRuns.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    watch: Mapped[LiteratureWatch] = relationship()
    members: Mapped[list[SubscriptionMember]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )
    votes: Mapped[list[RunVote]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )


class SubscriptionMember(Base):
    """Ad-hoc membership row.

    `role` is voter | observer. Observers receive notifications when
    quorum clears but don't count toward the denominator and can't
    vote — useful for sponsors / liaisons who attend but don't decide.
    """

    __tablename__ = "subscription_members"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "user_id",
            name="uq_subscription_member_user",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("literature_watch_subscriptions.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(
        Text,
        default="voter",
        doc="'voter' (counts toward quorum) | 'observer' (read-only).",
    )
    invited_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    subscription: Mapped[LiteratureWatchSubscription] = relationship(
        back_populates="members",
        foreign_keys=[subscription_id],
    )


class RunVote(Base):
    """One member's vote on one WatchRun for one Subscription.

    Idempotent per (subscription, run, voter): the unique constraint
    means re-voting overwrites the prior choice (the repo method
    upserts) instead of creating a duplicate. Rationale is free-text
    captured at vote time.
    """

    __tablename__ = "run_votes"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "run_id",
            "voter_user_id",
            name="uq_run_vote_voter",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("literature_watch_subscriptions.id", ondelete="CASCADE"),
        index=True,
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("watch_runs.id", ondelete="CASCADE"),
        index=True,
    )
    voter_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    vote: Mapped[str] = mapped_column(
        Text,
        doc="'yes' | 'no' | 'abstain'. Enforced by the repo method.",
    )
    rationale: Mapped[str] = mapped_column(Text, default="")
    voted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    subscription: Mapped[LiteratureWatchSubscription] = relationship(
        back_populates="votes",
    )


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


class EcrfStudy(Base):
    """An eCRF study — container for form definitions + the visit schedule (E0).

    Metadata only (no subject PHI; that lives in the separate clinical-data
    store from E1 onward — see ecrf-design.md D2). `schedule_json` holds a
    serialised `domain.ecrf.VisitSchedule`.
    """

    __tablename__ = "ecrf_studies"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text)
    protocol_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    schedule_json: Mapped[str] = mapped_column(Text, default='{"events": [], "form_event_map": []}')
    status: Mapped[str] = mapped_column(Text, default="draft", doc="draft | active | closed")
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    forms: Mapped[list[EcrfFormDefinition]] = relationship(
        back_populates="study", cascade="all, delete-orphan"
    )


class EcrfFormDefinition(Base):
    """A versioned CRF form definition (E0).

    The whole `domain.ecrf.FormDefinition` tree is stored validated in
    `definition_json` (JSON-native, decision D1). Once `status='published'`
    the row is immutable — amendments create a new version (next `version`
    for the same `study_id` + `name`); publishing supersedes the prior
    published version.
    """

    __tablename__ = "ecrf_form_definitions"
    __table_args__ = (
        UniqueConstraint("study_id", "name", "version", name="uq_ecrf_form_study_name_version"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("ecrf_studies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text, doc="Machine key, unique per study across versions.")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(Text, default="draft", doc="draft | published | superseded")
    title: Mapped[str] = mapped_column(Text)
    definition_json: Mapped[str] = mapped_column(Text, doc="Serialised FormDefinition.")
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    study: Mapped[EcrfStudy] = relationship(back_populates="forms")


# ── Systematic-review screening (top-6 #2) ──────────────────────────────


class SrReview(Base):
    """A systematic-review project.

    The container for the screening workflow: holds the PICO snapshot, the
    Boolean search query that seeds the candidate set, the inclusion +
    exclusion criteria reviewers use, and the project status. Reviewer
    assignments live on `SrReviewMembership`; candidate papers and per-
    reviewer judgements hang off `SrCandidate` and `ScreeningDecision`.
    """

    __tablename__ = "sr_reviews"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    pico_json: Mapped[str] = mapped_column(Text, default="{}")
    search_query: Mapped[str] = mapped_column(Text, default="")
    sources_json: Mapped[str] = mapped_column(
        Text,
        default='["pubmed", "europepmc"]',
        doc="JSON list of source ids to fan out the ingest across.",
    )
    inclusion_criteria_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
        doc=(
            "JSON list of inclusion criterion strings. The screening UI surfaces "
            "these next to the abstract; AI-assist passes them to the classifier."
        ),
    )
    exclusion_criteria_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(
        Text,
        default="draft",
        doc="draft | ingested | abstract_screening | fulltext_screening | complete",
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    memberships: Mapped[list[SrReviewMembership]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )
    candidates: Mapped[list[SrCandidate]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )


class SrReviewMembership(Base):
    """A user's role on an SR project (reviewer_1 / reviewer_2 / adjudicator).

    Distinct from the project-scoped `RoleAssignment` rows that grant the
    `sr.*` permissions — the membership row is the authoritative record of
    WHICH reviewer slot the user fills (R1 vs R2 vs adjudicator) when
    `ScreeningDecision.role` is recorded. The RBAC grant is created in
    parallel by the project-management endpoint and revoked together.
    """

    __tablename__ = "sr_review_memberships"
    __table_args__ = (
        UniqueConstraint("sr_review_id", "user_id", "role", name="uq_sr_membership_unique"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    sr_review_id: Mapped[str] = mapped_column(
        ForeignKey("sr_reviews.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(
        Text,
        doc="One of 'reviewer_1' | 'reviewer_2' | 'adjudicator'.",
    )
    granted_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    review: Mapped[SrReview] = relationship(back_populates="memberships")


class SrCandidate(Base):
    """A paper proposed for inclusion in an SR project.

    Ingested from the search-query fan-out; deduped against the shared
    `Publication` cache (R0) so the same paper across PubMed + Europe PMC
    collapses to one candidate. `current_status` tracks the candidate's
    journey through the two-phase screening funnel; it's recomputed from
    the `ScreeningDecision` rows whenever a new decision lands.
    """

    __tablename__ = "sr_candidates"
    __table_args__ = (
        UniqueConstraint("sr_review_id", "publication_id", name="uq_sr_candidate_publication"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    sr_review_id: Mapped[str] = mapped_column(
        ForeignKey("sr_reviews.id", ondelete="CASCADE"), index=True
    )
    publication_id: Mapped[str] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT"), index=True
    )
    pmid: Mapped[str | None] = mapped_column(Text, nullable=True, default=None, index=True)
    source_origin: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Which source surfaced this paper first (pubmed | europepmc | upload | …).",
    )
    current_status: Mapped[str] = mapped_column(
        Text,
        default="pending_abstract",
        doc=(
            "pending_abstract | pending_adjudication_abstract | "
            "included_after_abstract | excluded_at_abstract | "
            "pending_fulltext | pending_adjudication_fulltext | "
            "included_after_fulltext | excluded_at_fulltext"
        ),
    )
    excluded_reason_code: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    excluded_at_phase: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="abstract | fulltext"
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    review: Mapped[SrReview] = relationship(back_populates="candidates")
    publication: Mapped[Publication] = relationship()
    decisions: Mapped[list[ScreeningDecision]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    ai_suggestions: Mapped[list[AiSuggestion]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class ScreeningDecision(Base):
    """One reviewer's judgement of one candidate at one phase.

    Unique on (candidate, reviewer, phase) so a reviewer can revise their
    own decision (the row is upserted) but each reviewer slot only counts
    once per phase. Disagreement between R1 and R2 raises the candidate to
    pending_adjudication; the adjudicator's decision wins and is stored on
    the same table with `role='adjudicator'`.
    """

    __tablename__ = "sr_screening_decisions"
    __table_args__ = (
        UniqueConstraint(
            "sr_candidate_id",
            "reviewer_user_id",
            "phase",
            name="uq_sr_decision_unique",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    sr_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("sr_candidates.id", ondelete="CASCADE"), index=True
    )
    reviewer_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(Text, doc="reviewer_1 | reviewer_2 | adjudicator")
    phase: Mapped[str] = mapped_column(Text, doc="abstract | fulltext")
    decision: Mapped[str] = mapped_column(Text, doc="include | exclude | maybe")
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    ai_suggested: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        doc=(
            "True when the reviewer accepted the AI-assist suggestion as-is "
            "(useful for measuring AI-assist agreement)."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    candidate: Mapped[SrCandidate] = relationship(back_populates="decisions")


class AiSuggestion(Base):
    """AI-assist's predicted screening decision for one candidate at one phase.

    Written by the batch classifier; surfaced next to the abstract in the
    screening UI. Persisted separately from `ScreeningDecision` so we can
    measure prediction accuracy vs. human judgement and so a stale
    suggestion never accidentally gets counted as a real decision.
    """

    __tablename__ = "sr_ai_suggestions"
    __table_args__ = (
        UniqueConstraint("sr_candidate_id", "phase", name="uq_sr_ai_suggestion_unique"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    sr_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("sr_candidates.id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[str] = mapped_column(Text, doc="abstract | fulltext")
    predicted_decision: Mapped[str] = mapped_column(Text, doc="include | exclude | maybe")
    predicted_reason_code: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    model_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Bedrock model id used so we can detect stale predictions.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    candidate: Mapped[SrCandidate] = relationship(back_populates="ai_suggestions")
