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

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint
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
    # Account-layer linkage (Sprint A1). By-value because AccountSite lives
    # in the research DB. Nullable for backwards-compat.
    account_site_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        index=True,
        doc=(
            "By-value FK to research-DB AccountSite.id. NULL = pre-account "
            "legacy site OR a site without an institutional counterpart yet."
        ),
    )
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
    baseline_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        doc=(
            "Per-subject baseline date — anchor for the visit-schedule "
            "day_offset arithmetic (P1 #4). Set explicitly via the "
            "calendar API; PlannedVisit generation falls back to "
            "`created_at` when None."
        ),
    )

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
    expectedness: Mapped[str] = mapped_column(
        Text,
        default="unknown",
        server_default="unknown",
        doc=(
            "expected | unexpected | unknown — assessed against the "
            "Reference Safety Information (Investigator's Brochure). "
            "'unexpected' is the SUSAR trigger (safety_rules.is_susar)."
        ),
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
            "drug_compliance | temp_excursion | ae_not_reported | "
            "data_capture | other"
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
        doc=("VS test code from SDTM CT (e.g. HEIGHT, WEIGHT, SYSBP, DIABP, PULSE, TEMP)."),
    )
    VSTEST: Mapped[str] = mapped_column(Text, doc="Human-readable test name.")
    VSORRES: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    VSORRESU: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    VSDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmLb(ClinicalBase):
    """SDTM Laboratory Tests row — one per lab measurement.

    LBORRES / LBORRESU is the value-as-collected; LBSTRESN /
    LBSTRESC / LBSTRESU is the standardised numeric + character form
    after unit conversion (we currently emit STRES* = ORRES* until a
    deploy-time unit-conversion table is wired in). LBNRIND derives
    from LBSTRESN vs LBSTNRLO/HI: NORMAL / LOW / HIGH / (blank when
    not computable).
    """

    __tablename__ = "sdtm_lb"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "LBSEQ", name="uq_sdtm_lb_lbseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="LB")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    LBSEQ: Mapped[int] = mapped_column(Integer)
    LBTESTCD: Mapped[str] = mapped_column(
        Text, doc="LB test code from SDTM CT (e.g. HGB, GLUC, ALT, CREAT)."
    )
    LBTEST: Mapped[str] = mapped_column(Text, doc="Human-readable test name.")
    LBORRES: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    LBORRESU: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    LBSTRESC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    LBSTRESN: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    LBSTRESU: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    LBORNRLO: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    LBORNRHI: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    LBSTNRLO: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    LBSTNRHI: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    LBNRIND: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Reference range indicator: NORMAL / LOW / HIGH (blank if N/A).",
    )
    LBDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmEx(ClinicalBase):
    """SDTM Exposure row — one per dose administration."""

    __tablename__ = "sdtm_ex"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "EXSEQ", name="uq_sdtm_ex_exseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="EX")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    EXSEQ: Mapped[int] = mapped_column(Integer)
    EXTRT: Mapped[str] = mapped_column(Text, doc="Treatment name (free text).")
    EXDOSE: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    EXDOSU: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    EXROUTE: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="ORAL / IV / IM / SC / TOPICAL / INHALATION / NASAL / RECTAL — controlled-term.",
    )
    EXSTDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    EXENDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmCm(ClinicalBase):
    """SDTM Concomitant Medications row — one per concomitant medication."""

    __tablename__ = "sdtm_cm"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "CMSEQ", name="uq_sdtm_cm_cmseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="CM")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    CMSEQ: Mapped[int] = mapped_column(Integer)
    CMTRT: Mapped[str] = mapped_column(Text, doc="Reported medication name (verbatim).")
    CMDECOD: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "WHODrug / ATC standardised name (free text until a dictionary "
            "is wired in at deploy time — analogous to MedDRA PT on AE)."
        ),
    )
    CMINDC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None, doc="Indication.")
    CMDOSE: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    CMDOSU: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    CMSTDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    CMENDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmMh(ClinicalBase):
    """SDTM Medical History row — one per pre-existing condition."""

    __tablename__ = "sdtm_mh"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "MHSEQ", name="uq_sdtm_mh_mhseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="MH")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    MHSEQ: Mapped[int] = mapped_column(Integer)
    MHTERM: Mapped[str] = mapped_column(Text, doc="Reported condition (verbatim).")
    MHDECOD: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="MedDRA Preferred Term — free text until MedDRA license at deploy.",
    )
    MHCAT: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="High-level category (e.g. CARDIOVASCULAR, RESPIRATORY).",
    )
    MHSTDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    MHENDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    MHONGO: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Y/N — derived: Y when MHENDTC is missing (ongoing condition).",
    )

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmDa(ClinicalBase):
    """SDTM Drug Accountability row — one per accountability measurement.

    A Findings-class domain derived from the IP dispense / return records
    (not from a form). Each DrugDispensation becomes a DISPAMT (Dispensed
    Amount) row and each DrugReturn a RETURNED (Returned Amount) row, keyed
    by `DAREFID` = the kit id so a dispense and its return cross-reference.
    `DATESTCD` values should be validated against the DA test-code CT at
    deploy — the MVP emits the two amounts that drive reconciliation.
    """

    __tablename__ = "sdtm_da"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "DASEQ", name="uq_sdtm_da_daseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="DA")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    DASEQ: Mapped[int] = mapped_column(Integer, doc="Sequence number within subject.")
    DAREFID: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="Reference ID — the kit id."
    )
    DATESTCD: Mapped[str] = mapped_column(Text, doc="DISPAMT | RETURNED (DA test code).")
    DATEST: Mapped[str] = mapped_column(Text, doc="Human-readable test name.")
    DAORRES: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="Result as collected (character)."
    )
    DAORRESU: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    DASTRESN: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None, doc="Standardised numeric result."
    )
    DASTRESU: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    DADTC: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="Date/time of collection (ISO 8601)."
    )

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmSv(ClinicalBase):
    """SDTM Subject Visits row — one per subject per completed visit.

    Derived from the PlannedVisit calendar: a visit that reached
    status='completed' becomes an SV record dated at `completed_at` (the
    actual visit) falling back to `planned_date`. VISIT / VISITNUM come
    from the linked ScheduledVisit (name + day-offset ordering). Visits
    still pending / missed / cancelled are NOT emitted — SV captures
    visits that actually occurred.
    """

    __tablename__ = "sdtm_sv"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "SVSEQ", name="uq_sdtm_sv_svseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="SV")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    SVSEQ: Mapped[int] = mapped_column(Integer, doc="Sequence number within subject.")
    VISITNUM: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None, doc="Visit number (day-offset ordering)."
    )
    VISIT: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    SVSTDTC: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="Start date/time of the visit (ISO 8601)."
    )
    SVENDTC: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SdtmDs(ClinicalBase):
    """SDTM Disposition row — one disposition event per subject.

    MINIMAL derivation from `Subject.status`: the platform does not yet
    capture a per-subject disposition lifecycle (withdrawals, completion
    dates, screen-failure reasons live in the screening log, not linked by
    USUBJID). Today this emits one DISPOSITION EVENT per enrolled subject
    with DSDECOD mapped from the coarse status, DSSTDTC = the subject
    reference (baseline) date as the available anchor. Enrich when a
    dedicated disposition-capture surface lands.
    """

    __tablename__ = "sdtm_ds"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "DSSEQ", name="uq_sdtm_ds_dsseq"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    DOMAIN: Mapped[str] = mapped_column(Text, default="DS")
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    DSSEQ: Mapped[int] = mapped_column(Integer, doc="Sequence number within subject.")
    DSTERM: Mapped[str] = mapped_column(Text, doc="Reported disposition term (verbatim status).")
    DSDECOD: Mapped[str] = mapped_column(Text, doc="Standardised disposition (CT).")
    DSCAT: Mapped[str] = mapped_column(Text, default="DISPOSITION EVENT")
    DSSTDTC: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="Start date of the disposition event (ISO 8601)."
    )

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


