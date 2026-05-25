"""EDC capture API (eCRF E1) — the collection plane.

Distinct from the `/api/ecrf/*` authoring API: this namespace deploys a
published study for data collection and captures subject data into the
separate clinical-data (PHI) store, with a full audit trail. Writes require
the `data_entry`/`admin` role; the future Data Collector UI (E4) consumes
this API (it never touches the database directly — D5).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from ..domain.ecrf import FormDefinition
from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
    FormSnapshot,
    HardCheckError,
    LockedError,
)
from ..persistence.database import get_db_session
from ..persistence.ecrf_repository import EcrfRepository
from .auth import AdminUser, DataEntryUser

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
    model_config = ConfigDict(extra="forbid")
    meaning: str


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
    model_config = ConfigDict(extra="forbid")
    meaning: str


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


def create_edc_router() -> APIRouter:
    router = APIRouter(prefix="/edc", tags=["edc"])

    # ── deployments ──────────────────────────────────────────────────────

    @router.post("/deployments", response_model=DeploymentOut, status_code=201)
    async def deploy(body: DeploymentIn, user: DataEntryUser) -> DeploymentOut:
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
    async def list_deployments() -> list[DeploymentOut]:
        async with get_clinical_session() as s:
            return [
                DeploymentOut.model_validate(d)
                for d in await ClinicalRepository(s).list_deployments()
            ]

    @router.get("/deployments/{deployment_id}/forms", response_model=list[DeployedFormOut])
    async def list_deployed_forms(deployment_id: str) -> list[DeployedFormOut]:
        async with get_clinical_session() as s:
            forms = await ClinicalRepository(s).list_deployed_forms(deployment_id)
            return [DeployedFormOut.model_validate(f) for f in forms]

    @router.get("/deployed-forms/{deployed_form_id}", response_model=DeployedFormDetailOut)
    async def get_deployed_form(deployed_form_id: str) -> DeployedFormDetailOut:
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
    async def add_site(deployment_id: str, body: SiteIn, user: DataEntryUser) -> SiteOut:
        async with get_clinical_session() as s:
            try:
                site = await ClinicalRepository(s).add_site(
                    deployment_id, name=body.name, code=body.code, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return SiteOut.model_validate(site)

    @router.get("/deployments/{deployment_id}/sites", response_model=list[SiteOut])
    async def list_sites(deployment_id: str) -> list[SiteOut]:
        async with get_clinical_session() as s:
            return [
                SiteOut.model_validate(x)
                for x in await ClinicalRepository(s).list_sites(deployment_id)
            ]

    # ── subjects ─────────────────────────────────────────────────────────

    @router.post(
        "/deployments/{deployment_id}/subjects", response_model=SubjectOut, status_code=201
    )
    async def add_subject(deployment_id: str, body: SubjectIn, user: DataEntryUser) -> SubjectOut:
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
    async def list_subjects(deployment_id: str) -> list[SubjectOut]:
        async with get_clinical_session() as s:
            subs = await ClinicalRepository(s).list_subjects(deployment_id)
            return [SubjectOut.model_validate(x) for x in subs]

    @router.post(
        "/subjects/{subject_id}/epro-access", response_model=EproAccessOut, status_code=201
    )
    async def issue_epro_access(subject_id: str, user: DataEntryUser) -> EproAccessOut:
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
    async def list_subject_forms(subject_id: str) -> list[FormInstanceOut]:
        async with get_clinical_session() as s:
            instances = await ClinicalRepository(s).list_form_instances(subject_id)
            return [FormInstanceOut.model_validate(fi) for fi in instances]

    @router.post("/subjects/{subject_id}/forms", response_model=FormInstanceOut, status_code=201)
    async def open_form(subject_id: str, body: OpenFormIn, user: DataEntryUser) -> FormInstanceOut:
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
        form_instance_id: str, body: SubmitDataIn, user: DataEntryUser
    ) -> FormInstanceOut:
        async with get_clinical_session() as s:
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
    async def get_form_instance(form_instance_id: str) -> FormInstanceDetailOut:
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
    async def get_audit(form_instance_id: str) -> list[AuditEntryOut]:
        async with get_clinical_session() as s:
            entries = await ClinicalRepository(s).get_form_instance_audit(form_instance_id)
            return [AuditEntryOut.model_validate(e) for e in entries]

    # ── queries / discrepancies ──────────────────────────────────────────

    @router.get("/form-instances/{form_instance_id}/queries", response_model=list[QueryOut])
    async def list_queries(form_instance_id: str) -> list[QueryOut]:
        async with get_clinical_session() as s:
            qs = await ClinicalRepository(s).list_queries(form_instance_id)
            return [QueryOut.model_validate(q) for q in qs]

    @router.post(
        "/form-instances/{form_instance_id}/queries", response_model=QueryOut, status_code=201
    )
    async def raise_query(
        form_instance_id: str, body: ManualQueryIn, user: DataEntryUser
    ) -> QueryOut:
        async with get_clinical_session() as s:
            try:
                q = await ClinicalRepository(s).create_manual_query(
                    form_instance_id, item_id=body.item_id, text=body.text, actor_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return QueryOut.model_validate(q)

    @router.post("/queries/{query_id}/respond", response_model=QueryOut)
    async def respond_query(query_id: str, body: QueryResponseIn, user: DataEntryUser) -> QueryOut:
        async with get_clinical_session() as s:
            try:
                q = await ClinicalRepository(s).respond_query(
                    query_id, text=body.text, author_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return QueryOut.model_validate(q)

    @router.post("/queries/{query_id}/close", response_model=QueryOut)
    async def close_query(query_id: str, user: DataEntryUser) -> QueryOut:
        async with get_clinical_session() as s:
            try:
                q = await ClinicalRepository(s).close_query(query_id, actor_sub=user.sub)
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return QueryOut.model_validate(q)

    # ── e-signatures + lock (E5) ───────────────────────────────────────────

    @router.post("/form-instances/{form_instance_id}/sign", response_model=SignatureOut)
    async def sign(form_instance_id: str, body: SignIn, user: DataEntryUser) -> SignatureOut:
        async with get_clinical_session() as s:
            try:
                sig = await ClinicalRepository(s).sign_form_instance(
                    form_instance_id, meaning=body.meaning, signer_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return SignatureOut.model_validate(sig)

    @router.post("/form-instances/{form_instance_id}/unlock", response_model=FormInstanceOut)
    async def unlock(form_instance_id: str, body: UnlockIn, admin: AdminUser) -> FormInstanceOut:
        # Unlocking voids a signature — an elevated (admin) action.
        async with get_clinical_session() as s:
            try:
                fi = await ClinicalRepository(s).unlock_form_instance(
                    form_instance_id, reason=body.reason, actor_sub=admin.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return FormInstanceOut.model_validate(fi)

    @router.get("/form-instances/{form_instance_id}/signatures", response_model=list[SignatureOut])
    async def list_signatures(form_instance_id: str) -> list[SignatureOut]:
        async with get_clinical_session() as s:
            sigs = await ClinicalRepository(s).list_signatures(form_instance_id)
            return [SignatureOut.model_validate(x) for x in sigs]

    # ── source-data verification (E6) ───────────────────────────────────────

    @router.post("/form-instances/{form_instance_id}/verify")
    async def verify(form_instance_id: str, body: VerifyIn, user: DataEntryUser) -> dict[str, int]:
        async with get_clinical_session() as s:
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
    async def list_verifications(form_instance_id: str) -> list[VerificationOut]:
        async with get_clinical_session() as s:
            vs = await ClinicalRepository(s).list_verifications(form_instance_id)
            return [VerificationOut.model_validate(v) for v in vs]

    # ── subject-casebook sign-off + lock (E6) ───────────────────────────────

    @router.post("/subjects/{subject_id}/sign", response_model=SubjectSignatureOut)
    async def sign_subject(
        subject_id: str, body: SubjectSignIn, user: DataEntryUser
    ) -> SubjectSignatureOut:
        async with get_clinical_session() as s:
            try:
                sig = await ClinicalRepository(s).sign_subject(
                    subject_id, meaning=body.meaning, signer_sub=user.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return SubjectSignatureOut.model_validate(sig)

    @router.post("/subjects/{subject_id}/unlock", response_model=SubjectOut)
    async def unlock_subject(subject_id: str, body: UnlockIn, admin: AdminUser) -> SubjectOut:
        async with get_clinical_session() as s:
            try:
                subject = await ClinicalRepository(s).unlock_subject(
                    subject_id, reason=body.reason, actor_sub=admin.sub
                )
            except ClinicalError as e:
                raise HTTPException(409, str(e)) from e
            return SubjectOut.model_validate(subject)

    @router.get("/subjects/{subject_id}/signatures", response_model=list[SubjectSignatureOut])
    async def list_subject_signatures(subject_id: str) -> list[SubjectSignatureOut]:
        async with get_clinical_session() as s:
            sigs = await ClinicalRepository(s).list_subject_signatures(subject_id)
            return [SubjectSignatureOut.model_validate(x) for x in sigs]

    return router
