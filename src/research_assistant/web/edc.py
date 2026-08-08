"""EDC capture API (eCRF E1) — the collection plane.

Distinct from the `/api/ecrf/*` authoring API: this namespace deploys a
published study for data collection and captures subject data into the
separate clinical-data (PHI) store, with a full audit trail. Writes are
gated per the permission matrix in `rbac-design.md` §4.5 — each handler's
dependency names the permission it requires and the scope (deployment,
subject, or form_instance) it resolves against.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from ..auth import SessionPayload
from ..auth.cognito_admin import (
    CognitoAdminError,
    verify_user_password_async,
)
from ..auth.rbac import Permission
from ..config import get_settings
from ..domain.ecrf import FormDefinition
from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.models import Allocation, RandomizationSchedule, Subject
from ..persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
    FormSnapshot,
    HardCheckError,
    LockedError,
)
from ..persistence.clinical.safety_rules import is_susar
from ..persistence.database import get_db_session
from ..persistence.ecrf_repository import EcrfRepository
from .auth import CurrentUser
from .authz import require_permission_scoped

logger = logging.getLogger(__name__)


# ── DTOs ──────────────────────────────────────────────────────────────────────


class DeploymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research_study_id: str
    name: str | None = None


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    research_study_id: str
    name: str
    status: str
    created_at: datetime


class SiteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    code: str | None = None


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    code: str | None


class DeployedFormOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    form_name: str
    version: int
    title: str


class DeployedFormDetailOut(DeployedFormOut):
    definition: FormDefinition


class SubjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: str
    subject_code: str


class SubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    site_id: str
    subject_code: str
    status: str


class EproAccessOut(BaseModel):
    token: str
    subject_id: str
    epro_path: str


class OpenFormIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployed_form_id: str
    event_instance_id: str | None = None


class ItemDataOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: str
    value: str | None
    updated_at: datetime


class FormInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    subject_id: str
    deployed_form_id: str
    status: str


class FormInstanceDetailOut(FormInstanceOut):
    items: list[ItemDataOut]


class SubmitDataIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str | None]
    reason: str | None = None
    mark_complete: bool = False


class QueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    item_id: str
    check_id: str | None
    query_type: str
    severity: str | None
    status: str
    text: str
    created_at: datetime


class ManualQueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    text: str


class QueryResponseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class SignIn(BaseModel):
    """Sign payload for both form-instance and casebook signing.

    `password` carries the second identification component per Part 11
    §11.200 (active session + explicit credential challenge). When
    auth is not configured (dev/test), the reauth check is bypassed
    and the field may be empty.
    """

    model_config = ConfigDict(extra="forbid")
    meaning: str
    password: str = ""


class UnlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str


class SignatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    signer_sub: str | None
    meaning: str
    signed_at: datetime
    voided: bool


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_ids: list[str]


class VerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: str
    verified_by: str | None
    verified_at: datetime


class SubjectSignIn(BaseModel):
    """Casebook sign-off payload. `password` mirrors :class:`SignIn`."""

    model_config = ConfigDict(extra="forbid")
    meaning: str
    password: str = ""


class StudyLockIn(BaseModel):
    """Deployment-wide study lock (E7 — validation pack)."""

    model_config = ConfigDict(extra="forbid")
    reason: str
    force_open_queries: bool = Field(
        default=False,
        description=(
            "When False (default), lock is refused if any non-closed "
            "query exists. When True, lock is forced through anyway — "
            "audited as such."
        ),
    )


class StudyUnlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str


class StudyLockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    locked_at: datetime
    locked_by_sub: str | None
    lock_reason: str
    unlocked_at: datetime | None
    unlocked_by_sub: str | None
    unlock_reason: str | None


class StudyLockStatusOut(BaseModel):
    deployment_id: str
    locked: bool
    active_lock: StudyLockOut | None


# ── IRT (eCRF E8) ──────────────────────────────────────────────────────


class ScheduleGenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    algorithm: str = Field(
        description="simple | permuted_block | stratified_permuted_block | minimisation",
    )
    arms: list[str] = Field(min_length=2)
    ratio: list[int] | None = None
    block_sizes: list[int] | None = None
    strata_factors: list[str] | None = None
    expected_per_stratum: dict[str, int] | None = Field(
        default=None,
        description=(
            "For stratified_permuted_block: {stratum_label: expected_N} so the "
            "generator emits a sequence per stratum. Operator computes this "
            "from the protocol's planned enrolment per stratum."
        ),
    )
    expected_n: int | None = Field(
        default=None,
        description=(
            "For simple / permuted_block: total expected enrolment so the "
            "schedule pre-generates the sequence. Ignored for stratified + "
            "minimisation."
        ),
    )
    weights: dict[str, float] | None = None
    seed: int | None = Field(
        default=None,
        description=(
            "Optional explicit seed for audit reproducibility. Server "
            "generates one if omitted (and returns it)."
        ),
    )
    blinding: str = Field(
        default="open_label",
        description="open_label | single_blind | double_blind | triple_blind",
    )


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    algorithm: str
    arms_json: str
    ratio_json: str
    block_sizes_json: str | None
    strata_factors_json: str | None
    weights_json: str | None
    seed: int
    blinding: str
    status: str
    created_by_sub: str | None
    created_at: datetime
    closed_at: datetime | None


class AllocateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor_values: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Subject-specific stratification factor values, captured at "
            "enrolment (e.g. {'site_id': 'S01', 'sex': 'F'}). REQUIRED for "
            "stratified_permuted_block + minimisation; ignored for simple "
            "and permuted_block."
        ),
    )


class AllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    schedule_id: str
    subject_id: str
    arm: str | None
    stratum_label: str | None
    sequence_position: int | None
    allocated_at: datetime
    allocated_by_sub: str | None
    unblinded: bool
    unblinded_at: datetime | None


class CodeBreakIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=8)


class CodeBreakOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    subject_id: str
    allocation_id: str
    reason: str
    broken_at: datetime
    broken_by_sub: str | None
    sponsor_notified: bool


class SubjectSignatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    signer_sub: str | None
    meaning: str
    signed_at: datetime
    voided: bool


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    action: str
    entity_type: str
    item_id: str | None
    old_value: str | None
    new_value: str | None
    reason: str | None
    actor_sub: str | None
    source: str
    created_at: datetime


# ── Safety subsystem (top-6 #4) ──────────────────────────────────────────


class AdverseEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term_text: str
    severity_grade: int = Field(ge=1, le=5)
    outcome: str = "unknown"
    relationship_to_intervention: str = "unknown"
    expectedness: str = "unknown"  # expected | unexpected | unknown (SUSAR trigger)
    start_date: datetime
    end_date: datetime | None = None
    hospitalisation_flag: bool = False
    life_threatening_flag: bool = False
    persistent_disability_flag: bool = False
    congenital_anomaly_flag: bool = False
    other_medically_significant_flag: bool = False
    narrative: str | None = None
    form_instance_id: str | None = None


class AdverseEventClassifyIn(BaseModel):
    """PI / DM override of the auto-classification."""

    model_config = ConfigDict(extra="forbid")
    is_serious: bool | None = None
    serious_reasons: list[str] | None = None
    outcome: str | None = None
    severity_grade: int | None = Field(default=None, ge=1, le=5)
    expectedness: str | None = None  # override the RSI assessment (can flip SUSAR)
    narrative: str | None = None


class AdverseEventMeddraIn(BaseModel):
    """Manually-coded MedDRA Preferred Term.

    Real MedDRA validation needs a deploy-time license; the field is
    free-text here so the workflow ships without that gate.
    """

    model_config = ConfigDict(extra="forbid")
    meddra_pt: str


class AdverseEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    subject_id: str
    deployment_id: str
    form_instance_id: str | None
    term_text: str
    meddra_pt: str | None
    start_date: datetime
    end_date: datetime | None
    severity_grade: int
    outcome: str
    relationship_to_intervention: str
    expectedness: str
    is_serious: bool
    is_susar: bool
    serious_reasons: list[str]
    reported_at: datetime
    reportable_deadline: datetime | None
    reported_to_authority_at: datetime | None
    recorded_by: str | None
    classified_by: str | None
    narrative: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_ae(cls, ae: Any) -> AdverseEventOut:
        return cls(
            id=ae.id,
            subject_id=ae.subject_id,
            deployment_id=ae.deployment_id,
            form_instance_id=ae.form_instance_id,
            term_text=ae.term_text,
            meddra_pt=ae.meddra_pt,
            start_date=ae.start_date,
            end_date=ae.end_date,
            severity_grade=ae.severity_grade,
            outcome=ae.outcome,
            relationship_to_intervention=ae.relationship_to_intervention,
            expectedness=ae.expectedness,
            is_serious=ae.is_serious,
            is_susar=is_susar(
                is_serious=ae.is_serious,
                relationship_to_intervention=ae.relationship_to_intervention,
                expectedness=ae.expectedness,
            ),
            serious_reasons=json.loads(ae.serious_reasons_json or "[]"),
            reported_at=ae.reported_at,
            reportable_deadline=ae.reportable_deadline,
            reported_to_authority_at=ae.reported_to_authority_at,
            recorded_by=ae.recorded_by,
            classified_by=ae.classified_by,
            narrative=ae.narrative,
            created_at=ae.created_at,
            updated_at=ae.updated_at,
        )


class DeviationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classification: str  # major | minor | critical
    category: str
    description: str
    root_cause: str | None = None
    subject_id: str | None = None  # only on the deployment-scoped variant


class DeviationClassifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classification: str | None = None
    category: str | None = None
    root_cause: str | None = None


class DeviationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    subject_id: str | None
    deployment_id: str
    classification: str
    category: str
    description: str
    root_cause: str | None
    status: str
    discovered_at: datetime
    discovered_by: str | None
    classified_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None


class CapaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_text: str
    owner_sub: str | None = None
    due_date: datetime | None = None


# ── Screening / recruitment log DTOs (P1 #3) ─────────────────────────────


class ScreeningLogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screening_code: str
    site_id: str | None = None
    screening_date: datetime | None = None
    age_band: str | None = None
    sex: str | None = None
    race: str | None = None
    ethnicity: str | None = None
    dob_year: int | None = None
    notes: str = ""


class ScreeningEligibilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligibility_status: str  # pending | eligible | screen_failure
    exclusion_reason_code: str | None = None
    exclusion_reason_text: str = ""


class ScreeningConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent_status: str  # pending | consented | declined | withdrew
    consent_date: datetime | None = None


class ScreeningEnrolmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrolment_status: str  # pending | enrolled | not_enrolled
    enrolled_subject_id: str | None = None
    enrolment_date: datetime | None = None


class ScreeningLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    site_id: str | None
    screening_code: str
    screening_date: datetime
    age_band: str | None
    sex: str | None
    race: str | None
    ethnicity: str | None
    dob_year: int | None
    eligibility_status: str
    exclusion_reason_code: str | None
    exclusion_reason_text: str
    consent_status: str
    consent_date: datetime | None
    enrolment_status: str
    enrolled_subject_id: str | None
    enrolment_date: datetime | None
    recorded_by_sub: str | None
    recorded_at: datetime
    updated_at: datetime
    notes: str


class RecruitmentFunnelOut(BaseModel):
    deployment_id: str
    totals: dict[str, int]
    screen_failures_by_reason: dict[str, int]
    per_week_per_site: dict[str, dict[str, dict[str, int]]]


# ── Visit scheduling + participant reminders DTOs (P1 #4) ────────────────


class VisitScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""


class VisitScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    name: str
    description: str
    is_active: bool
    created_by_sub: str | None
    created_at: datetime


class ScheduledVisitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visit_name: str
    day_offset: int
    window_before_days: int = 0
    window_after_days: int = 0
    reminder_offsets: list[int] | None = None
    ordering: int = 0


class ScheduledVisitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    schedule_id: str
    visit_name: str
    day_offset: int
    window_before_days: int
    window_after_days: int
    reminder_offsets_json: str
    ordering: int


class PlannedVisitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    subject_id: str
    scheduled_visit_id: str
    planned_date: datetime
    window_start: datetime
    window_end: datetime
    status: str
    completed_at: datetime | None
    override_reason: str
    created_at: datetime
    updated_at: datetime


class PlannedVisitUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planned_date: datetime | None = None
    status: str | None = None
    override_reason: str = ""


class GeneratePlannedVisitsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_date: datetime | None = None


class ParticipantContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str | None = None
    phone: str | None = None
    preferred_channel: str = "email"
    opt_in_channels: list[str] = Field(default_factory=lambda: ["email"])
    opt_out: bool = False


class ParticipantContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    participant_access_id: str
    subject_id: str
    email: str | None
    phone: str | None
    preferred_channel: str
    opt_in_channels_json: str
    opt_out_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SentReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    planned_visit_id: str
    subject_id: str
    channel: str
    offset_days: int
    provider: str
    status: str
    recipient: str | None
    error: str | None
    queued_at: datetime
    sent_at: datetime | None


class ReminderRunResultOut(BaseModel):
    deployment_id: str
    queued: int
    sent: int
    failed: int
    skipped: int


# ── Source-document extraction DTOs (P1 #5) ──────────────────────────────


class SourceDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    filename: str
    content_hash: str
    row_count: int
    headers_json: str
    uploaded_by_sub: str | None
    uploaded_at: datetime
    notes: str


class SourceRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_document_id: str
    row_index: int
    payload_json: str
    subject_code_hint: str | None


class ExtractionMappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployed_form_id: str
    subject_code_field: str
    mapping: dict[str, str]
    name: str = "default"
    notes: str = ""


class ExtractionMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    deployed_form_id: str
    name: str
    version: int
    subject_code_field: str
    mapping_json: str
    is_active: bool
    created_by_sub: str | None
    created_at: datetime
    notes: str


class ExtractionApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_document_id: str
    mode: str = "subjects"  # "subjects" → eCRF pre-fill; "table" → flat output


class ExtractionApplyResultOut(BaseModel):
    mode: str
    subjects_filled: int = 0
    items_written: int = 0
    source_rows_unmatched: int = 0
    table_rows: list[dict[str, object]] = Field(default_factory=list)


class ExtractionFillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_row_id: str
    source_field: str
    mapping_id: str
    mapping_version: int
    target_kind: str
    target_id: str
    applied_value: str | None
    applied_by_sub: str | None
    applied_at: datetime


# ── Drug accountability DTOs (P2 #3) ─────────────────────────────────────


class InvestigationalProductIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drug_name: str
    strength: str
    units: str = "tablet"
    kit_id_pattern: str | None = None
    notes: str = ""


class InvestigationalProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    drug_name: str
    strength: str
    units: str
    kit_id_pattern: str | None
    status: str
    created_by_sub: str | None
    created_at: datetime
    notes: str


class DrugReceiptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ip_id: str
    lot_number: str
    quantity_received: int
    site_id: str | None = None
    expiry_date: datetime | None = None
    packing_slip_ref: str | None = None
    temp_excursion_flag: bool = False
    notes: str = ""


class DrugReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    site_id: str | None
    ip_id: str
    lot_number: str
    expiry_date: datetime | None
    quantity_received: int
    packing_slip_ref: str | None
    temp_excursion_flag: bool
    received_at: datetime
    received_by_sub: str | None
    notes: str


class DrugDispensationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str
    ip_id: str
    lot_number: str
    kit_id: str
    quantity_dispensed: int
    planned_visit_id: str | None = None
    notes: str = ""


class DrugDispensationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    subject_id: str
    ip_id: str
    lot_number: str
    kit_id: str
    quantity_dispensed: int
    planned_visit_id: str | None
    dispensed_at: datetime
    dispensed_by_sub: str | None
    notes: str


class DrugReturnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quantity_returned: int
    quantity_used: int = 0
    quantity_lost: int = 0
    return_reason: str = "end_of_visit"
    notes: str = ""


class DrugReturnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    subject_id: str
    dispensation_id: str
    kit_id: str
    quantity_returned: int
    quantity_used: int
    quantity_lost: int
    return_reason: str
    returned_at: datetime
    returned_by_sub: str | None
    notes: str


class DrugReconciliationOut(BaseModel):
    deployment_id: str
    totals: dict[str, int]
    by_lot: dict[str, dict[str, int]]


class SubjectComplianceOut(BaseModel):
    """Per-subject drug-accountability compliance (used / dispensed)."""

    deployment_id: str
    # subject_id -> {subject_code, dispensed, used, returned, lost, compliance}
    by_subject: dict[str, dict[str, object]]


class MultiSiteRollupOut(BaseModel):
    """Per-site rollup within one deployment (P2 #5)."""

    deployment_id: str
    deployment_name: str
    status: str
    n_sites: int
    n_subjects: int
    site_totals: dict[str, object]
    sites: list[dict[str, object]]


# ── Lab-data feeds DTOs (P2 #6) ──────────────────────────────────────────


class LabBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deployment_id: str
    source_format: str
    content_hash: str
    filename: str | None
    raw_size_bytes: int
    row_count: int
    ingested_by_sub: str | None
    ingested_at: datetime
    notes: str


class LabIngestResultOut(BaseModel):
    batch: LabBatchOut
    was_new: bool
    warnings: list[str] = Field(default_factory=list)


class LabResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    batch_id: str
    deployment_id: str
    subject_id: str | None
    subject_code_hint: str | None
    test_code: str
    test_name: str | None
    value_numeric: float | None
    value_text: str | None
    units: str | None
    ref_range_low: float | None
    ref_range_high: float | None
    abnormal_flag: str | None
    specimen_id: str | None
    collected_at: datetime | None
    parsed_at: datetime


class CapaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    deviation_id: str
    action_text: str
    owner_sub: str | None
    due_date: datetime | None
    status: str
    completed_at: datetime | None
    completed_by: str | None
    created_by: str | None
    created_at: datetime


async def _require_deployment_unlocked(s: Any, deployment_id: str) -> None:
    """Refuse the write with 409 if the deployment-wide study lock is active."""
    if await ClinicalRepository(s).is_study_locked(deployment_id):
        raise HTTPException(
            409,
            f"Study deployment {deployment_id!r} is locked (E7). "
            "Unlock before writes / signatures / SDV.",
        )


async def _require_deployment_unlocked_for_form(s: Any, form_instance_id: str) -> None:
    dep_id = await ClinicalRepository(s).deployment_id_for_form_instance(form_instance_id)
    if dep_id is None:
        return
    await _require_deployment_unlocked(s, dep_id)


async def _require_deployment_unlocked_for_subject(s: Any, subject_id: str) -> None:
    dep_id = await ClinicalRepository(s).deployment_id_for_subject(subject_id)
    if dep_id is None:
        return
    await _require_deployment_unlocked(s, dep_id)


async def _require_deployment_unlocked_for_ae(s: Any, ae_id: str) -> None:
    dep_id = await ClinicalRepository(s).deployment_id_for_adverse_event(ae_id)
    if dep_id is None:
        return
    await _require_deployment_unlocked(s, dep_id)


async def _require_deployment_unlocked_for_deviation(s: Any, deviation_id: str) -> None:
    dep_id = await ClinicalRepository(s).deployment_id_for_deviation(deviation_id)
    if dep_id is None:
        return
    await _require_deployment_unlocked(s, dep_id)


async def _require_deployment_unlocked_for_capa(s: Any, capa_id: str) -> None:
    dep_id = await ClinicalRepository(s).deployment_id_for_capa(capa_id)
    if dep_id is None:
        return
    await _require_deployment_unlocked(s, dep_id)


async def _require_deployment_unlocked_for_query(s: Any, query_id: str) -> None:
    dep_id = await ClinicalRepository(s).deployment_id_for_query(query_id)
    if dep_id is None:
        return
    await _require_deployment_unlocked(s, dep_id)


async def _require_signing_reauth(user_sub: str, password: str) -> None:
    """Enforce Part 11 §11.200 two-component re-auth at signing time.

    Bypassed entirely when auth isn't configured (dev / pytest), so the
    capture flow keeps working offline. Raises ``HTTPException(401)``
    when Cognito refuses the password and ``HTTPException(503)`` when
    Cognito itself fails (caller should retry, not infer auth failure).
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return
    if not password:
        raise HTTPException(401, "Password re-authentication required to sign (Part 11 §11.200).")
    try:
        ok = await verify_user_password_async(
            user_sub,
            password,
            region=settings.cognito_region,
            user_pool_id=settings.cognito_user_pool_id,
            client_id=settings.cognito_client_id,
            client_secret=settings.cognito_client_secret,
        )
    except CognitoAdminError as e:
        logger.warning("Signing re-auth: Cognito call failed: %s", e)
        raise HTTPException(503, "Identity provider unavailable; retry signing.") from e
    if not ok:
        raise HTTPException(401, "Password re-authentication failed.")


def create_edc_router() -> APIRouter:
    router = APIRouter(prefix="/edc", tags=["edc"])

    # ── deployments ──────────────────────────────────────────────────────

    @router.post("/deployments", response_model=DeploymentOut, status_code=201)
    async def deploy(
        body: DeploymentIn,
        user: SessionPayload = require_permission_scoped(Permission.DEPLOYMENT_MANAGE),
    ) -> DeploymentOut:
        # Read the study's PUBLISHED forms from the research DB, then snapshot
        # them into the clinical store so it is self-contained.
        async with get_db_session() as rsession:
            repo = EcrfRepository(rsession)
            study = await repo.get_study(body.research_study_id)
            if study is None:
                raise HTTPException(404, "Research study not found")
            published = [
                f for f in await repo.list_forms(body.research_study_id) if f.status == "published"
            ]
            if not published:
                raise HTTPException(409, "Study has no published forms to deploy")
            snapshots = [
                FormSnapshot(
                    form_def_id=f.id,
                    form_name=f.name,
                    version=f.version,
                    title=f.title,
                    definition_json=f.definition_json,
                )
                for f in published
            ]
            study_name = study.name

        async with get_clinical_session() as csession:
            deployment = await ClinicalRepository(csession).deploy_study(
                research_study_id=body.research_study_id,
                name=body.name or study_name,
                forms=snapshots,
                actor_sub=user.sub,
            )
            deployment_id = deployment.id
            view = DeploymentOut.model_validate(deployment)
        # Sprint A2 auto-status: deployment create flips the trial to
        # 'deployed'. No-op for legacy studies without a trial_id.
        from ..services.trial_status import promote_to_deployed_for_deployment

        await promote_to_deployed_for_deployment(deployment_id)
        return view

    @router.get("/deployments", response_model=list[DeploymentOut])
    async def list_deployments(user: CurrentUser) -> list[DeploymentOut]:
        async with get_clinical_session() as s:
            return [
                DeploymentOut.model_validate(d)
                for d in await ClinicalRepository(s).list_deployments()
            ]

    @router.get("/deployments/{deployment_id}/forms", response_model=list[DeployedFormOut])
    async def list_deployed_forms(deployment_id: str, user: CurrentUser) -> list[DeployedFormOut]:
        async with get_clinical_session() as s:
            forms = await ClinicalRepository(s).list_deployed_forms(deployment_id)
            return [DeployedFormOut.model_validate(f) for f in forms]

    @router.get("/deployed-forms/{deployed_form_id}", response_model=DeployedFormDetailOut)
    async def get_deployed_form(deployed_form_id: str, user: CurrentUser) -> DeployedFormDetailOut:
        """The deployed form's full definition — used by the collector to render it."""
        async with get_clinical_session() as s:
            df = await ClinicalRepository(s).get_deployed_form(deployed_form_id)
            if df is None:
                raise HTTPException(404, "Deployed form not found")
            return DeployedFormDetailOut(
                **DeployedFormOut.model_validate(df).model_dump(),
                definition=FormDefinition.model_validate(json.loads(df.definition_json)),
            )

    # ── sites ────────────────────────────────────────────────────────────

    @router.post("/deployments/{deployment_id}/sites", response_model=SiteOut, status_code=201)
    async def add_site(
        deployment_id: str,
        body: SiteIn,
        user: SessionPayload = require_permission_scoped(
            Permission.DEPLOYMENT_MANAGE, resource_param="deployment_id"
        ),
    ) -> SiteOut:
        async with get_clinical_session() as s:
            try:
                site = await ClinicalRepository(s).add_site(
                    deployment_id, name=body.name, code=body.code, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return SiteOut.model_validate(site)

    @router.get("/deployments/{deployment_id}/sites", response_model=list[SiteOut])
    async def list_sites(deployment_id: str, user: CurrentUser) -> list[SiteOut]:
        async with get_clinical_session() as s:
            return [
                SiteOut.model_validate(x)
                for x in await ClinicalRepository(s).list_sites(deployment_id)
            ]

    # ── subjects ─────────────────────────────────────────────────────────

    @router.post(
        "/deployments/{deployment_id}/subjects", response_model=SubjectOut, status_code=201
    )
    async def add_subject(
        deployment_id: str,
        body: SubjectIn,
        user: SessionPayload = require_permission_scoped(
            Permission.DEPLOYMENT_MANAGE, resource_param="deployment_id"
        ),
    ) -> SubjectOut:
        async with get_clinical_session() as s:
            try:
                subject = await ClinicalRepository(s).add_subject(
                    deployment_id,
                    site_id=body.site_id,
                    subject_code=body.subject_code,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(400, str(e)) from e
            return SubjectOut.model_validate(subject)

    @router.get("/deployments/{deployment_id}/subjects", response_model=list[SubjectOut])
    async def list_subjects(deployment_id: str, user: CurrentUser) -> list[SubjectOut]:
        async with get_clinical_session() as s:
            subs = await ClinicalRepository(s).list_subjects(deployment_id)
            return [SubjectOut.model_validate(x) for x in subs]

    @router.post(
        "/subjects/{subject_id}/epro-access", response_model=EproAccessOut, status_code=201
    )
    async def issue_epro_access(
        subject_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.DATA_ENTER, resource_param="subject_id"
        ),
    ) -> EproAccessOut:
        """Issue a participant ePRO magic-link token for a subject (raw token shown once)."""
        async with get_clinical_session() as s:
            try:
                _access, raw = await ClinicalRepository(s).issue_participant_access(
                    subject_id, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return EproAccessOut(
                token=raw, subject_id=subject_id, epro_path=f"/epro.html?token={raw}"
            )

    # ── form instances + data ────────────────────────────────────────────

    @router.get("/subjects/{subject_id}/forms", response_model=list[FormInstanceOut])
    async def list_subject_forms(subject_id: str, user: CurrentUser) -> list[FormInstanceOut]:
        async with get_clinical_session() as s:
            instances = await ClinicalRepository(s).list_form_instances(subject_id)
            return [FormInstanceOut.model_validate(fi) for fi in instances]

    @router.post("/subjects/{subject_id}/forms", response_model=FormInstanceOut, status_code=201)
    async def open_form(
        subject_id: str,
        body: OpenFormIn,
        user: SessionPayload = require_permission_scoped(
            Permission.DATA_ENTER, resource_param="subject_id"
        ),
    ) -> FormInstanceOut:
        async with get_clinical_session() as s:
            try:
                fi = await ClinicalRepository(s).open_form_instance(
                    subject_id,
                    deployed_form_id=body.deployed_form_id,
                    event_instance_id=body.event_instance_id,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(400, str(e)) from e
            return FormInstanceOut.model_validate(fi)

    @router.put("/form-instances/{form_instance_id}/data", response_model=FormInstanceOut)
    async def submit_data(
        form_instance_id: str,
        body: SubmitDataIn,
        user: SessionPayload = require_permission_scoped(
            Permission.DATA_ENTER, resource_param="form_instance_id"
        ),
    ) -> FormInstanceOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_form(s, form_instance_id)
            try:
                fi = await ClinicalRepository(s).submit_item_data(
                    form_instance_id,
                    body.values,
                    actor_sub=user.sub,
                    reason=body.reason,
                    mark_complete=body.mark_complete,
                )
            except HardCheckError as e:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Hard edit-check(s) failed — nothing was saved.",
                        "failures": [
                            {"item_id": f.item_id, "check_id": f.check_id, "message": f.message}
                            for f in e.failures
                        ],
                    },
                ) from e
            except LockedError as e:
                raise HTTPException(409, str(e)) from e
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return FormInstanceOut.model_validate(fi)

    @router.get("/form-instances/{form_instance_id}", response_model=FormInstanceDetailOut)
    async def get_form_instance(form_instance_id: str, user: CurrentUser) -> FormInstanceDetailOut:
        async with get_clinical_session() as s:
            found = await ClinicalRepository(s).get_form_instance(form_instance_id)
            if found is None:
                raise HTTPException(404, "Form instance not found")
            fi, items = found
            return FormInstanceDetailOut(
                **FormInstanceOut.model_validate(fi).model_dump(),
                items=[ItemDataOut.model_validate(i) for i in items],
            )

    @router.get("/form-instances/{form_instance_id}/audit", response_model=list[AuditEntryOut])
    async def get_audit(
        form_instance_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.AUDIT_READ, resource_param="form_instance_id"
        ),
    ) -> list[AuditEntryOut]:
        async with get_clinical_session() as s:
            entries = await ClinicalRepository(s).get_form_instance_audit(form_instance_id)
            return [AuditEntryOut.model_validate(e) for e in entries]

    # ── queries / discrepancies ──────────────────────────────────────────

    @router.get("/form-instances/{form_instance_id}/queries", response_model=list[QueryOut])
    async def list_queries(form_instance_id: str, user: CurrentUser) -> list[QueryOut]:
        async with get_clinical_session() as s:
            qs = await ClinicalRepository(s).list_queries(form_instance_id)
            return [QueryOut.model_validate(q) for q in qs]

    @router.post(
        "/form-instances/{form_instance_id}/queries", response_model=QueryOut, status_code=201
    )
    async def raise_query(
        form_instance_id: str,
        body: ManualQueryIn,
        user: SessionPayload = require_permission_scoped(
            Permission.QUERY_RAISE, resource_param="form_instance_id"
        ),
    ) -> QueryOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_form(s, form_instance_id)
            try:
                q = await ClinicalRepository(s).create_manual_query(
                    form_instance_id, item_id=body.item_id, text=body.text, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return QueryOut.model_validate(q)

    @router.post("/queries/{query_id}/respond", response_model=QueryOut)
    async def respond_query(
        query_id: str,
        body: QueryResponseIn,
        user: SessionPayload = require_permission_scoped(
            Permission.QUERY_RESPOND, resource_param="query_id"
        ),
    ) -> QueryOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_query(s, query_id)
            try:
                q = await ClinicalRepository(s).respond_query(
                    query_id, text=body.text, author_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return QueryOut.model_validate(q)

    @router.post("/queries/{query_id}/close", response_model=QueryOut)
    async def close_query(
        query_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.QUERY_CLOSE, resource_param="query_id"
        ),
    ) -> QueryOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_query(s, query_id)
            try:
                q = await ClinicalRepository(s).close_query(query_id, actor_sub=user.sub)
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return QueryOut.model_validate(q)

    # ── e-signatures + lock (E5) ───────────────────────────────────────────

    @router.post("/form-instances/{form_instance_id}/sign", response_model=SignatureOut)
    async def sign(
        form_instance_id: str,
        body: SignIn,
        user: SessionPayload = require_permission_scoped(
            Permission.FORM_SIGN, resource_param="form_instance_id"
        ),
    ) -> SignatureOut:
        await _require_signing_reauth(user.sub, body.password)
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_form(s, form_instance_id)
            try:
                sig = await ClinicalRepository(s).sign_form_instance(
                    form_instance_id, meaning=body.meaning, signer_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return SignatureOut.model_validate(sig)

    @router.post("/form-instances/{form_instance_id}/unlock", response_model=FormInstanceOut)
    async def unlock(
        form_instance_id: str,
        body: UnlockIn,
        user: SessionPayload = require_permission_scoped(
            Permission.FORM_UNLOCK, resource_param="form_instance_id"
        ),
    ) -> FormInstanceOut:
        # Unlocking voids a signature — data-manager territory under
        # RBAC-2's split. Previously admin-only; now the data_manager role
        # carries `form.unlock` at study scope.
        async with get_clinical_session() as s:
            try:
                fi = await ClinicalRepository(s).unlock_form_instance(
                    form_instance_id, reason=body.reason, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return FormInstanceOut.model_validate(fi)

    @router.get("/form-instances/{form_instance_id}/signatures", response_model=list[SignatureOut])
    async def list_signatures(form_instance_id: str, user: CurrentUser) -> list[SignatureOut]:
        async with get_clinical_session() as s:
            sigs = await ClinicalRepository(s).list_signatures(form_instance_id)
            return [SignatureOut.model_validate(x) for x in sigs]

    # ── source-data verification (E6) ───────────────────────────────────────

    @router.post("/form-instances/{form_instance_id}/verify")
    async def verify(
        form_instance_id: str,
        body: VerifyIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SDV_VERIFY, resource_param="form_instance_id"
        ),
    ) -> dict[str, int]:
        # SDV is monitor (CRA) territory under RBAC-2. Previously open to
        # any data-entry user, which violated separation-of-duties.
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_form(s, form_instance_id)
            try:
                n = await ClinicalRepository(s).verify_items(
                    form_instance_id, body.item_ids, verifier_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return {"verified": n}

    @router.get(
        "/form-instances/{form_instance_id}/verifications", response_model=list[VerificationOut]
    )
    async def list_verifications(form_instance_id: str, user: CurrentUser) -> list[VerificationOut]:
        async with get_clinical_session() as s:
            vs = await ClinicalRepository(s).list_verifications(form_instance_id)
            return [VerificationOut.model_validate(v) for v in vs]

    # ── subject-casebook sign-off + lock (E6) ───────────────────────────────

    @router.post("/subjects/{subject_id}/sign", response_model=SubjectSignatureOut)
    async def sign_subject(
        subject_id: str,
        body: SubjectSignIn,
        user: SessionPayload = require_permission_scoped(
            Permission.CASEBOOK_SIGNOFF, resource_param="subject_id"
        ),
    ) -> SubjectSignatureOut:
        await _require_signing_reauth(user.sub, body.password)
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_subject(s, subject_id)
            try:
                sig = await ClinicalRepository(s).sign_subject(
                    subject_id, meaning=body.meaning, signer_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return SubjectSignatureOut.model_validate(sig)

    @router.post("/subjects/{subject_id}/unlock", response_model=SubjectOut)
    async def unlock_subject(
        subject_id: str,
        body: UnlockIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SUBJECT_UNLOCK, resource_param="subject_id"
        ),
    ) -> SubjectOut:
        async with get_clinical_session() as s:
            try:
                subject = await ClinicalRepository(s).unlock_subject(
                    subject_id, reason=body.reason, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return SubjectOut.model_validate(subject)

    @router.get("/subjects/{subject_id}/signatures", response_model=list[SubjectSignatureOut])
    async def list_subject_signatures(
        subject_id: str, user: CurrentUser
    ) -> list[SubjectSignatureOut]:
        async with get_clinical_session() as s:
            sigs = await ClinicalRepository(s).list_subject_signatures(subject_id)
            return [SubjectSignatureOut.model_validate(x) for x in sigs]

    # ── Safety subsystem: adverse events (top-6 #4) ──────────────────────

    @router.post(
        "/subjects/{subject_id}/adverse-events",
        response_model=AdverseEventOut,
        status_code=201,
    )
    async def record_adverse_event(
        subject_id: str,
        body: AdverseEventIn,
        user: SessionPayload = require_permission_scoped(
            Permission.AE_RECORD, resource_param="subject_id"
        ),
    ) -> AdverseEventOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_subject(s, subject_id)
            try:
                ae = await ClinicalRepository(s).record_adverse_event(
                    subject_id,
                    term_text=body.term_text,
                    severity_grade=body.severity_grade,
                    outcome=body.outcome,
                    relationship_to_intervention=body.relationship_to_intervention,
                    expectedness=body.expectedness,
                    start_date=body.start_date,
                    end_date=body.end_date,
                    hospitalisation_flag=body.hospitalisation_flag,
                    life_threatening_flag=body.life_threatening_flag,
                    persistent_disability_flag=body.persistent_disability_flag,
                    congenital_anomaly_flag=body.congenital_anomaly_flag,
                    other_medically_significant_flag=body.other_medically_significant_flag,
                    narrative=body.narrative,
                    form_instance_id=body.form_instance_id,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return AdverseEventOut.from_orm_ae(ae)

    @router.get(
        "/subjects/{subject_id}/adverse-events",
        response_model=list[AdverseEventOut],
    )
    async def list_subject_adverse_events(
        subject_id: str, user: CurrentUser
    ) -> list[AdverseEventOut]:
        async with get_clinical_session() as s:
            aes = await ClinicalRepository(s).list_adverse_events(subject_id=subject_id)
            return [AdverseEventOut.from_orm_ae(a) for a in aes]

    @router.get("/ae/{ae_id}", response_model=AdverseEventOut)
    async def get_adverse_event(ae_id: str, user: CurrentUser) -> AdverseEventOut:
        async with get_clinical_session() as s:
            ae = await ClinicalRepository(s).get_adverse_event(ae_id)
            if ae is None:
                raise HTTPException(404, "Adverse event not found")
            return AdverseEventOut.from_orm_ae(ae)

    @router.patch("/ae/{ae_id}", response_model=AdverseEventOut)
    async def classify_adverse_event(
        ae_id: str,
        body: AdverseEventClassifyIn,
        user: SessionPayload = require_permission_scoped(
            Permission.AE_CLASSIFY, resource_param="ae_id"
        ),
    ) -> AdverseEventOut:
        # SeriousReason is a Literal — runtime acceptance is permissive
        # (the rbac module only inspects the strings) but the repo
        # accepts list[str] for the reasons override.
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_ae(s, ae_id)
            try:
                ae = await ClinicalRepository(s).reclassify_adverse_event(
                    ae_id,
                    is_serious=body.is_serious,
                    serious_reasons=body.serious_reasons,  # type: ignore[arg-type]
                    outcome=body.outcome,
                    severity_grade=body.severity_grade,
                    expectedness=body.expectedness,
                    narrative=body.narrative,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return AdverseEventOut.from_orm_ae(ae)

    @router.patch("/ae/{ae_id}/meddra-pt", response_model=AdverseEventOut)
    async def code_adverse_event_meddra(
        ae_id: str,
        body: AdverseEventMeddraIn,
        user: SessionPayload = require_permission_scoped(
            Permission.AE_CLASSIFY, resource_param="ae_id"
        ),
    ) -> AdverseEventOut:
        """Manually code a MedDRA Preferred Term.

        For production deployments needing real MedDRA validation,
        wire a licensed MedDRA dictionary into the validator at this
        endpoint. The roadmap and rbac-design docs both flag the
        license as a deploy-time concern.
        """
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_ae(s, ae_id)
            try:
                ae = await ClinicalRepository(s).reclassify_adverse_event(
                    ae_id, meddra_pt=body.meddra_pt, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return AdverseEventOut.from_orm_ae(ae)

    @router.get(
        "/deployments/{deployment_id}/sae/overdue",
        response_model=list[AdverseEventOut],
    )
    async def list_overdue_saes(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SAE_REPORT, resource_param="deployment_id"
        ),
    ) -> list[AdverseEventOut]:
        async with get_clinical_session() as s:
            aes = await ClinicalRepository(s).list_overdue_serious_aes(deployment_id)
            return [AdverseEventOut.from_orm_ae(a) for a in aes]

    @router.get(
        "/deployments/{deployment_id}/sae/susars",
        response_model=list[AdverseEventOut],
    )
    async def list_susars(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SAE_REPORT, resource_param="deployment_id"
        ),
    ) -> list[AdverseEventOut]:
        """Suspected Unexpected Serious Adverse Reactions — the subset on
        the tightest (7 / 15-day) expedited-reporting clock. Screening
        signal for the safety physician, not a regulatory determination."""
        async with get_clinical_session() as s:
            aes = await ClinicalRepository(s).list_susars(deployment_id)
            return [AdverseEventOut.from_orm_ae(a) for a in aes]

    @router.post("/ae/{ae_id}/mark-reported", response_model=AdverseEventOut)
    async def mark_ae_reported(
        ae_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SAE_REPORT, resource_param="ae_id"
        ),
    ) -> AdverseEventOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_ae(s, ae_id)
            try:
                ae = await ClinicalRepository(s).mark_ae_reported_to_authority(
                    ae_id, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return AdverseEventOut.from_orm_ae(ae)

    @router.get("/ae/{ae_id}/report/fda-3500a/{fmt}")
    async def download_fda_3500a(
        ae_id: str,
        fmt: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SAE_REPORT, resource_param="ae_id"
        ),
    ) -> Response:
        """Generate an FDA 3500A IND safety report draft for the AE.

        Sponsor regulatory affairs reviews + submits via the FDA gateway
        or paper — this endpoint produces the draft only. PHI minimised:
        subject_code is the only patient identifier emitted.
        """
        if fmt not in ("pdf", "docx"):
            raise HTTPException(400, "fmt must be 'pdf' or 'docx'")

        async with get_clinical_session() as s:
            ae = await ClinicalRepository(s).get_adverse_event(ae_id)
            if ae is None:
                raise HTTPException(404, "Adverse event not found")
            subject = await s.get(Subject, ae.subject_id)
            from ..persistence.clinical.models import StudyDeployment

            deployment = await s.get(StudyDeployment, ae.deployment_id)
            research_study_id = deployment.research_study_id if deployment else None

        # Cross-DB: pull the human-readable study name from the research DB
        # so the product field is populated.
        research_study_name: str | None = None
        if research_study_id:
            async with get_db_session() as rs:
                study = await EcrfRepository(rs).get_study(research_study_id)
                if study is not None:
                    research_study_name = study.name

        from ..reports import sae_3500a as _3500a

        data = _3500a.assemble_3500a_data(
            ae=ae,
            subject=subject,
            deployment=deployment,
            research_study_name=research_study_name,
        )
        builder = _3500a.build_pdf if fmt == "pdf" else _3500a.build_docx
        payload = builder(data, Path(get_settings().images_dir))
        media = (
            "application/pdf"
            if fmt == "pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        filename = f"fda-3500a-{ae_id[:8]}.{fmt}"
        return Response(
            content=payload,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    # ── Safety subsystem: protocol deviations + CAPA ─────────────────────

    @router.post(
        "/subjects/{subject_id}/deviations",
        response_model=DeviationOut,
        status_code=201,
    )
    async def record_subject_deviation(
        subject_id: str,
        body: DeviationIn,
        user: SessionPayload = require_permission_scoped(
            Permission.DEVIATION_RECORD, resource_param="subject_id"
        ),
    ) -> DeviationOut:
        async with get_clinical_session() as s:
            subject = await s.get(Subject, subject_id)
            if subject is None:
                raise HTTPException(404, "Subject not found")
            await _require_deployment_unlocked(s, subject.deployment_id)
            try:
                dev = await ClinicalRepository(s).record_deviation(
                    deployment_id=subject.deployment_id,
                    subject_id=subject_id,
                    classification=body.classification,
                    category=body.category,
                    description=body.description,
                    root_cause=body.root_cause,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return DeviationOut.model_validate(dev)

    @router.post(
        "/deployments/{deployment_id}/deviations",
        response_model=DeviationOut,
        status_code=201,
    )
    async def record_deployment_deviation(
        deployment_id: str,
        body: DeviationIn,
        user: SessionPayload = require_permission_scoped(
            Permission.DEVIATION_RECORD, resource_param="deployment_id"
        ),
    ) -> DeviationOut:
        """Deployment-wide deviation (no specific subject) — e.g. a
        central drug-supply temperature excursion."""
        async with get_clinical_session() as s:
            await _require_deployment_unlocked(s, deployment_id)
            try:
                dev = await ClinicalRepository(s).record_deviation(
                    deployment_id=deployment_id,
                    subject_id=body.subject_id,
                    classification=body.classification,
                    category=body.category,
                    description=body.description,
                    root_cause=body.root_cause,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return DeviationOut.model_validate(dev)

    @router.get(
        "/deployments/{deployment_id}/deviations",
        response_model=list[DeviationOut],
    )
    async def list_deployment_deviations(
        deployment_id: str,
        user: CurrentUser,
        status: str | None = None,
    ) -> list[DeviationOut]:
        async with get_clinical_session() as s:
            devs = await ClinicalRepository(s).list_deviations(
                deployment_id=deployment_id, status=status
            )
            return [DeviationOut.model_validate(d) for d in devs]

    @router.get(
        "/subjects/{subject_id}/deviations",
        response_model=list[DeviationOut],
    )
    async def list_subject_deviations(subject_id: str, user: CurrentUser) -> list[DeviationOut]:
        async with get_clinical_session() as s:
            devs = await ClinicalRepository(s).list_deviations(subject_id=subject_id)
            return [DeviationOut.model_validate(d) for d in devs]

    @router.patch("/deviations/{deviation_id}", response_model=DeviationOut)
    async def classify_deviation(
        deviation_id: str,
        body: DeviationClassifyIn,
        user: SessionPayload = require_permission_scoped(
            Permission.DEVIATION_CLASSIFY, resource_param="deviation_id"
        ),
    ) -> DeviationOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_deviation(s, deviation_id)
            try:
                dev = await ClinicalRepository(s).reclassify_deviation(
                    deviation_id,
                    classification=body.classification,
                    category=body.category,
                    root_cause=body.root_cause,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return DeviationOut.model_validate(dev)

    @router.post(
        "/deviations/{deviation_id}/capa",
        response_model=CapaOut,
        status_code=201,
    )
    async def add_capa(
        deviation_id: str,
        body: CapaIn,
        user: SessionPayload = require_permission_scoped(
            Permission.CAPA_AUTHOR, resource_param="deviation_id"
        ),
    ) -> CapaOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_deviation(s, deviation_id)
            try:
                capa = await ClinicalRepository(s).add_capa(
                    deviation_id,
                    action_text=body.action_text,
                    owner_sub=body.owner_sub,
                    due_date=body.due_date,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return CapaOut.model_validate(capa)

    @router.get("/deviations/{deviation_id}/capa", response_model=list[CapaOut])
    async def list_capas(deviation_id: str, user: CurrentUser) -> list[CapaOut]:
        async with get_clinical_session() as s:
            capas = await ClinicalRepository(s).list_capas(deviation_id)
            return [CapaOut.model_validate(c) for c in capas]

    @router.post("/capa/{capa_id}/complete", response_model=CapaOut)
    async def complete_capa(
        capa_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CAPA_AUTHOR, resource_param="capa_id"
        ),
    ) -> CapaOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_capa(s, capa_id)
            try:
                capa = await ClinicalRepository(s).complete_capa(capa_id, actor_sub=user.sub)
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return CapaOut.model_validate(capa)

    @router.post("/deviations/{deviation_id}/close", response_model=DeviationOut)
    async def close_deviation(
        deviation_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CAPA_CLOSE, resource_param="deviation_id"
        ),
    ) -> DeviationOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked_for_deviation(s, deviation_id)
            try:
                dev = await ClinicalRepository(s).close_deviation(deviation_id, actor_sub=user.sub)
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return DeviationOut.model_validate(dev)

    # ── CDISC submission pipeline (top-6 #6) ─────────────────────────────

    @router.post("/deployments/{deployment_id}/cdisc/derive")
    async def cdisc_derive(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CDISC_DERIVE, resource_param="deployment_id"
        ),
    ) -> dict[str, Any]:
        from ..cdisc.pipeline import run_derivation

        async with get_clinical_session() as s:
            try:
                result = await run_derivation(s, deployment_id=deployment_id, triggered_by=user.sub)
            except ValueError as e:
                raise HTTPException(404, str(e)) from e
            return {
                "deployment_id": result.deployment_id,
                "triggered_at": result.triggered_at.isoformat(),
                "counts": result.counts,
            }

    @router.post("/deployments/{deployment_id}/cdisc/survival/render")
    async def cdisc_render_survival(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CDISC_DERIVE, resource_param="deployment_id"
        ),
    ) -> dict[str, Any]:
        """Run the K-M / Cox PH analysis script in the sandbox + persist
        the resulting figures + Cox summary as TlfArtefact rows.

        First CDISC output to round-trip the sandbox: pulls the current
        ADTTE + ADSL rows, serialises them as JSON, hands them to
        `_impl` along with the script text. Output PNGs are base64-
        encoded into the TlfArtefact.svg_content field as data URIs so
        the existing TLF preview UI renders them via an <img> tag
        (which we already detect on the data-URI prefix).

        Refuses 409 if no derivation has been run yet (no ADTTE rows
        to analyse). Refuses 503 if the sandbox isn't available — the
        sandbox is the regulator-audited compute boundary, so we
        don't fall back to in-process Cox / K-M.
        """
        import base64
        import json as _json

        from ..cdisc.pipeline import (
            fetch_adsl as _fetch_adsl,
        )
        from ..cdisc.pipeline import (
            fetch_adtte as _fetch_adtte,
        )
        from ..persistence.clinical.models import TlfArtefact as _TlfArtefact
        from ..tools.data_science.sandbox_exec import _impl as _sandbox_impl

        settings = get_settings()
        if not getattr(settings, "sandbox_enabled", False):
            raise HTTPException(
                503,
                "Sandbox execution is disabled. Survival analyses require "
                "the Docker sandbox — enable SANDBOX_ENABLED in the deploy.",
            )

        # Load script text from the bundled package data so the deploy
        # ships one consistent script across all callers.
        from importlib.resources import files as _files

        script_text = (
            _files("research_assistant.cdisc.sandbox_scripts")
            .joinpath("survival_analysis.py")
            .read_text(encoding="utf-8")
        )

        async with get_clinical_session() as s:
            adsl = await _fetch_adsl(s, deployment_id)
            adtte = await _fetch_adtte(s, deployment_id)
            if not adtte:
                raise HTTPException(
                    409,
                    "No ADTTE rows for this deployment — run /cdisc/derive first.",
                )
            payload = {
                "adtte": [
                    {
                        "USUBJID": r.USUBJID,
                        "PARAMCD": r.PARAMCD,
                        "PARAM": r.PARAM,
                        "AVAL": r.AVAL,
                        "CNSR": r.CNSR,
                        "TRT01A": r.TRT01A,
                    }
                    for r in adtte
                ],
                "adsl": [{"USUBJID": r.USUBJID, "TRT01A": r.TRT01A} for r in adsl],
            }

        result = await _sandbox_impl(
            script_text, input_data=_json.dumps(payload), input_format="json"
        )
        if result.error:
            raise HTTPException(500, f"Sandbox failed: {result.error}")

        # Pull the PNGs back from images_dir; `_impl` returned URL paths
        # of the form "/static/sandbox-images/{run_prefix}_km-{...}.png"
        # — fetch them off disk so we can embed them as data URIs.
        from pathlib import Path as _Path

        images_dir = _Path(settings.images_dir)
        new_tlfs: list[_TlfArtefact] = []
        for filename, url_or_text in result.files.items():
            if filename.startswith("km-") and filename.endswith(".png"):
                stored_name = url_or_text.rsplit("/", 1)[-1]
                png_path = images_dir / stored_name
                if not png_path.exists():
                    logger.warning("Survival PNG not found at %s", png_path)
                    continue
                b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
                tlf_id = f"f-{filename.removesuffix('.png')}"
                paramcd = filename.removeprefix("km-").removesuffix(".png").upper()
                new_tlfs.append(
                    _TlfArtefact(
                        deployment_id=deployment_id,
                        kind="figure",
                        tlf_id=tlf_id,
                        title=f"Figure — Kaplan-Meier for {paramcd}",
                        svg_content=f"data:image/png;base64,{b64}",
                    )
                )
            elif filename == "cox-summary.json" and isinstance(url_or_text, str):
                try:
                    cox = _json.loads(url_or_text)
                except _json.JSONDecodeError:
                    cox = {"params": []}
                # Build a flat per-comparison table.
                rows: list[list[Any]] = []
                for param in cox.get("params", []):
                    if not param.get("fitted"):
                        rows.append(
                            [
                                param.get("paramcd", "?"),
                                param.get("skip_reason", "not fitted"),
                                "—",
                                "—",
                                "—",
                                "—",
                            ]
                        )
                        continue
                    for crow in param.get("rows", []):
                        rows.append(
                            [
                                param.get("paramcd", "?"),
                                crow.get("comparison", "?"),
                                f"{crow['hr']:.3f}",
                                f"{crow['hr_95ci_lo']:.3f}",
                                f"{crow['hr_95ci_hi']:.3f}",
                                f"{crow['p_value']:.4f}",
                            ]
                        )
                if not rows:
                    rows.append(["(no Cox fits)", "—", "—", "—", "—", "—"])
                new_tlfs.append(
                    _TlfArtefact(
                        deployment_id=deployment_id,
                        kind="table",
                        tlf_id="t-cox-ph-summary",
                        title="Table 5 — Cox PH Summary (TRT01A reference)",
                        content_json=json.dumps(
                            {
                                "columns": [
                                    "PARAMCD",
                                    "Comparison",
                                    "HR",
                                    "HR 95% CI low",
                                    "HR 95% CI high",
                                    "p-value",
                                ],
                                "rows": rows,
                            }
                        ),
                    )
                )

        # Persist — replace any prior survival TLF rows for this deployment
        # so a re-run is idempotent.
        async with get_clinical_session() as s:
            from sqlalchemy import delete as _delete

            await s.execute(
                _delete(_TlfArtefact)
                .where(_TlfArtefact.deployment_id == deployment_id)
                .where(
                    (_TlfArtefact.tlf_id == "t-cox-ph-summary") | _TlfArtefact.tlf_id.like("f-km-%")
                )
            )
            for tlf in new_tlfs:
                s.add(tlf)
            await s.flush()

        return {
            "deployment_id": deployment_id,
            "tlfs_added": len(new_tlfs),
            "stdout": result.stdout[:2000],
        }

    @router.get("/deployments/{deployment_id}/cdisc/datasets")
    async def cdisc_list_datasets(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CDISC_READ, resource_param="deployment_id"
        ),
    ) -> dict[str, Any]:
        from ..cdisc.pipeline import list_datasets

        async with get_clinical_session() as s:
            return await list_datasets(s, deployment_id)

    @router.get("/deployments/{deployment_id}/cdisc/datasets/{domain}")
    async def cdisc_get_dataset(
        deployment_id: str,
        domain: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CDISC_READ, resource_param="deployment_id"
        ),
    ) -> dict[str, Any]:
        from ..cdisc.pipeline import (
            fetch_adsl,
            fetch_adtte,
            fetch_ae,
            fetch_cm,
            fetch_dm,
            fetch_ex,
            fetch_lb,
            fetch_mh,
            fetch_vs,
        )

        # Use json round-trips so the response shape is uniform across
        # the SDTM/ADaM dataset variants without per-domain DTOs.
        rows: list[Any]
        async with get_clinical_session() as s:
            domain_upper = domain.upper()
            if domain_upper == "DM":
                rows = list(await fetch_dm(s, deployment_id))
            elif domain_upper == "AE":
                rows = list(await fetch_ae(s, deployment_id))
            elif domain_upper == "VS":
                rows = list(await fetch_vs(s, deployment_id))
            elif domain_upper == "LB":
                rows = list(await fetch_lb(s, deployment_id))
            elif domain_upper == "EX":
                rows = list(await fetch_ex(s, deployment_id))
            elif domain_upper == "CM":
                rows = list(await fetch_cm(s, deployment_id))
            elif domain_upper == "MH":
                rows = list(await fetch_mh(s, deployment_id))
            elif domain_upper == "ADSL":
                rows = list(await fetch_adsl(s, deployment_id))
            elif domain_upper == "ADTTE":
                rows = list(await fetch_adtte(s, deployment_id))
            else:
                raise HTTPException(
                    400,
                    f"Unknown domain {domain!r}. Choose one of: "
                    "DM, AE, VS, LB, EX, CM, MH, ADSL, ADTTE.",
                )
        return {
            "domain": domain_upper,
            "n": len(rows),
            "records": [
                {
                    c: getattr(r, c)
                    for c in r.__table__.columns
                    if c not in {"id", "deployment_id", "derived_at"}
                }
                for r in rows
            ],
        }

    @router.get("/deployments/{deployment_id}/cdisc/datasets/{domain}/export.csv")
    async def cdisc_export_dataset(
        deployment_id: str,
        domain: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CDISC_READ, resource_param="deployment_id"
        ),
    ) -> Response:
        from ..cdisc.exporter import (
            adsl_to_csv,
            adtte_to_csv,
            ae_to_csv,
            cm_to_csv,
            dm_to_csv,
            ex_to_csv,
            lb_to_csv,
            mh_to_csv,
            vs_to_csv,
        )
        from ..cdisc.pipeline import (
            fetch_adsl,
            fetch_adtte,
            fetch_ae,
            fetch_cm,
            fetch_dm,
            fetch_ex,
            fetch_lb,
            fetch_mh,
            fetch_vs,
        )

        async with get_clinical_session() as s:
            domain_upper = domain.upper()
            if domain_upper == "DM":
                payload = dm_to_csv(await fetch_dm(s, deployment_id))
            elif domain_upper == "AE":
                payload = ae_to_csv(await fetch_ae(s, deployment_id))
            elif domain_upper == "VS":
                payload = vs_to_csv(await fetch_vs(s, deployment_id))
            elif domain_upper == "LB":
                payload = lb_to_csv(await fetch_lb(s, deployment_id))
            elif domain_upper == "EX":
                payload = ex_to_csv(await fetch_ex(s, deployment_id))
            elif domain_upper == "CM":
                payload = cm_to_csv(await fetch_cm(s, deployment_id))
            elif domain_upper == "MH":
                payload = mh_to_csv(await fetch_mh(s, deployment_id))
            elif domain_upper == "ADSL":
                payload = adsl_to_csv(await fetch_adsl(s, deployment_id))
            elif domain_upper == "ADTTE":
                payload = adtte_to_csv(await fetch_adtte(s, deployment_id))
            else:
                raise HTTPException(
                    400,
                    f"Unknown domain {domain!r}. Choose one of: "
                    "DM, AE, VS, LB, EX, CM, MH, ADSL, ADTTE.",
                )
        return Response(
            content=payload,
            media_type="text/csv",
            headers={
                "Content-Disposition": (f'attachment; filename="{domain_upper.lower()}.csv"'),
            },
        )

    @router.get("/deployments/{deployment_id}/cdisc/tlf")
    async def cdisc_list_tlfs(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CDISC_READ, resource_param="deployment_id"
        ),
    ) -> list[dict[str, Any]]:
        from ..cdisc.pipeline import fetch_tlfs

        async with get_clinical_session() as s:
            tlfs = await fetch_tlfs(s, deployment_id)
        return [
            {
                "id": t.id,
                "tlf_id": t.tlf_id,
                "kind": t.kind,
                "title": t.title,
                "content": json.loads(t.content_json) if t.content_json else None,
                "svg": t.svg_content,
            }
            for t in tlfs
        ]

    @router.get("/deployments/{deployment_id}/cdisc/submission-bundle.zip")
    async def cdisc_export_bundle(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.CDISC_EXPORT, resource_param="deployment_id"
        ),
    ) -> Response:
        from ..cdisc.exporter import build_submission_bundle
        from ..cdisc.pipeline import (
            fetch_adsl,
            fetch_adtte,
            fetch_ae,
            fetch_cm,
            fetch_dm,
            fetch_ex,
            fetch_lb,
            fetch_mh,
            fetch_tlfs,
            fetch_vs,
            list_datasets,
        )

        async with get_clinical_session() as s:
            summary = await list_datasets(s, deployment_id)
            if not summary.get("last_derived_at"):
                raise HTTPException(
                    409,
                    "No derivation has been run for this deployment. "
                    "POST /api/edc/deployments/{id}/cdisc/derive first.",
                )
            dm = await fetch_dm(s, deployment_id)
            ae = await fetch_ae(s, deployment_id)
            vs = await fetch_vs(s, deployment_id)
            lb = await fetch_lb(s, deployment_id)
            ex = await fetch_ex(s, deployment_id)
            cm = await fetch_cm(s, deployment_id)
            mh = await fetch_mh(s, deployment_id)
            adsl = await fetch_adsl(s, deployment_id)
            adtte = await fetch_adtte(s, deployment_id)
            tlfs = await fetch_tlfs(s, deployment_id)
            # Use the deployment's underlying research study id for the
            # manifest's STUDYID — that's what regulators expect.
            from ..persistence.clinical.models import StudyDeployment

            deployment = await s.get(StudyDeployment, deployment_id)
            study_id = deployment.research_study_id if deployment else deployment_id

        payload = build_submission_bundle(
            study_id=study_id,
            triggered_at=summary["last_derived_at"],
            dm=dm,
            ae=ae,
            vs=vs,
            lb=lb,
            ex=ex,
            cm=cm,
            mh=mh,
            adsl=adsl,
            adtte=adtte,
            tlfs=tlfs,
        )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="submission-{deployment_id[:8]}.zip"'
                ),
            },
        )

    # ── Study-level lock (E7 — validation pack) ───────────────────────────

    @router.get(
        "/deployments/{deployment_id}/lock-status",
        response_model=StudyLockStatusOut,
    )
    async def lock_status(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_READ, resource_param="deployment_id"
        ),
    ) -> StudyLockStatusOut:
        async with get_clinical_session() as s:
            active = await ClinicalRepository(s).get_active_study_lock(deployment_id)
            return StudyLockStatusOut(
                deployment_id=deployment_id,
                locked=active is not None,
                active_lock=StudyLockOut.model_validate(active) if active is not None else None,
            )

    @router.post(
        "/deployments/{deployment_id}/lock",
        response_model=StudyLockOut,
        status_code=201,
    )
    async def lock_study(
        deployment_id: str,
        body: StudyLockIn,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_LOCK, resource_param="deployment_id"
        ),
    ) -> StudyLockOut:
        async with get_clinical_session() as s:
            try:
                lock = await ClinicalRepository(s).lock_study(
                    deployment_id,
                    reason=body.reason,
                    actor_sub=user.sub,
                    require_no_open_queries=not body.force_open_queries,
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            view = StudyLockOut.model_validate(lock)
        # Sprint A2 auto-status: lock advances the trial to 'locked'.
        from ..services.trial_status import promote_to_locked_for_deployment

        await promote_to_locked_for_deployment(deployment_id)
        return view

    @router.post(
        "/deployments/{deployment_id}/unlock",
        response_model=StudyLockOut,
    )
    async def unlock_study(
        deployment_id: str,
        body: StudyUnlockIn,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_LOCK, resource_param="deployment_id"
        ),
    ) -> StudyLockOut:
        async with get_clinical_session() as s:
            try:
                lock = await ClinicalRepository(s).unlock_study(
                    deployment_id,
                    reason=body.reason,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            view = StudyLockOut.model_validate(lock)
        # Sprint A2 auto-status: unlock demotes trial 'locked' →
        # 'deployed'. One-step regression; archived trials untouched.
        from ..services.trial_status import demote_to_deployed_for_deployment

        await demote_to_deployed_for_deployment(deployment_id)
        return view

    @router.get(
        "/deployments/{deployment_id}/lock-history",
        response_model=list[StudyLockOut],
    )
    async def lock_history(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.AUDIT_READ, resource_param="deployment_id"
        ),
    ) -> list[StudyLockOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_study_locks(deployment_id)
            return [StudyLockOut.model_validate(r) for r in rows]

    # ── IRT / Randomisation (E8) ─────────────────────────────────────────

    def _mask_allocation_for_caller(allocation: Any, schedule_blinding: str) -> AllocationOut:
        """Blinded deployments hide the arm until a code-break.

        The Allocation row's `arm` is the source of truth; this helper
        clones it for output and clears the arm when the deployment is
        blinded AND the allocation hasn't been broken.
        """
        out = AllocationOut.model_validate(allocation)
        if schedule_blinding != "open_label" and not allocation.unblinded:
            out = out.model_copy(update={"arm": None})
        return out

    @router.post(
        "/deployments/{deployment_id}/randomization/schedule",
        response_model=ScheduleOut,
        status_code=201,
    )
    async def generate_schedule(
        deployment_id: str,
        body: ScheduleGenerateIn,
        user: SessionPayload = require_permission_scoped(
            Permission.RANDOMIZATION_GENERATE, resource_param="deployment_id"
        ),
    ) -> ScheduleOut:
        import os as _os

        from ..randomization import (
            generate_permuted_block,
            generate_simple,
            generate_stratified_permuted_block,
        )

        seed = body.seed if body.seed is not None else int.from_bytes(_os.urandom(4), "big")
        sequence_json: str | None = None
        minimisation_state_json: str | None = None

        try:
            if body.algorithm == "simple":
                if body.expected_n is None or body.expected_n <= 0:
                    raise HTTPException(422, "expected_n required for simple algorithm.")
                seq = generate_simple(
                    body.expected_n,
                    body.arms,
                    ratio=body.ratio,
                    seed=seed,
                )
                sequence_json = json.dumps(seq)
            elif body.algorithm == "permuted_block":
                if body.expected_n is None or body.expected_n <= 0:
                    raise HTTPException(422, "expected_n required for permuted_block algorithm.")
                seq = generate_permuted_block(
                    body.expected_n,
                    body.arms,
                    ratio=body.ratio,
                    block_sizes=body.block_sizes,
                    seed=seed,
                )
                sequence_json = json.dumps(seq)
            elif body.algorithm == "stratified_permuted_block":
                if not body.expected_per_stratum:
                    raise HTTPException(
                        422,
                        "expected_per_stratum required for stratified_permuted_block.",
                    )
                if not body.strata_factors:
                    raise HTTPException(
                        422,
                        "strata_factors required for stratified_permuted_block.",
                    )
                seq_by_stratum = generate_stratified_permuted_block(
                    body.expected_per_stratum,
                    body.arms,
                    ratio=body.ratio,
                    block_sizes=body.block_sizes,
                    seed=seed,
                )
                sequence_json = json.dumps(seq_by_stratum)
            elif body.algorithm == "minimisation":
                if not body.strata_factors:
                    raise HTTPException(
                        422,
                        "strata_factors required for minimisation algorithm.",
                    )
                # Initial state: zeros for every (arm, factor) pair.
                empty: dict[str, dict[str, dict[str, int]]] = {
                    a: {f: {} for f in body.strata_factors} for a in body.arms
                }
                minimisation_state_json = json.dumps({"counts": empty})
            else:
                raise HTTPException(422, f"Unknown algorithm {body.algorithm!r}.")
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

        async with get_clinical_session() as s:
            try:
                schedule = await ClinicalRepository(s).create_randomization_schedule(
                    deployment_id,
                    algorithm=body.algorithm,
                    arms=body.arms,
                    ratio=body.ratio,
                    block_sizes=body.block_sizes,
                    strata_factors=body.strata_factors,
                    weights=body.weights,
                    seed=seed,
                    blinding=body.blinding,
                    sequence_json=sequence_json,
                    minimisation_state_json=minimisation_state_json,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return ScheduleOut.model_validate(schedule)

    @router.get(
        "/deployments/{deployment_id}/randomization/schedule",
        response_model=ScheduleOut | None,
    )
    async def get_schedule(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.RANDOMIZATION_READ, resource_param="deployment_id"
        ),
    ) -> ScheduleOut | None:
        async with get_clinical_session() as s:
            schedule = await ClinicalRepository(s).get_active_schedule(deployment_id)
            return ScheduleOut.model_validate(schedule) if schedule else None

    @router.post(
        "/deployments/{deployment_id}/randomization/schedule/close",
        response_model=ScheduleOut,
    )
    async def close_schedule(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.RANDOMIZATION_GENERATE, resource_param="deployment_id"
        ),
    ) -> ScheduleOut:
        async with get_clinical_session() as s:
            schedule = await ClinicalRepository(s).get_active_schedule(deployment_id)
            if schedule is None:
                raise HTTPException(404, "No active schedule to close.")
            try:
                closed = await ClinicalRepository(s).close_randomization_schedule(
                    schedule.id, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return ScheduleOut.model_validate(closed)

    @router.post(
        "/subjects/{subject_id}/randomize",
        response_model=AllocationOut,
        status_code=201,
    )
    async def randomize_subject(
        subject_id: str,
        body: AllocateIn,
        user: SessionPayload = require_permission_scoped(
            Permission.RANDOMIZATION_ALLOCATE, resource_param="subject_id"
        ),
    ) -> AllocationOut:
        from ..randomization import (
            canonical_stratum_label,
            pocock_simon_choose_arm,
        )

        async with get_clinical_session() as s:
            subject = await s.get(Subject, subject_id)
            if subject is None:
                raise HTTPException(404, "Subject not found")
            await _require_deployment_unlocked(s, subject.deployment_id)
            repo = ClinicalRepository(s)
            schedule = await repo.get_active_schedule(subject.deployment_id)
            if schedule is None:
                raise HTTPException(
                    409,
                    "No active randomisation schedule for this deployment.",
                )

            arms: list[str] = json.loads(schedule.arms_json)
            strata_factors: list[str] | None = (
                json.loads(schedule.strata_factors_json) if schedule.strata_factors_json else None
            )

            arm: str
            stratum_label: str | None = None
            sequence_position: int | None = None
            updated_state_json: str | None = None

            try:
                if schedule.algorithm in ("simple", "permuted_block"):
                    sequence: list[str] = json.loads(schedule.sequence_json or "[]")
                    used = await s.scalar(
                        select(func.count(Allocation.id)).where(
                            Allocation.schedule_id == schedule.id
                        )
                    )
                    used = int(used or 0)
                    if used >= len(sequence):
                        raise HTTPException(
                            409,
                            "Randomisation sequence exhausted — generate a "
                            "new schedule or extend the existing one.",
                        )
                    arm = sequence[used]
                    sequence_position = used
                elif schedule.algorithm == "stratified_permuted_block":
                    if not strata_factors:
                        raise HTTPException(500, "Schedule missing strata_factors.")
                    missing = [f for f in strata_factors if f not in body.factor_values]
                    if missing:
                        raise HTTPException(
                            422,
                            f"factor_values missing required factors: {missing!r}",
                        )
                    relevant = {f: body.factor_values[f] for f in strata_factors}
                    stratum_label = canonical_stratum_label(relevant)
                    by_stratum: dict[str, list[str]] = json.loads(schedule.sequence_json or "{}")
                    stratum_seq = by_stratum.get(stratum_label, [])
                    used_in_stratum = await s.scalar(
                        select(func.count(Allocation.id))
                        .where(Allocation.schedule_id == schedule.id)
                        .where(Allocation.stratum_label == stratum_label)
                    )
                    used_in_stratum = int(used_in_stratum or 0)
                    if used_in_stratum >= len(stratum_seq):
                        raise HTTPException(
                            409,
                            f"Stratum {stratum_label!r} sequence exhausted — "
                            "extend the schedule for this stratum.",
                        )
                    arm = stratum_seq[used_in_stratum]
                    sequence_position = used_in_stratum
                elif schedule.algorithm == "minimisation":
                    if not strata_factors:
                        raise HTTPException(500, "Schedule missing strata_factors.")
                    missing = [f for f in strata_factors if f not in body.factor_values]
                    if missing:
                        raise HTTPException(
                            422,
                            f"factor_values missing required factors: {missing!r}",
                        )
                    relevant = {f: body.factor_values[f] for f in strata_factors}
                    stratum_label = canonical_stratum_label(relevant)
                    weights = json.loads(schedule.weights_json) if schedule.weights_json else None
                    current_state = (
                        json.loads(schedule.minimisation_state_json)
                        if schedule.minimisation_state_json
                        else None
                    )
                    arm, new_state = pocock_simon_choose_arm(
                        current_state,
                        relevant,
                        arms,
                        weights=weights,
                        seed=schedule.seed
                        + len(await repo.list_allocations(subject.deployment_id)),
                    )
                    updated_state_json = json.dumps(new_state)
                else:
                    raise HTTPException(500, f"Unsupported algorithm {schedule.algorithm!r}.")

                allocation = await repo.persist_allocation(
                    schedule=schedule,
                    subject=subject,
                    arm=arm,
                    stratum_label=stratum_label,
                    factor_values=dict(body.factor_values) or None,
                    sequence_position=sequence_position,
                    actor_sub=user.sub,
                    updated_minimisation_state_json=updated_state_json,
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return _mask_allocation_for_caller(allocation, schedule.blinding)

    @router.get(
        "/subjects/{subject_id}/allocation",
        response_model=AllocationOut | None,
    )
    async def get_subject_allocation(
        subject_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.RANDOMIZATION_READ, resource_param="subject_id"
        ),
    ) -> AllocationOut | None:
        async with get_clinical_session() as s:
            repo = ClinicalRepository(s)
            allocation = await repo.get_allocation_for_subject(subject_id)
            if allocation is None:
                return None
            schedule = await s.get(RandomizationSchedule, allocation.schedule_id)
            blinding = schedule.blinding if schedule else "open_label"
            return _mask_allocation_for_caller(allocation, blinding)

    @router.get(
        "/deployments/{deployment_id}/allocations",
        response_model=list[AllocationOut],
    )
    async def list_deployment_allocations(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.RANDOMIZATION_READ, resource_param="deployment_id"
        ),
    ) -> list[AllocationOut]:
        async with get_clinical_session() as s:
            repo = ClinicalRepository(s)
            rows = await repo.list_allocations(deployment_id)
            schedule = await repo.get_active_schedule(deployment_id)
            blinding = schedule.blinding if schedule else "open_label"
            return [_mask_allocation_for_caller(r, blinding) for r in rows]

    @router.post(
        "/subjects/{subject_id}/code-break",
        response_model=CodeBreakOut,
        status_code=201,
    )
    async def code_break_subject(
        subject_id: str,
        body: CodeBreakIn,
        user: SessionPayload = require_permission_scoped(
            Permission.RANDOMIZATION_CODEBREAK, resource_param="subject_id"
        ),
    ) -> CodeBreakOut:
        async with get_clinical_session() as s:
            try:
                event = await ClinicalRepository(s).code_break(
                    subject_id, reason=body.reason, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return CodeBreakOut.model_validate(event)

    @router.get(
        "/deployments/{deployment_id}/code-break-events",
        response_model=list[CodeBreakOut],
    )
    async def list_deployment_code_breaks(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.RANDOMIZATION_READ, resource_param="deployment_id"
        ),
    ) -> list[CodeBreakOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_code_break_events(deployment_id)
            return [CodeBreakOut.model_validate(r) for r in rows]

    # ── Recruitment / screening log (P1 #3) ──────────────────────────────

    @router.post(
        "/deployments/{deployment_id}/screening",
        response_model=ScreeningLogOut,
        status_code=201,
    )
    async def record_screening(
        deployment_id: str,
        body: ScreeningLogIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SCREENING_RECORD, resource_param="deployment_id"
        ),
    ) -> ScreeningLogOut:
        async with get_clinical_session() as s:
            await _require_deployment_unlocked(s, deployment_id)
            try:
                log = await ClinicalRepository(s).record_screening(
                    deployment_id=deployment_id,
                    screening_code=body.screening_code,
                    screening_date=body.screening_date,
                    site_id=body.site_id,
                    age_band=body.age_band,
                    sex=body.sex,
                    race=body.race,
                    ethnicity=body.ethnicity,
                    dob_year=body.dob_year,
                    notes=body.notes,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return ScreeningLogOut.model_validate(log)

    @router.get(
        "/deployments/{deployment_id}/screening",
        response_model=list[ScreeningLogOut],
    )
    async def list_screening_logs(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SCREENING_READ, resource_param="deployment_id"
        ),
        site_id: str | None = None,
        eligibility_status: str | None = None,
        consent_status: str | None = None,
        enrolment_status: str | None = None,
    ) -> list[ScreeningLogOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_screening_logs(
                deployment_id=deployment_id,
                site_id=site_id,
                eligibility_status=eligibility_status,
                consent_status=consent_status,
                enrolment_status=enrolment_status,
            )
            return [ScreeningLogOut.model_validate(r) for r in rows]

    @router.patch(
        "/screening/{log_id}/eligibility",
        response_model=ScreeningLogOut,
    )
    async def update_screening_eligibility(
        log_id: str,
        body: ScreeningEligibilityIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SCREENING_UPDATE, resource_param="log_id"
        ),
    ) -> ScreeningLogOut:
        async with get_clinical_session() as s:
            try:
                log = await ClinicalRepository(s).update_screening_eligibility(
                    log_id,
                    eligibility_status=body.eligibility_status,
                    exclusion_reason_code=body.exclusion_reason_code,
                    exclusion_reason_text=body.exclusion_reason_text,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return ScreeningLogOut.model_validate(log)

    @router.patch(
        "/screening/{log_id}/consent",
        response_model=ScreeningLogOut,
    )
    async def update_screening_consent(
        log_id: str,
        body: ScreeningConsentIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SCREENING_UPDATE, resource_param="log_id"
        ),
    ) -> ScreeningLogOut:
        async with get_clinical_session() as s:
            try:
                log = await ClinicalRepository(s).update_screening_consent(
                    log_id,
                    consent_status=body.consent_status,
                    consent_date=body.consent_date,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return ScreeningLogOut.model_validate(log)

    @router.patch(
        "/screening/{log_id}/enrolment",
        response_model=ScreeningLogOut,
    )
    async def update_screening_enrolment(
        log_id: str,
        body: ScreeningEnrolmentIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SCREENING_UPDATE, resource_param="log_id"
        ),
    ) -> ScreeningLogOut:
        async with get_clinical_session() as s:
            try:
                log = await ClinicalRepository(s).update_screening_enrolment(
                    log_id,
                    enrolment_status=body.enrolment_status,
                    enrolled_subject_id=body.enrolled_subject_id,
                    enrolment_date=body.enrolment_date,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return ScreeningLogOut.model_validate(log)

    @router.get(
        "/deployments/{deployment_id}/recruitment/funnel",
        response_model=RecruitmentFunnelOut,
    )
    async def get_recruitment_funnel(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SCREENING_READ, resource_param="deployment_id"
        ),
        site_id: str | None = None,
    ) -> RecruitmentFunnelOut:
        async with get_clinical_session() as s:
            payload = await ClinicalRepository(s).recruitment_funnel(deployment_id, site_id=site_id)
            return RecruitmentFunnelOut.model_validate(payload)

    # ── Visit scheduling + reminders (P1 #4) ─────────────────────────────

    @router.post(
        "/deployments/{deployment_id}/visit-schedules",
        response_model=VisitScheduleOut,
        status_code=201,
    )
    async def create_visit_schedule(
        deployment_id: str,
        body: VisitScheduleIn,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_SCHEDULE_AUTHOR, resource_param="deployment_id"
        ),
    ) -> VisitScheduleOut:
        async with get_clinical_session() as s:
            try:
                schedule = await ClinicalRepository(s).create_visit_schedule(
                    deployment_id,
                    name=body.name,
                    description=body.description,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return VisitScheduleOut.model_validate(schedule)

    @router.get(
        "/deployments/{deployment_id}/visit-schedules",
        response_model=list[VisitScheduleOut],
    )
    async def list_visit_schedules(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_SCHEDULE_READ, resource_param="deployment_id"
        ),
    ) -> list[VisitScheduleOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_visit_schedules(deployment_id)
            return [VisitScheduleOut.model_validate(r) for r in rows]

    @router.post(
        "/visit-schedules/{schedule_id}/activate",
        response_model=VisitScheduleOut,
    )
    async def activate_visit_schedule(
        schedule_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_SCHEDULE_AUTHOR, resource_param="schedule_id"
        ),
    ) -> VisitScheduleOut:
        async with get_clinical_session() as s:
            try:
                schedule = await ClinicalRepository(s).set_active_visit_schedule(
                    schedule_id, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return VisitScheduleOut.model_validate(schedule)

    @router.post(
        "/visit-schedules/{schedule_id}/visits",
        response_model=ScheduledVisitOut,
        status_code=201,
    )
    async def add_scheduled_visit(
        schedule_id: str,
        body: ScheduledVisitIn,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_SCHEDULE_AUTHOR, resource_param="schedule_id"
        ),
    ) -> ScheduledVisitOut:
        async with get_clinical_session() as s:
            try:
                visit = await ClinicalRepository(s).add_scheduled_visit(
                    schedule_id,
                    visit_name=body.visit_name,
                    day_offset=body.day_offset,
                    window_before_days=body.window_before_days,
                    window_after_days=body.window_after_days,
                    reminder_offsets=body.reminder_offsets,
                    ordering=body.ordering,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return ScheduledVisitOut.model_validate(visit)

    @router.get(
        "/visit-schedules/{schedule_id}/visits",
        response_model=list[ScheduledVisitOut],
    )
    async def list_scheduled_visits(
        schedule_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_SCHEDULE_READ, resource_param="schedule_id"
        ),
    ) -> list[ScheduledVisitOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_scheduled_visits(schedule_id)
            return [ScheduledVisitOut.model_validate(r) for r in rows]

    @router.post(
        "/subjects/{subject_id}/planned-visits/generate",
        response_model=list[PlannedVisitOut],
    )
    async def generate_planned_visits(
        subject_id: str,
        body: GeneratePlannedVisitsIn,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_UPDATE, resource_param="subject_id"
        ),
    ) -> list[PlannedVisitOut]:
        async with get_clinical_session() as s:
            try:
                rows = await ClinicalRepository(s).generate_planned_visits(
                    subject_id,
                    baseline_date=body.baseline_date,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return [PlannedVisitOut.model_validate(r) for r in rows]

    @router.get(
        "/subjects/{subject_id}/planned-visits",
        response_model=list[PlannedVisitOut],
    )
    async def list_subject_planned_visits(
        subject_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_SCHEDULE_READ, resource_param="subject_id"
        ),
        status: str | None = None,
    ) -> list[PlannedVisitOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_planned_visits(
                subject_id=subject_id, status=status
            )
            return [PlannedVisitOut.model_validate(r) for r in rows]

    @router.get(
        "/deployments/{deployment_id}/planned-visits",
        response_model=list[PlannedVisitOut],
    )
    async def list_deployment_planned_visits(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_SCHEDULE_READ, resource_param="deployment_id"
        ),
        status: str | None = None,
    ) -> list[PlannedVisitOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_planned_visits(
                deployment_id=deployment_id, status=status
            )
            return [PlannedVisitOut.model_validate(r) for r in rows]

    @router.patch(
        "/planned-visits/{planned_visit_id}",
        response_model=PlannedVisitOut,
    )
    async def update_planned_visit(
        planned_visit_id: str,
        body: PlannedVisitUpdateIn,
        user: SessionPayload = require_permission_scoped(
            Permission.VISIT_UPDATE, resource_param="planned_visit_id"
        ),
    ) -> PlannedVisitOut:
        async with get_clinical_session() as s:
            try:
                row = await ClinicalRepository(s).update_planned_visit(
                    planned_visit_id,
                    planned_date=body.planned_date,
                    status=body.status,
                    override_reason=body.override_reason,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return PlannedVisitOut.model_validate(row)

    @router.put(
        "/participant-access/{access_id}/contact",
        response_model=ParticipantContactOut,
    )
    async def upsert_participant_contact(
        access_id: str,
        body: ParticipantContactIn,
        user: SessionPayload = require_permission_scoped(
            Permission.PARTICIPANT_CONTACT_MANAGE, resource_param="access_id"
        ),
    ) -> ParticipantContactOut:
        async with get_clinical_session() as s:
            try:
                contact = await ClinicalRepository(s).upsert_participant_contact(
                    access_id,
                    email=body.email,
                    phone=body.phone,
                    preferred_channel=body.preferred_channel,
                    opt_in_channels=body.opt_in_channels,
                    opt_out=body.opt_out,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return ParticipantContactOut.model_validate(contact)

    @router.get(
        "/deployments/{deployment_id}/reminders",
        response_model=list[SentReminderOut],
    )
    async def list_sent_reminders(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.REMINDER_READ, resource_param="deployment_id"
        ),
    ) -> list[SentReminderOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_sent_reminders(deployment_id=deployment_id)
            return [SentReminderOut.model_validate(r) for r in rows]

    @router.post(
        "/deployments/{deployment_id}/reminders/run-due",
        response_model=ReminderRunResultOut,
    )
    async def run_due_reminders(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.REMINDER_SEND, resource_param="deployment_id"
        ),
    ) -> ReminderRunResultOut:
        """Manual trigger for the reminder fire pipeline. Useful when the
        APScheduler interval would take too long for a sponsor-asked
        weekly burst, or for testing."""
        from ..services.reminders import fire_due_reminders

        result = await fire_due_reminders(deployment_id)
        return ReminderRunResultOut.model_validate(result.as_dict())

    # ── Source-document extraction (P1 #5) ───────────────────────────────

    _MAX_SOURCE_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

    @router.post(
        "/deployments/{deployment_id}/source-documents",
        response_model=SourceDocumentOut,
        status_code=201,
    )
    async def upload_source_document(
        deployment_id: str,
        file: UploadFile = File(...),
        subject_code_field: str | None = Form(default=None),
        notes: str = Form(default=""),
        user: SessionPayload = require_permission_scoped(
            Permission.SOURCE_DOCUMENT_UPLOAD, resource_param="deployment_id"
        ),
    ) -> SourceDocumentOut:
        """Upload a CSV source-data file.

        The platform does NOT enforce de-identification — operators are
        expected to upload pre-de-identified data per their protocol's
        PHI policy. Content-hash dedupe: re-uploading the same file
        returns the existing row.
        """
        data = await file.read()
        if not data:
            raise HTTPException(422, "Empty file.")
        if len(data) > _MAX_SOURCE_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"File exceeds {_MAX_SOURCE_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            )
        async with get_clinical_session() as s:
            try:
                doc, _added = await ClinicalRepository(s).ingest_source_document(
                    deployment_id,
                    filename=file.filename or "upload.csv",
                    raw_bytes=data,
                    subject_code_field=subject_code_field,
                    actor_sub=user.sub,
                )
                if notes:
                    doc.notes = notes
                    await s.flush()
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return SourceDocumentOut.model_validate(doc)

    @router.get(
        "/deployments/{deployment_id}/source-documents",
        response_model=list[SourceDocumentOut],
    )
    async def list_deployment_source_documents(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SOURCE_DOCUMENT_READ, resource_param="deployment_id"
        ),
    ) -> list[SourceDocumentOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_source_documents(deployment_id)
            return [SourceDocumentOut.model_validate(r) for r in rows]

    @router.get(
        "/source-documents/{doc_id}/rows",
        response_model=list[SourceRowOut],
    )
    async def list_source_rows(
        doc_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SOURCE_DOCUMENT_READ, resource_param="doc_id"
        ),
    ) -> list[SourceRowOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_source_rows(doc_id)
            return [SourceRowOut.model_validate(r) for r in rows]

    @router.post(
        "/deployments/{deployment_id}/extraction-mappings",
        response_model=ExtractionMappingOut,
        status_code=201,
    )
    async def create_extraction_mapping(
        deployment_id: str,
        body: ExtractionMappingIn,
        user: SessionPayload = require_permission_scoped(
            Permission.EXTRACTION_MAPPING_AUTHOR, resource_param="deployment_id"
        ),
    ) -> ExtractionMappingOut:
        async with get_clinical_session() as s:
            try:
                m = await ClinicalRepository(s).create_extraction_mapping(
                    deployment_id,
                    deployed_form_id=body.deployed_form_id,
                    name=body.name,
                    subject_code_field=body.subject_code_field,
                    mapping=body.mapping,
                    notes=body.notes,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return ExtractionMappingOut.model_validate(m)

    @router.get(
        "/deployments/{deployment_id}/extraction-mappings",
        response_model=list[ExtractionMappingOut],
    )
    async def list_extraction_mappings(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SOURCE_DOCUMENT_READ, resource_param="deployment_id"
        ),
        deployed_form_id: str | None = None,
    ) -> list[ExtractionMappingOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_extraction_mappings(
                deployment_id, deployed_form_id=deployed_form_id
            )
            return [ExtractionMappingOut.model_validate(r) for r in rows]

    @router.post(
        "/extraction-mappings/{mapping_id}/dry-run",
    )
    async def dry_run_extraction(
        mapping_id: str,
        source_document_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.EXTRACTION_MAPPING_APPLY, resource_param="mapping_id"
        ),
    ) -> list[dict[str, object]]:
        async with get_clinical_session() as s:
            try:
                return await ClinicalRepository(s).dry_run_extraction(
                    mapping_id, source_document_id=source_document_id
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e

    @router.post(
        "/extraction-mappings/{mapping_id}/apply",
        response_model=ExtractionApplyResultOut,
    )
    async def apply_extraction(
        mapping_id: str,
        body: ExtractionApplyIn,
        user: SessionPayload = require_permission_scoped(
            Permission.EXTRACTION_MAPPING_APPLY, resource_param="mapping_id"
        ),
    ) -> ExtractionApplyResultOut:
        if body.mode not in ("subjects", "table"):
            raise HTTPException(
                422,
                f"Invalid mode {body.mode!r}; choose 'subjects' or 'table'.",
            )
        async with get_clinical_session() as s:
            try:
                if body.mode == "subjects":
                    summary = await ClinicalRepository(s).apply_extraction_to_subjects(
                        mapping_id,
                        source_document_id=body.source_document_id,
                        actor_sub=user.sub,
                    )
                    return ExtractionApplyResultOut(
                        mode="subjects",
                        subjects_filled=summary["subjects_filled"],
                        items_written=summary["items_written"],
                        source_rows_unmatched=summary["source_rows_unmatched"],
                    )
                table = await ClinicalRepository(s).apply_extraction_to_table(
                    mapping_id,
                    source_document_id=body.source_document_id,
                    actor_sub=user.sub,
                )
                return ExtractionApplyResultOut(mode="table", table_rows=table)
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e

    @router.get(
        "/deployments/{deployment_id}/extraction-fills",
        response_model=list[ExtractionFillOut],
    )
    async def list_extraction_fills(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.EXTRACTION_AUDIT_READ, resource_param="deployment_id"
        ),
        target_id: str | None = None,
        source_document_id: str | None = None,
    ) -> list[ExtractionFillOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_extraction_fills(
                deployment_id=deployment_id,
                target_id=target_id,
                source_document_id=source_document_id,
            )
            return [ExtractionFillOut.model_validate(r) for r in rows]

    # ── Drug accountability (P2 #3) ────────────────────────────────────

    @router.post(
        "/deployments/{deployment_id}/ip-catalogue",
        response_model=InvestigationalProductOut,
        status_code=201,
    )
    async def register_investigational_product(
        deployment_id: str,
        body: InvestigationalProductIn,
        user: SessionPayload = require_permission_scoped(
            Permission.IP_CATALOGUE, resource_param="deployment_id"
        ),
    ) -> InvestigationalProductOut:
        """Register an investigational product against this deployment.
        Authored by the study_designer at study-design time; data_manager
        can add mid-study additions when a new lot is introduced."""
        async with get_clinical_session() as s:
            await _require_deployment_unlocked(s, deployment_id)
            try:
                ip = await ClinicalRepository(s).register_investigational_product(
                    deployment_id,
                    drug_name=body.drug_name,
                    strength=body.strength,
                    units=body.units,
                    kit_id_pattern=body.kit_id_pattern,
                    notes=body.notes,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return InvestigationalProductOut.model_validate(ip)

    @router.get(
        "/deployments/{deployment_id}/ip-catalogue",
        response_model=list[InvestigationalProductOut],
    )
    async def list_investigational_products(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_READ, resource_param="deployment_id"
        ),
    ) -> list[InvestigationalProductOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_investigational_products(deployment_id)
            return [InvestigationalProductOut.model_validate(r) for r in rows]

    @router.post(
        "/deployments/{deployment_id}/drug-receipts",
        response_model=DrugReceiptOut,
        status_code=201,
    )
    async def record_drug_receipt(
        deployment_id: str,
        body: DrugReceiptIn,
        user: SessionPayload = require_permission_scoped(
            Permission.IP_RECEIVE, resource_param="deployment_id"
        ),
    ) -> DrugReceiptOut:
        """Log a shipment of IP arriving at the site / central depot."""
        async with get_clinical_session() as s:
            await _require_deployment_unlocked(s, deployment_id)
            try:
                receipt = await ClinicalRepository(s).record_drug_receipt(
                    deployment_id,
                    ip_id=body.ip_id,
                    lot_number=body.lot_number,
                    quantity_received=body.quantity_received,
                    site_id=body.site_id,
                    expiry_date=body.expiry_date,
                    packing_slip_ref=body.packing_slip_ref,
                    temp_excursion_flag=body.temp_excursion_flag,
                    notes=body.notes,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return DrugReceiptOut.model_validate(receipt)

    @router.get(
        "/deployments/{deployment_id}/drug-receipts",
        response_model=list[DrugReceiptOut],
    )
    async def list_drug_receipts(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_READ, resource_param="deployment_id"
        ),
        ip_id: str | None = None,
        lot_number: str | None = None,
    ) -> list[DrugReceiptOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_drug_receipts(
                deployment_id=deployment_id,
                ip_id=ip_id,
                lot_number=lot_number,
            )
            return [DrugReceiptOut.model_validate(r) for r in rows]

    @router.post(
        "/deployments/{deployment_id}/drug-dispensations",
        response_model=DrugDispensationOut,
        status_code=201,
    )
    async def record_drug_dispensation(
        deployment_id: str,
        body: DrugDispensationIn,
        user: SessionPayload = require_permission_scoped(
            Permission.IP_DISPENSE, resource_param="deployment_id"
        ),
    ) -> DrugDispensationOut:
        """Hand a kit from site to subject. Refuses if the requested
        quantity would drive lot inventory negative."""
        async with get_clinical_session() as s:
            await _require_deployment_unlocked(s, deployment_id)
            try:
                disp = await ClinicalRepository(s).record_drug_dispensation(
                    deployment_id,
                    subject_id=body.subject_id,
                    ip_id=body.ip_id,
                    lot_number=body.lot_number,
                    kit_id=body.kit_id,
                    quantity_dispensed=body.quantity_dispensed,
                    planned_visit_id=body.planned_visit_id,
                    notes=body.notes,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return DrugDispensationOut.model_validate(disp)

    @router.get(
        "/deployments/{deployment_id}/drug-dispensations",
        response_model=list[DrugDispensationOut],
    )
    async def list_drug_dispensations(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_READ, resource_param="deployment_id"
        ),
    ) -> list[DrugDispensationOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_drug_dispensations(deployment_id=deployment_id)
            return [DrugDispensationOut.model_validate(r) for r in rows]

    @router.post(
        "/drug-dispensations/{dispensation_id}/return",
        response_model=DrugReturnOut,
        status_code=201,
    )
    async def record_drug_return(
        dispensation_id: str,
        body: DrugReturnIn,
        user: SessionPayload = require_permission_scoped(
            Permission.IP_RETURN, resource_param="dispensation_id"
        ),
    ) -> DrugReturnOut:
        """Log a return event against a prior dispensation. The
        repository enforces quantity_used + quantity_lost <=
        quantity_returned <= quantity_dispensed."""
        async with get_clinical_session() as s:
            from ..persistence.clinical.models import DrugDispensation

            parent = await s.get(DrugDispensation, dispensation_id)
            if parent is None:
                raise HTTPException(404, "Dispensation not found.")
            await _require_deployment_unlocked(s, parent.deployment_id)
            try:
                ret = await ClinicalRepository(s).record_drug_return(
                    dispensation_id,
                    quantity_returned=body.quantity_returned,
                    quantity_used=body.quantity_used,
                    quantity_lost=body.quantity_lost,
                    return_reason=body.return_reason,
                    notes=body.notes,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return DrugReturnOut.model_validate(ret)

    @router.get(
        "/deployments/{deployment_id}/drug-returns",
        response_model=list[DrugReturnOut],
    )
    async def list_drug_returns(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_READ, resource_param="deployment_id"
        ),
    ) -> list[DrugReturnOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_drug_returns(deployment_id=deployment_id)
            return [DrugReturnOut.model_validate(r) for r in rows]

    @router.get(
        "/deployments/{deployment_id}/drug-reconciliation",
        response_model=DrugReconciliationOut,
    )
    async def drug_reconciliation(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.IP_RECONCILE, resource_param="deployment_id"
        ),
    ) -> DrugReconciliationOut:
        """Per-lot inventory + activity rollup. Gated on ip.reconcile so
        coordinators don't see the cross-site rollup unless they also hold
        DM / monitor / auditor / PI."""
        async with get_clinical_session() as s:
            rollup = await ClinicalRepository(s).drug_reconciliation(deployment_id)
            return DrugReconciliationOut.model_validate(rollup)

    @router.get(
        "/deployments/{deployment_id}/drug-compliance",
        response_model=SubjectComplianceOut,
    )
    async def subject_drug_compliance(
        deployment_id: str,
        subject_id: str | None = None,
        user: SessionPayload = require_permission_scoped(
            Permission.IP_RECONCILE, resource_param="deployment_id"
        ),
    ) -> SubjectComplianceOut:
        """Per-subject compliance = used / dispensed. Same ip.reconcile
        gate as the inventory rollup. Optional ?subject_id= scopes to one
        subject. A lower bound while drug is still out (see repository)."""
        async with get_clinical_session() as s:
            rollup = await ClinicalRepository(s).subject_drug_compliance(
                deployment_id, subject_id=subject_id
            )
            return SubjectComplianceOut.model_validate(rollup)

    # ── Lab-data feeds (P2 #6) ─────────────────────────────────────────

    _MAX_LAB_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
    _CONTENT_TYPE_TO_FORMAT = {
        "application/hl7-v2+er7": "hl7v2",
        "application/x-hl7-v2": "hl7v2",
        "text/x-hl7-v2": "hl7v2",
        "text/hl7-v2": "hl7v2",
        "application/json": "fhir",
        "application/fhir+json": "fhir",
        "text/tab-separated-values": "cdisc_lab",
        "text/tsv": "cdisc_lab",
        "text/csv": "cdisc_lab",
        "application/x-cdisc-lab": "cdisc_lab",
    }

    @router.post(
        "/deployments/{deployment_id}/lab-uploads",
        response_model=LabIngestResultOut,
        status_code=201,
    )
    async def upload_lab_batch(
        deployment_id: str,
        file: UploadFile = File(...),
        source_format: str = Form(...),
        notes: str = Form(default=""),
        user: SessionPayload = require_permission_scoped(
            Permission.LAB_UPLOAD, resource_param="deployment_id"
        ),
    ) -> LabIngestResultOut:
        """Operator-driven upload: pick a format + attach a file.

        Re-uploads of the same file dedupe by SHA-256 (returns the
        existing batch with `was_new=False`).
        """
        data = await file.read()
        if not data:
            raise HTTPException(422, "Empty file.")
        if len(data) > _MAX_LAB_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"File exceeds {_MAX_LAB_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            )
        async with get_clinical_session() as s:
            await _require_deployment_unlocked(s, deployment_id)
            try:
                batch, was_new, warnings = await ClinicalRepository(s).ingest_lab_batch(
                    deployment_id,
                    source_format=source_format,
                    raw_bytes=data,
                    filename=file.filename,
                    actor_sub=user.sub,
                )
                if notes and was_new:
                    batch.notes = notes
                    await s.flush()
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return LabIngestResultOut(
                batch=LabBatchOut.model_validate(batch),
                was_new=was_new,
                warnings=warnings,
            )

    @router.post(
        "/deployments/{deployment_id}/lab-feed",
        response_model=LabIngestResultOut,
        status_code=201,
    )
    async def lab_listener_endpoint(
        request: Request,
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.LAB_UPLOAD, resource_param="deployment_id"
        ),
    ) -> LabIngestResultOut:
        """System-to-system listener endpoint.

        Source format is derived from the Content-Type header (or the
        `?source_format=` query string when the sender can't set it).
        Authenticated like every other endpoint — bearer token in the
        Cognito session. Lab vendors typically configure a service
        account with `lab.upload`.
        """
        body = await request.body()
        if not body:
            raise HTTPException(422, "Empty body.")
        if len(body) > _MAX_LAB_UPLOAD_BYTES:
            raise HTTPException(413, "Payload too large.")
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        fmt = request.query_params.get("source_format") or _CONTENT_TYPE_TO_FORMAT.get(ctype)
        if not fmt:
            raise HTTPException(
                422,
                "Unable to infer source_format. Set Content-Type or pass "
                "?source_format=hl7v2|cdisc_lab|fhir.",
            )
        async with get_clinical_session() as s:
            await _require_deployment_unlocked(s, deployment_id)
            try:
                batch, was_new, warnings = await ClinicalRepository(s).ingest_lab_batch(
                    deployment_id,
                    source_format=fmt,
                    raw_bytes=body,
                    filename=None,
                    actor_sub=user.sub,
                )
            except ClinicalError as e:
                raise HTTPException(422, str(e)) from e
            return LabIngestResultOut(
                batch=LabBatchOut.model_validate(batch),
                was_new=was_new,
                warnings=warnings,
            )

    @router.get(
        "/deployments/{deployment_id}/lab-batches",
        response_model=list[LabBatchOut],
    )
    async def list_lab_batches(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.LAB_READ, resource_param="deployment_id"
        ),
    ) -> list[LabBatchOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_lab_batches(deployment_id)
            return [LabBatchOut.model_validate(r) for r in rows]

    @router.get(
        "/lab-batches/{batch_id}/results",
        response_model=list[LabResultOut],
    )
    async def list_results_for_batch(
        batch_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.LAB_READ, resource_param="batch_id"
        ),
    ) -> list[LabResultOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_lab_results(batch_id=batch_id)
            return [LabResultOut.model_validate(r) for r in rows]

    @router.get(
        "/deployments/{deployment_id}/lab-results",
        response_model=list[LabResultOut],
    )
    async def list_lab_results(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.LAB_READ, resource_param="deployment_id"
        ),
        subject_id: str | None = None,
        test_code: str | None = None,
    ) -> list[LabResultOut]:
        async with get_clinical_session() as s:
            rows = await ClinicalRepository(s).list_lab_results(
                deployment_id=deployment_id,
                subject_id=subject_id,
                test_code=test_code,
            )
            return [LabResultOut.model_validate(r) for r in rows]

    @router.post(
        "/deployments/{deployment_id}/lab-results/backfill-subjects",
        response_model=dict,
    )
    async def backfill_lab_subjects(
        deployment_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.LAB_UPLOAD, resource_param="deployment_id"
        ),
    ) -> dict[str, int]:
        """Re-link parsed-lab rows whose subject_code_hint now matches a
        Subject. Run after enrolling a new cohort if labs arrived first."""
        async with get_clinical_session() as s:
            await _require_deployment_unlocked(s, deployment_id)
            count = await ClinicalRepository(s).backfill_lab_subject_link(
                deployment_id, actor_sub=user.sub
            )
            return {"updated": count}

    # ── Multi-site rollup (P2 #5) ──────────────────────────────────────

    @router.get(
        "/deployments/{deployment_id}/multisite",
        response_model=MultiSiteRollupOut,
    )
    async def multisite_per_deployment_rollup(
        deployment_id: str,
        low_ip_threshold: int = 10,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_READ, resource_param="deployment_id"
        ),
    ) -> MultiSiteRollupOut:
        """Per-site rollup across every site in the deployment.

        Returns four KPI groups per site: enrolment funnel, query
        backlog, safety load, and operational health. Gated on
        study.read at deployment scope — same threshold as every
        other 'see this trial' endpoint.
        """
        from ..services.multisite import site_rollup

        async with get_clinical_session() as s:
            try:
                card = await site_rollup(
                    s,
                    deployment_id=deployment_id,
                    low_ip_threshold=low_ip_threshold,
                )
            except ValueError as e:
                raise HTTPException(404, str(e)) from e
            return MultiSiteRollupOut.model_validate(card.as_dict())

    return router