class AdamAdtte(ClinicalBase):
    """ADaM Time-to-Event analysis dataset — one row per subject × PARAMCD.

    PARAMCD enumerated for the MVP slice: TTAE (time to first AE),
    TTSAE (time to first SAE), DEATH (overall survival). AVAL is the
    elapsed time in AVALU (DAYS for the MVP). CNSR follows the SDTM/
    ADaM convention: 0 = event observed, 1 = censored. SRCDOM /
    SRCVAR carry the regulator-required audit trail back to the
    source (e.g. SRCDOM=AE / SRCVAR=AESTDTC for TTAE event rows).
    """

    __tablename__ = "adam_adtte"
    __table_args__ = (
        UniqueConstraint("deployment_id", "USUBJID", "PARAMCD", name="uq_adam_adtte_param"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    STUDYID: Mapped[str] = mapped_column(Text)
    USUBJID: Mapped[str] = mapped_column(Text, index=True)
    PARAMCD: Mapped[str] = mapped_column(Text, doc="TTAE | TTSAE | DEATH (MVP).")
    PARAM: Mapped[str] = mapped_column(Text, doc="Human-readable parameter label.")
    AVAL: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None, doc="Analysis value (time in AVALU)."
    )
    AVALU: Mapped[str] = mapped_column(Text, default="DAYS")
    CNSR: Mapped[int] = mapped_column(
        Integer, doc="0 = event observed; 1 = censored (ADaM convention)."
    )
    STARTDT: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Start date for the interval (usually DM.RFSTDTC).",
    )
    ADT: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Analysis date — event date when CNSR=0, censor date when CNSR=1.",
    )
    EVNTDESC: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Description of the observed event (free text).",
    )
    SRCDOM: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Source SDTM domain (e.g. AE, DM) — for regulator audit trail.",
    )
    SRCVAR: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Source variable in SRCDOM (e.g. AESTDTC, RFSTDTC).",
    )
    TRT01P: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    TRT01A: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

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


