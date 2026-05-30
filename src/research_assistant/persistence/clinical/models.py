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


class Verification(ClinicalBase):
    """Source-data verification of a captured item by a monitor (eCRF E6, SDV).

    A separate table (not a column on ItemData) so the clinical store gains the
    feature via create_all without an ItemData migration."""

    __tablename__ = "verifications"
    __table_args__ = (UniqueConstraint("form_instance_id", "item_id", name="uq_verification"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    form_instance_id: Mapped[str] = mapped_column(
        ForeignKey("form_instances.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str] = mapped_column(Text)
    verified_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SubjectSignature(ClinicalBase):
    """A subject-casebook sign-off (eCRF E6) — the next level of the lock
    hierarchy above the form-instance Signature."""

    __tablename__ = "subject_signatures"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    signer_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    meaning: Mapped[str] = mapped_column(Text)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class StudyLock(ClinicalBase):
    """A deployment-wide database lock (eCRF E7 — validation pack).

    The last step before analysis: blocks all data entry, signing, SDV,
    and signoff across every subject in the deployment. Distinct from
    form-level + subject-level lock — those are casebook-progress steps;
    this is the regulatory database-lock event.

    Only one *active* row per deployment_id (locked=True with no
    unlocked_at). Unlock writes back to the same row (sets unlocked_at +
    unlock_reason + unlocked_by_sub) rather than deleting — so the lock
    history is preserved for audit.
    """

    __tablename__ = "study_locks"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    locked_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    lock_reason: Mapped[str] = mapped_column(Text, doc="Free-text reason captured at lock.")
    unlocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    unlocked_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    unlock_reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


# ── Safety subsystem (AE/SAE + protocol deviations + CAPA) — top-6 #4 ────


class AdverseEvent(ClinicalBase):
    """An adverse event recorded against a subject.

    Captured by coordinators at the point of care (`ae.record`), then
    reviewed by the PI who can override the auto-classifier
    (`ae.classify`). The auto-classification fires at write time via
    `safety_rules.auto_classify_serious(...)`; the resulting
    `is_serious` + `serious_reasons` are PERSISTED so a later rule
    change doesn't quietly re-classify historical events. `reportable_
    deadline` is the platform's 24-hour internal escalation timer
    (NOT the FDA regulatory clock — that's documented in the IND
    safety report draft).

    `meddra_pt` is captured as free text — real MedDRA preferred-term
    validation requires a license at deploy time. Treat the field as
    "what the PI chose to code this as"; for production use, wire a
    MedDRA dictionary into the validate-on-write path.

    `form_instance_id` is nullable — AEs can be captured from a
    dedicated form instance OR ad-hoc from the safety panel. When
    present, the link gives a one-click jump from the AE record back
    to the source form.
    """

    __tablename__ = "adverse_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    form_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("form_instances.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
    )

    term_text: Mapped[str] = mapped_column(Text, doc="Verbatim AE description from the reporter.")
    meddra_pt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "MedDRA Preferred Term — free text in MVP; deploy with a "
            "MedDRA license to validate against the dictionary."
        ),
    )

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    severity_grade: Mapped[int] = mapped_column(
        Integer,
        doc="CTCAE 1–5 scale; 5 = death related to AE.",
    )
    outcome: Mapped[str] = mapped_column(
        Text,
        default="unknown",
        doc="recovered | recovering | not_recovered | death | unknown",
    )
    relationship_to_intervention: Mapped[str] = mapped_column(
        Text,
        default="unknown",
        doc="unrelated | unlikely | possible | probable | definite | unknown",
    )

    is_serious: Mapped[bool] = mapped_column(Boolean, default=False)
    serious_reasons_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
        doc="JSON list of safety_rules.SeriousReason values that fired.",
    )

    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        doc="When the event was first recorded in the platform.",
    )
    reportable_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        doc=(
            "Platform 24h internal-triage deadline; populated for serious "
            "AEs. NULL when is_serious=False or after the event is reported."
        ),
    )
    reported_to_authority_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        doc="Set when the FDA 3500A (or equivalent) is generated + acknowledged.",
    )

    recorded_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    classified_by: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="The sub of the last PI/DM who reviewed + locked the classification.",
    )
    narrative: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Long-form clinical narrative; required for the FDA 3500A report.",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProtocolDeviation(ClinicalBase):
    """A deviation from the approved protocol.

    Logged by coordinators or monitors (`deviation.record`), classified
    by the data manager / PI (`deviation.classify`). A deviation can
    sit at `open` indefinitely; once a CAPA is added, status flips to
    `under_capa`. The PI closes the deviation (`capa.close`) only after
    every linked CapaAction is completed.

    `category` captures the kind of deviation so trends are queryable
    (e.g. recurring eligibility issues at a site point at process
    training needs). Free-text categories drift; a closed enum keeps
    reporting stable.
    """

    __tablename__ = "protocol_deviations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subject_id: Mapped[str | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
        doc=(
            "Nullable for deployment-wide deviations (e.g. a temperature "
            "excursion in the central drug supply) that don't tie to a "
            "single subject."
        ),
    )
    deployment_id: Mapped[str] = mapped_column(Text, index=True)

    classification: Mapped[str] = mapped_column(
        Text,
        default="minor",
        doc="major | minor | critical (drives reportability + DMC visibility)",
    )
    category: Mapped[str] = mapped_column(
        Text,
        doc=(
            "consent | eligibility | procedure | visit_window | "
            "drug_compliance | ae_not_reported | data_capture | other"
        ),
    )
    description: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    status: Mapped[str] = mapped_column(
        Text,
        default="open",
        doc="open | under_capa | closed",
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    discovered_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    classified_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    capas: Mapped[list[CapaAction]] = relationship(
        back_populates="deviation", cascade="all, delete-orphan"
    )


class CapaAction(ClinicalBase):
    """A Corrective And Preventive Action attached to a deviation.

    Authored by the data manager (`capa.author`), executed by the
    assigned owner. The owner marks their own action complete; the PI
    closes the deviation once every CAPA is `completed`. An audit
    entry is written for every transition.
    """

    __tablename__ = "capa_actions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deviation_id: Mapped[str] = mapped_column(
        ForeignKey("protocol_deviations.id", ondelete="CASCADE"), index=True
    )
    action_text: Mapped[str] = mapped_column(Text)
    owner_sub: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "Cognito sub of the person assigned to execute. NULL = "
            "unassigned (the data manager will fill in)."
        ),
    )
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    status: Mapped[str] = mapped_column(Text, default="open", doc="open | completed")
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    completed_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    created_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    deviation: Mapped[ProtocolDeviation] = relationship(back_populates="capas")


