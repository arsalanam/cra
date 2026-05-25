"""ORM models for the clinical-data (PHI) store — eCRF capture (E1).

Their own `ClinicalBase` (separate from the research-app `Base`) so the two
metadatas never cross-create. Hierarchy (design-doc §9.2):

    StudyDeployment -> Site
                    -> DeployedForm        (snapshot of a published form)
                    -> Subject -> EventInstance -> FormInstance -> ItemData

`AuditEntry` (§9.3) is append-only and records every data-affecting action.
References to the research-app form definitions are by *value* (id/version)
plus a JSON snapshot in `DeployedForm` — there is no cross-database FK, so the
PHI store interprets its own data without touching the research DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class ClinicalBase(DeclarativeBase):
    pass


class StudyDeployment(ClinicalBase):
    """A research study bound to live data collection. References the research
    `EcrfStudy` by id (cross-store, by value)."""

    __tablename__ = "study_deployments"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    research_study_id: Mapped[str] = mapped_column(Text, index=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active", doc="active | closed")
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    sites: Mapped[list[Site]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )
    deployed_forms: Mapped[list[DeployedForm]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )
    subjects: Mapped[list[Subject]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )


class Site(ClinicalBase):
    """A participating site within a deployment."""

    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    deployment: Mapped[StudyDeployment] = relationship(back_populates="sites")


class DeployedForm(ClinicalBase):
    """An immutable snapshot of a published research FormDefinition (decision:
    snapshot into the clinical store so it is self-contained)."""

    __tablename__ = "deployed_forms"
    __table_args__ = (
        UniqueConstraint("deployment_id", "form_name", "version", name="uq_deployed_form"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    form_def_id: Mapped[str] = mapped_column(Text, doc="Research EcrfFormDefinition.id.")
    form_name: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    definition_json: Mapped[str] = mapped_column(Text, doc="Snapshot of the published definition.")
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    deployment: Mapped[StudyDeployment] = relationship(back_populates="deployed_forms")


class Subject(ClinicalBase):
    """A study subject. Identified by a study-issued `subject_code`; direct
    identifiers are minimised (captured form data lives in ItemData)."""

    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("deployment_id", "subject_code", name="uq_subject_code"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"), index=True)
    subject_code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="enrolled")
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    deployment: Mapped[StudyDeployment] = relationship(back_populates="subjects")
    events: Mapped[list[EventInstance]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )
    form_instances: Mapped[list[FormInstance]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )


class EventInstance(ClinicalBase):
    """An actual occurrence of a scheduled visit/event for a subject."""

    __tablename__ = "event_instances"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(Text, doc="ScheduledEvent.id from the visit schedule.")
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    subject: Mapped[Subject] = relationship(back_populates="events")


class FormInstance(ClinicalBase):
    """A subject's instance of a deployed form (the unit data is entered into)."""

    __tablename__ = "form_instances"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    event_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("event_instances.id", ondelete="SET NULL"), nullable=True, default=None
    )
    deployed_form_id: Mapped[str] = mapped_column(
        ForeignKey("deployed_forms.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(Text, default="blank", doc="blank | in_progress | complete")
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    subject: Mapped[Subject] = relationship(back_populates="form_instances")
    items: Mapped[list[ItemData]] = relationship(
        back_populates="form_instance", cascade="all, delete-orphan"
    )


class ItemData(ClinicalBase):
    """One captured value for one item on a form instance."""

    __tablename__ = "item_data"
    __table_args__ = (UniqueConstraint("form_instance_id", "item_id", name="uq_item_data"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    form_instance_id: Mapped[str] = mapped_column(
        ForeignKey("form_instances.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str] = mapped_column(Text, doc="Item.id from the form definition.")
    value: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    entered_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    form_instance: Mapped[FormInstance] = relationship(back_populates="items")


class ParticipantAccess(ClinicalBase):
    """A magic-link/token grant letting a participant fill their own ePRO forms
    (eCRF E4b, design O1). Token is stored HASHED; the raw token only ever lives
    in the issued link. Scoped to a single subject; consent is captured before
    first entry."""

    __tablename__ = "participant_access"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, index=True)
    status: Mapped[str] = mapped_column(Text, default="active", doc="active | revoked")
    consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Query(ClinicalBase):
    """A data discrepancy/query against a captured item (eCRF E2, design §12).

    `auto` queries are raised by the system on a failed soft edit-check and
    auto-closed when the check later passes; `manual` queries are raised by a
    data manager / monitor. Lifecycle: open -> answered -> closed (reopenable).
    Every transition is recorded via the audit writer.
    """

    __tablename__ = "queries"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    form_instance_id: Mapped[str] = mapped_column(
        ForeignKey("form_instances.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str] = mapped_column(Text, index=True)
    item_id: Mapped[str] = mapped_column(Text)
    check_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    query_type: Mapped[str] = mapped_column(Text, default="manual", doc="auto | manual")
    severity: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    status: Mapped[str] = mapped_column(Text, default="open", doc="open | answered | closed")
    text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    responses: Mapped[list[QueryResponse]] = relationship(
        back_populates="query", cascade="all, delete-orphan", order_by="QueryResponse.created_at"
    )


class QueryResponse(ClinicalBase):
    """A response/comment on a query (the answer thread)."""

    __tablename__ = "query_responses"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    query_id: Mapped[str] = mapped_column(ForeignKey("queries.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    author_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    query: Mapped[Query] = relationship(back_populates="responses")


class Signature(ClinicalBase):
    """An electronic signature on a form instance (eCRF E5; Part 11 §11.50/70).

    Records the signer, the meaning of the signature, when it was applied, and
    a `content_hash` binding it to the exact data state signed. Editing a signed
    form is blocked; unlocking voids the signature (`voided=True`) and is
    audited — so any change invalidates the signature.
    """

    __tablename__ = "signatures"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    form_instance_id: Mapped[str] = mapped_column(
        ForeignKey("form_instances.id", ondelete="CASCADE"), index=True
    )
    signer_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    meaning: Mapped[str] = mapped_column(Text, doc="e.g. 'PI sign-off: data accurate & complete'.")
    content_hash: Mapped[str] = mapped_column(Text, doc="SHA-256 of the signed item values.")
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class AuditEntry(ClinicalBase):
    """Append-only audit trail (ALCOA+ / 21 CFR Part 11 §11.10(e)).

    Written for EVERY data-affecting action and never updated or deleted.
    `form_instance_id` is denormalised so the full history of a form instance
    (form-level + item-level events) can be queried directly.
    """

    __tablename__ = "audit_entries"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    entity_type: Mapped[str] = mapped_column(Text, doc="e.g. subject | form_instance | item_data")
    entity_id: Mapped[str] = mapped_column(Text, index=True)
    form_instance_id: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True, default=None
    )
    action: Mapped[str] = mapped_column(Text, doc="create | update | status | deploy | ...")
    item_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    actor_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    actor_role: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    source: Mapped[str] = mapped_column(Text, default="edc", doc="edc | epro | import | system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