# ── IRT / Randomisation (eCRF E8 — RCT enabler) ─────────────────────────


class RandomizationSchedule(ClinicalBase):
    """A deployment-wide randomisation schedule.

    Generated once by the data_manager at study-start; allocations
    consume entries from the pre-generated `sequence_json` (simple /
    permuted-block / stratified-permuted-block) or, for `minimisation`,
    drive the Pocock-Simon update at allocation time.

    Only one *active* schedule per deployment_id (status='active').
    Closing the schedule (status='closed') is irreversible for audit
    integrity.
    """

    __tablename__ = "randomization_schedules"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    algorithm: Mapped[str] = mapped_column(
        Text,
        doc="simple | permuted_block | stratified_permuted_block | minimisation",
    )
    arms_json: Mapped[str] = mapped_column(Text, doc="JSON array of arm labels in canonical order.")
    ratio_json: Mapped[str] = mapped_column(
        Text,
        default="[1,1]",
        doc="JSON array of per-arm allocation ratios aligned to arms_json.",
    )
    block_sizes_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "JSON array of permitted block sizes (e.g. [4,6,8]). NULL for "
            "simple / minimisation algorithms."
        ),
    )
    strata_factors_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "JSON array of stratification factor names (e.g. ['site_id', "
            "'sex']). NULL for non-stratified schedules."
        ),
    )
    weights_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "Per-factor weights for minimisation (default = equal). "
            "JSON object {factor_name: weight}."
        ),
    )
    seed: Mapped[int] = mapped_column(
        Integer,
        doc="Seed for the underlying RNG — regulator-audited for reproducibility.",
    )
    blinding: Mapped[str] = mapped_column(
        Text,
        default="open_label",
        doc="open_label | single_blind | double_blind | triple_blind",
    )
    status: Mapped[str] = mapped_column(Text, default="active", doc="active | closed")
    sequence_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "JSON for pre-generated schedules: simple/block emit a flat "
            "list[arm]; stratified emits a {stratum_label: list[arm]} map. "
            "NULL for minimisation (state is dynamic in `minimisation_state_json`)."
        ),
    )
    minimisation_state_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "Running per-arm-per-factor counts for Pocock-Simon. JSON "
            "{arm: {factor: {level: count}}}. NULL for non-minimisation."
        ),
    )
    created_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class Allocation(ClinicalBase):
    """A subject's randomisation outcome — one row per randomised subject.

    Unique on subject_id so the API can return 409 on a double-allocate
    attempt. `unblinded=True` after a CodeBreakEvent flips it; that
    state is what lets the report endpoints expose the arm to the site
    in double-blind trials.
    """

    __tablename__ = "allocations"
    __table_args__ = (UniqueConstraint("subject_id", name="uq_allocation_subject"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("randomization_schedules.id", ondelete="RESTRICT"), index=True
    )
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    arm: Mapped[str] = mapped_column(Text)
    stratum_label: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "Canonical stratum label assembled as 'factor1=value1|"
            "factor2=value2|…' for stratified algorithms; NULL for "
            "non-stratified."
        ),
    )
    factor_values_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "JSON {factor_name: factor_value} captured at allocation time "
            "for the audit trail. NULL when no factors used."
        ),
    )
    sequence_position: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        doc=(
            "Position consumed from sequence_json (per stratum for "
            "stratified). NULL for minimisation."
        ),
    )
    allocated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    allocated_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    unblinded: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        doc=("True after a CodeBreakEvent. Open-label trials are always True at allocation time."),
    )
    unblinded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class CodeBreakEvent(ClinicalBase):
    """A PI-initiated emergency unblinding event (Part 11-style audit).

    Records the trigger, the reason captured at break time, and the
    sponsor-notification flag so the safety reporting workflow can
    pick it up.
    """

    __tablename__ = "code_break_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    allocation_id: Mapped[str] = mapped_column(
        ForeignKey("allocations.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[str] = mapped_column(Text, doc="Free-text clinical reason.")
    broken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    broken_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    sponsor_notified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        doc=(
            "Set True when the platform's safety workflow flags the "
            "sponsor (placeholder until a real notification surface lands)."
        ),
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


class ScreeningLog(ClinicalBase):
    """Recruitment / screening log row — one per prospect a site evaluates.

    Closes the operational-hygiene gap PIs ask about weekly: "how many people
    did we screen this month, how many were eligible, why were the rest
    excluded, and how many actually consented + enrolled?"

    Lifecycle:
      pending → eligible | screen_failure
                      ↓
             pending → consented | declined | withdrew
                                ↓
                       pending → enrolled | not_enrolled

    The screening_code is sponsor-assigned at first contact and is NOT the
    eventual USUBJID — the latter only exists after enrolment links the log
    to a `Subject` row via `enrolled_subject_id`. PHI minimisation: only the
    age band, sex, race, ethnicity, and DOB *year* (not full DOB) are stored;
    no name / DOB / MRN.

    Exclusion reasons follow the CONSORT 2010 standard reason set (see
    `recruitment_terminology.CONSORT_EXCLUSION_REASONS`).
    """

    __tablename__ = "screening_logs"
    __table_args__ = (
        UniqueConstraint("deployment_id", "screening_code", name="uq_screening_log_code"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[str | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
        doc="Optional — community-recruited prospects may not yet have a site.",
    )
    screening_code: Mapped[str] = mapped_column(
        Text,
        doc=(
            "Sponsor-assigned screening identifier (e.g. 'SCR-0042'). NOT the "
            "USUBJID — that only exists after enrolment."
        ),
    )
    screening_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Demographics — identity-light + NIH-diversity-reportable.
    age_band: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="One of the canonical age bands: <18 / 18-29 / 30-44 / 45-64 / 65-74 / 75+.",
    )
    sex: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="M | F | other | unknown"
    )
    race: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "OMB-1997 categorical (american_indian / asian / black / "
            "native_hawaiian / white / multiracial / other / unknown)."
        ),
    )
    ethnicity: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="OMB-1997 (hispanic_or_latino | not_hispanic_or_latino | unknown).",
    )
    dob_year: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        doc="Year-of-birth only — full DOB is identifying PHI.",
    )

    # Status pipeline.
    eligibility_status: Mapped[str] = mapped_column(
        Text,
        default="pending",
        doc="pending | eligible | screen_failure",
    )
    exclusion_reason_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="One of CONSORT_EXCLUSION_REASONS keys when status=screen_failure.",
    )
    exclusion_reason_text: Mapped[str] = mapped_column(Text, default="")

    consent_status: Mapped[str] = mapped_column(
        Text,
        default="pending",
        doc="pending | consented | declined | withdrew",
    )
    consent_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    enrolment_status: Mapped[str] = mapped_column(
        Text,
        default="pending",
        doc="pending | enrolled | not_enrolled",
    )
    enrolled_subject_id: Mapped[str | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
        doc="Set when the prospect enrols — links to the Subject row.",
    )
    enrolment_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Audit / provenance.
    recorded_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    notes: Mapped[str] = mapped_column(Text, default="")