# ── CDISC submission pipeline (SDTM → ADaM → TLF) — top-6 #6 ────────────


class SdtmDm(ClinicalBase):
    """SDTM Demographics domain row — one per subject (top-6 #6).

    The clinical-data store keeps a denormalised copy of the SDTM-shaped
    record after derivation so the submission bundle can ship without
    re-running the mapping pipeline. `STUDYID` + `USUBJID` form the
    natural key; the derivation refreshes records in place rather than
    appending versions (versioning lives on the CdiscDerivation row).

    Variable naming follows CDISC SDTMIG v3.4 conventions verbatim
    (uppercase) so the export step ships a regulator-shaped CSV without
    a translation layer.
    """

    __tablename__ = "sdtm_dm"
    __table_args__ = (UniqueConstraint("deployment_id", "USUBJID", name="uq_sdtm_dm_usubjid"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="DM")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    SUBJID: Mapped[str] = mapped_column(Text)
    SITEID: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    AGE: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    AGEU: Mapped[str] = mapped_column(Text, default="YEARS")
    SEX: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    RACE: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    ETHNIC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    RFSTDTC: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Reference start date — ISO 8601 (CDISC --DTC).",
    )
    RFENDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    ARM: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    ARMCD: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    COUNTRY: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmAe(ClinicalBase):
    """SDTM Adverse Events domain row — one per AdverseEvent.

    `AESEQ` is per-subject and assigned at derivation time so re-running
    yields stable sequence numbers (we order by AdverseEvent.reported_at).
    """

    __tablename__ = "sdtm_ae"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "AESEQ", name="uq_sdtm_ae_aeseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="AE")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    AESEQ: Mapped[int] = mapped_column(Integer, doc="Sequence number within subject.")
    AETERM: Mapped[str] = mapped_column(Text, doc="Verbatim reporter term.")
    AEDECOD: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="MedDRA Preferred Term (deploy with license for validation).",
    )
    AEBODSYS: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    AESTDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    AEENDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    AESEV: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="MILD / MODERATE / SEVERE / LIFE THREATENING / FATAL (SDTM CT).",
    )
    AESER: Mapped[str | None] = mapped_column(Text, nullable=True, default=None, doc="Y / N.")
    AEREL: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    AEOUT: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmVs(ClinicalBase):
    """SDTM Vital Signs domain row — one per vital-sign measurement."""

    __tablename__ = "sdtm_vs"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "VSSEQ", name="uq_sdtm_vs_vsseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="VS")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    VSSEQ: Mapped[int] = mapped_column(Integer)
    VSTESTCD: Mapped[str] = mapped_column(
        Text,
        doc=(
            "VS test code from SDTM CT (e.g. HEIGHT, WEIGHT, SYSBP, DIABP, "
            "PULSE, TEMP)."
        ),
    )
    VSTEST: Mapped[str] = mapped_column(Text, doc="Human-readable test name.")
    VSORRES: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    VSORRESU: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    VSDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AdamAdsl(ClinicalBase):
    """ADaM Subject-Level Analysis Dataset — one row per subject.

    Built from SdtmDm + SdtmAe. Carries the population flags
    (SAFFL / ITTFL / DTHFL) regulators expect on every ADaM analysis.
    Treatment fields are placeholder ("TBD") when randomisation isn't
    captured — randomisation/IRT integration is on the roadmap separately.
    """

    __tablename__ = "adam_adsl"
    __table_args__ = (UniqueConstraint("deployment_id", "USUBJID", name="uq_adam_adsl_usubjid"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    SUBJID: Mapped[str] = mapped_column(Text)
    SITEID: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    AGE: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    AGEU: Mapped[str] = mapped_column(Text, default="YEARS")
    AGEGR1: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Age group bin — <18 / 18-64 / 65-74 / >=75 (typical ICH E1).",
    )
    SEX: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    RACE: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    ETHNIC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    SAFFL: Mapped[str] = mapped_column(
        Text,
        default="N",
        doc="Safety analysis flag — Y when subject had any post-baseline data.",
    )
    ITTFL: Mapped[str] = mapped_column(Text, default="Y", doc="Intent-to-treat flag.")
    DTHFL: Mapped[str] = mapped_column(
        Text,
        default="N",
        doc="Death flag — Y when any AE has outcome=death.",
    )
    RFSTDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    RFENDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    TRT01P: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Planned treatment for period 1 — TBD until randomisation lands.",
    )
    TRT01A: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    COUNTRY: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TlfArtefact(ClinicalBase):
    """A generated Table / Listing / Figure for the submission bundle.

    Tables + listings render as tabular JSON (rows: list[dict]); figures
    ship as hand-rolled SVG (same posture as the PRISMA + AE-frequency
    pattern elsewhere). PDF bundling happens at export time; in-store we
    keep the raw content so a re-run re-materialises without reaching
    back into the source data.
    """

    __tablename__ = "tlf_artefacts"
    __table_args__ = (
        UniqueConstraint("deployment_id", "kind", "tlf_id", name="uq_tlf_artefact_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    kind: Mapped[str] = mapped_column(Text, doc="'table' | 'listing' | 'figure'")
    tlf_id: Mapped[str] = mapped_column(
        Text, doc="Stable id — e.g. 't-disposition', 't-demographics', 'f-ae-frequency'."
    )
    title: Mapped[str] = mapped_column(Text)
    content_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="JSON shape for tables/listings: {columns: [...], rows: [[...]]}.",
    )
    svg_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Hand-rolled SVG for figures.",
    )
    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CdiscDerivation(ClinicalBase):
    """An audit-style row tracking each derivation run for a deployment.

    A single row per (deployment, run) carrying the trigger sub + counts.
    The derivation pipeline upserts SDTM/ADaM/TLF rows in place; this
    table is the only place where "when did we re-derive" is recoverable.
    """

    __tablename__ = "cdisc_derivations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    triggered_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    counts_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        doc="JSON {dm: n, ae: n, vs: n, adsl: n, tlf: n} — quick summary.",
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
