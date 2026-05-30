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

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth import SessionPayload
from ..auth.cognito_admin import (
    CognitoAdminError,
    verify_user_password_async,
)
from ..auth.rbac import Permission
from ..config import get_settings
from ..domain.ecrf import FormDefinition
from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.models import Subject
from ..persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
    FormSnapshot,
    HardCheckError,
    LockedError,
)
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
    is_serious: bool
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
            is_serious=ae.is_serious,
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
            return DeploymentOut.model_validate(deployment)

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
                result = await run_derivation(
                    s, deployment_id=deployment_id, triggered_by=user.sub
                )
            except ValueError as e:
                raise HTTPException(404, str(e)) from e
            return {
                "deployment_id": result.deployment_id,
                "triggered_at": result.triggered_at.isoformat(),
                "counts": result.counts,
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
            else:
                raise HTTPException(
                    400,
                    f"Unknown domain {domain!r}. Choose one of: "
                    "DM, AE, VS, LB, EX, CM, MH, ADSL.",
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
            else:
                raise HTTPException(
                    400,
                    f"Unknown domain {domain!r}. Choose one of: "
                    "DM, AE, VS, LB, EX, CM, MH, ADSL.",
                )
        return Response(
            content=payload,
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{domain_upper.lower()}.csv"'
                ),
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
            return StudyLockOut.model_validate(lock)

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
            return StudyLockOut.model_validate(lock)

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

    return router