# ── Visit scheduling + participant reminders (P1 #4) ────────────────────


class VisitSchedule(ClinicalBase):
    """Per-deployment named visit schedule. Only one schedule per deployment
    is `active=True` at a time — the active one is the source from which
    PlannedVisit rows are generated when a subject is enrolled.
    """

    __tablename__ = "visit_schedules"
    __table_args__ = (UniqueConstraint("deployment_id", "name", name="uq_visit_schedule_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    visits: Mapped[list[ScheduledVisit]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class ScheduledVisit(ClinicalBase):
    """One visit in a VisitSchedule.

    `day_offset` is days from the subject's baseline date (0 = baseline).
    `window_before_days` / `window_after_days` define the visit window
    (negative = before, positive = after) — e.g. a Week 4 visit with
    offset=28, window_before=3, window_after=3 means the visit can occur
    between days 25 and 31.

    `reminder_offsets_json` is a JSON array of ints (days before due_date)
    when a reminder should fire. Example: `[-7, -1, 0]` = 7 days before,
    1 day before, and morning of the visit.
    """

    __tablename__ = "scheduled_visits"
    __table_args__ = (
        UniqueConstraint("schedule_id", "visit_name", name="uq_scheduled_visit_name"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("visit_schedules.id", ondelete="CASCADE"), index=True
    )
    visit_name: Mapped[str] = mapped_column(Text)
    day_offset: Mapped[int] = mapped_column(Integer, doc="Days from baseline (0 = baseline visit).")
    window_before_days: Mapped[int] = mapped_column(Integer, default=0)
    window_after_days: Mapped[int] = mapped_column(Integer, default=0)
    reminder_offsets_json: Mapped[str] = mapped_column(
        Text,
        default="[-7, -1, 0]",
        doc="JSON list[int] of offsets (days before due_date) when reminders fire.",
    )
    ordering: Mapped[int] = mapped_column(Integer, default=0, doc="Sort order within schedule.")

    schedule: Mapped[VisitSchedule] = relationship(back_populates="visits")


class PlannedVisit(ClinicalBase):
    """Per-subject instance of a ScheduledVisit. Auto-generated when a
    subject is enrolled (or via the explicit generate endpoint).

    `override_reason` is non-empty when a coordinator has shifted the
    planned_date away from the schedule-implied date (e.g. subject
    travel, holiday). The audit trail records every override.
    """

    __tablename__ = "planned_visits"
    __table_args__ = (
        UniqueConstraint("subject_id", "scheduled_visit_id", name="uq_planned_visit"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    scheduled_visit_id: Mapped[str] = mapped_column(
        ForeignKey("scheduled_visits.id", ondelete="RESTRICT"), index=True
    )
    planned_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        Text,
        default="pending",
        doc="pending | completed | missed | cancelled",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    override_reason: Mapped[str] = mapped_column(Text, default="")
    window_deviation_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "Id of the visit_window ProtocolDeviation auto-created when this "
            "visit aged past its window (see sweep_overdue_visits). Guards "
            "against duplicate deviations on re-sweeps; NULL until violated."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ParticipantContact(ClinicalBase):
    """Participant's reminder-channel preferences. Linked 1:1 with
    ParticipantAccess. PHI minimised: only created when the participant
    opts in to reminders at consent time.

    `opt_in_channels_json` is a JSON list of channels the participant
    has accepted (e.g. `["email"]`). When opt_out_at is set, the
    reminder pipeline skips this contact entirely.
    """

    __tablename__ = "participant_contacts"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    participant_access_id: Mapped[str] = mapped_column(
        ForeignKey("participant_access.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    preferred_channel: Mapped[str] = mapped_column(
        Text,
        default="email",
        doc="email | sms | none — sets the reminder pipeline's first-choice channel.",
    )
    opt_in_channels_json: Mapped[str] = mapped_column(
        Text,
        default='["email"]',
        doc="JSON list[str] — channels the participant has consented to.",
    )
    opt_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        doc="Set when the participant withdraws consent to reminders.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SentReminder(ClinicalBase):
    """Audit row written for every reminder the pipeline attempts to send.

    `provider` is the actual provider used (`ses`, `dry_run`, …). When
    SES creds are missing in env, the helper writes `provider='dry_run'`
    so the pipeline is testable without external IO. `error` carries any
    provider-side failure detail.

    Unique key `(planned_visit_id, offset_days, channel)` prevents double-
    sends — the scheduler is idempotent across restarts.
    """

    __tablename__ = "sent_reminders"
    __table_args__ = (
        UniqueConstraint(
            "planned_visit_id",
            "offset_days",
            "channel",
            name="uq_sent_reminder",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    planned_visit_id: Mapped[str] = mapped_column(
        ForeignKey("planned_visits.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str] = mapped_column(Text, index=True)
    channel: Mapped[str] = mapped_column(Text, doc="email | sms")
    offset_days: Mapped[int] = mapped_column(
        Integer, doc="Days before due_date this reminder was for (e.g. -7)."
    )
    provider: Mapped[str] = mapped_column(Text, doc="ses | twilio | dry_run")
    status: Mapped[str] = mapped_column(
        Text, default="queued", doc="queued | sent | failed | skipped"
    )
    recipient: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="Email or phone the reminder went to."
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


# ── Source-document extraction (P1 #5) ──────────────────────────────────


class SourceDocument(ClinicalBase):
    """An uploaded source-data file (CSV-only this slice).

    Provenance anchor for both prospective eCRF pre-fill AND retrospective
    extraction-table output. The platform stores the file's parsed rows
    but does NOT enforce de-identification — operators upload
    pre-de-identified data per the protocol's PHI policy.

    `content_hash` is the SHA-256 of the raw file bytes; uploading the
    same file twice in a deployment returns the existing row instead of
    re-ingesting. `headers_json` is a JSON list of the source column
    names so the mapping UI can offer field-name autocomplete.
    """

    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("deployment_id", "content_hash", name="uq_source_document_hash"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    headers_json: Mapped[str] = mapped_column(Text, default="[]")
    uploaded_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")

    rows: Mapped[list[SourceRow]] = relationship(
        back_populates="source_document", cascade="all, delete-orphan"
    )


class SourceRow(ClinicalBase):
    """One row from a SourceDocument.

    `payload_json` is the raw row as a JSON object keyed by header name.
    `subject_code_hint` is the value of the column the operator
    designated as the subject identifier in the file (e.g. 'mrn',
    'subject_id', 'patient_code') — used at apply-time to match the
    row to a Subject.
    """

    __tablename__ = "source_rows"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    row_index: Mapped[int] = mapped_column(
        Integer, doc="Zero-based row number in the source file (excludes header)."
    )
    payload_json: Mapped[str] = mapped_column(Text)
    subject_code_hint: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True, default=None
    )

    source_document: Mapped[SourceDocument] = relationship(back_populates="rows")


class ExtractionMapping(ClinicalBase):
    """Per-deployment + per-DeployedForm mapping spec.

    `mapping_json` is `{source_field: item_id}` — the source CSV's
    column → the form definition's Item.id. `subject_code_field` is
    the source column that carries the subject identifier (must match
    `SourceRow.subject_code_hint` for the apply step to find subjects).
    `version` bumps on every mutation so ExtractionFill rows can be
    tied to the exact mapping spec at apply time (regulator audit).

    Only one mapping per (deployment_id, deployed_form_id) is
    `is_active=True` at a time — the active one is what gets used
    by default at apply time.
    """

    __tablename__ = "extraction_mappings"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id",
            "deployed_form_id",
            "version",
            name="uq_extraction_mapping_version",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    deployed_form_id: Mapped[str] = mapped_column(
        ForeignKey("deployed_forms.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(Text, default="default")
    version: Mapped[int] = mapped_column(Integer, default=1)
    subject_code_field: Mapped[str] = mapped_column(
        Text,
        doc=(
            "Source column carrying the subject identifier. Used to "
            "match SourceRow → Subject at apply time."
        ),
    )
    mapping_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        doc="JSON object {source_field: item_id} from form definition.",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")


class ExtractionFill(ClinicalBase):
    """Per-cell audit row: source_row → target cell.

    Regulator can click any ItemData / extraction-table cell and trace
    it back to the exact source row + field + mapping version that
    produced it. `target_kind='item_data'` points at an
    `ItemData.id`; `target_kind='extraction_cell'` points at a
    flat extraction-row id (the apply-to-extraction-table path).

    `mapping_version` is denormalised so the audit survives the
    ExtractionMapping row being mutated (CASCADE-on-delete is
    deliberately RESTRICT for the mapping FK).
    """

    __tablename__ = "extraction_fills"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    source_row_id: Mapped[str] = mapped_column(
        ForeignKey("source_rows.id", ondelete="CASCADE"), index=True
    )
    source_field: Mapped[str] = mapped_column(Text)
    mapping_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_mappings.id", ondelete="RESTRICT"), index=True
    )
    mapping_version: Mapped[int] = mapped_column(Integer)
    target_kind: Mapped[str] = mapped_column(Text, doc="item_data | extraction_cell")
    target_id: Mapped[str] = mapped_column(Text, index=True)
    applied_value: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    applied_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── Drug accountability (P2 #3) ─────────────────────────────────────────


class InvestigationalProduct(ClinicalBase):
    """Per-deployment catalogue of the investigational products in use.

    The data manager / study designer registers each IP up front; receipts
    and dispensations reference the catalogue row by FK. Defines the
    accounting unit (`units`, typically 'tablet' / 'capsule' / 'ml') used
    across receipts + dispensations + returns for the same IP.
    """

    __tablename__ = "investigational_products"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id",
            "drug_name",
            "strength",
            name="uq_ip_deployment_name_strength",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    drug_name: Mapped[str] = mapped_column(Text)
    strength: Mapped[str] = mapped_column(
        Text,
        doc="e.g. '10 mg', '500 IU', '5 mg/ml'.",
    )
    units: Mapped[str] = mapped_column(
        Text,
        default="tablet",
        doc="Accounting unit: tablet | capsule | ml | mg | vial | kit.",
    )
    kit_id_pattern: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "Optional regex pattern that kit_ids for this IP must match "
            "(e.g. 'KIT-[0-9]{4}'). Validated as a compilable regex at "
            "registration and enforced (re.fullmatch) at dispense time."
        ),
    )
    status: Mapped[str] = mapped_column(Text, default="active")
    created_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")


class DrugReceipt(ClinicalBase):
    """A shipment of investigational product received at a site.

    Each shipment carries a lot_number (used downstream for the lot-level
    reconciliation rollup) + a quantity. `temp_excursion_flag` flags
    cold-chain breaks so the data-manager triage queue can pick them up;
    the reason text + corrective action live in `notes`.
    """

    __tablename__ = "drug_receipts"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[str | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
    )
    ip_id: Mapped[str] = mapped_column(
        ForeignKey("investigational_products.id", ondelete="RESTRICT"),
        index=True,
    )
    lot_number: Mapped[str] = mapped_column(Text, index=True)
    expiry_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    quantity_received: Mapped[int] = mapped_column(Integer)
    packing_slip_ref: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    temp_excursion_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    received_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    notes: Mapped[str] = mapped_column(Text, default="")


class DrugDispensation(ClinicalBase):
    """One dispense event — IP handed from site to a subject.

    `kit_id` is the sponsor-assigned dispensing unit (each kit is one
    physical container; e.g. a bottle with a sticker). The reconciliation
    rollup keys per (lot_number, kit_id). `planned_visit_id` links the
    dispense to the visit calendar from P1 #4 when available.
    """

    __tablename__ = "drug_dispensations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    ip_id: Mapped[str] = mapped_column(
        ForeignKey("investigational_products.id", ondelete="RESTRICT"),
        index=True,
    )
    lot_number: Mapped[str] = mapped_column(Text, index=True)
    kit_id: Mapped[str] = mapped_column(Text, index=True)
    quantity_dispensed: Mapped[int] = mapped_column(Integer)
    planned_visit_id: Mapped[str | None] = mapped_column(
        ForeignKey("planned_visits.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
    )
    dispensed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    dispensed_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    notes: Mapped[str] = mapped_column(Text, default="")


class DrugReturn(ClinicalBase):
    """Return event — subject brings unused IP back.

    State invariants enforced at the repository layer: a return must
    reference a prior dispensation (subject + kit_id pair), and
    quantity_used + quantity_lost + quantity_returned == quantity
    originally dispensed when reconciled. quantity_lost captures
    'subject misplaced 2 tablets' / damage / etc; quantity_used is the
    sponsor-reported compliance count.
    """

    __tablename__ = "drug_returns"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(Text, index=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    dispensation_id: Mapped[str] = mapped_column(
        ForeignKey("drug_dispensations.id", ondelete="RESTRICT"),
        index=True,
    )
    kit_id: Mapped[str] = mapped_column(Text, index=True)
    quantity_returned: Mapped[int] = mapped_column(Integer)
    quantity_used: Mapped[int] = mapped_column(Integer, default=0)
    quantity_lost: Mapped[int] = mapped_column(Integer, default=0)
    return_reason: Mapped[str] = mapped_column(
        Text,
        default="end_of_visit",
        doc=("end_of_visit | end_of_treatment | early_termination | adverse_event | other"),
    )
    returned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    returned_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    notes: Mapped[str] = mapped_column(Text, default="")


# ── Lab-data feeds (P2 #6) ──────────────────────────────────────────────


class LabBatch(ClinicalBase):
    """One ingest of a lab-data file or listener payload.

    Carries the source-format provenance + SHA-256 content hash so
    re-uploads of the same file dedupe. Children are LabResult rows
    parsed out of the batch. Audit trail writes per batch + per row.
    """

    __tablename__ = "lab_batches"
    __table_args__ = (
        UniqueConstraint("deployment_id", "content_hash", name="uq_lab_batch_content"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    source_format: Mapped[str] = mapped_column(
        Text,
        doc="hl7v2 | cdisc_lab | fhir.",
    )
    content_hash: Mapped[str] = mapped_column(
        Text,
        doc="SHA-256 of the raw payload (hex). Drives idempotent re-uploads.",
    )
    filename: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    raw_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    ingested_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")


class LabResult(ClinicalBase):
    """One parsed laboratory result row.

    Source-format-agnostic shape. Common fields filled by all three
    parsers (HL7 v2 ORU^R01, CDISC LAB tab, FHIR R4); format-specific
    extras live in `raw_segment_json` for traceability.

    Subject linkage: `subject_id` is nullable because a parsed message
    may carry a subject_code that doesn't yet match a registered
    Subject (e.g. lab arrives before randomisation completes). The
    `subject_code_hint` field preserves the raw id from the source
    so a later subject-create can backfill the link.
    """

    __tablename__ = "lab_results"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("lab_batches.id", ondelete="CASCADE"), index=True
    )
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("study_deployments.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
    )
    subject_code_hint: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "Raw subject identifier from the source message. Lets the "
            "platform link to a Subject row that's created later."
        ),
    )

    # Test identity.
    test_code: Mapped[str] = mapped_column(
        Text,
        doc=(
            "Code identifying the test — LOINC for HL7 / FHIR, "
            "LBTESTCD for CDISC LAB. Free-text if the source omits a "
            "code system."
        ),
    )
    test_name: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    # Result. value_numeric is None for qualitative results (e.g.
    # 'POSITIVE'), in which case value_text carries the answer.
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    units: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    # Reference range + abnormal flag.
    ref_range_low: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    ref_range_high: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    abnormal_flag: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc=(
            "HL7 OBX-8 flag (H / L / N / A / etc.) or the CDISC "
            "LBNRIND value (LOW / NORMAL / HIGH). NULL when the source "
            "didn't supply one."
        ),
    )

    # Specimen + timing.
    specimen_id: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Raw provenance — JSON snapshot of the source segment / row /
    # Observation so an auditor can trace any parsed field back to
    # the wire format.
    raw_segment_json: Mapped[str] = mapped_column(Text, default="{}")

    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
