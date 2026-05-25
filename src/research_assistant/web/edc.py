"""EDC capture API (eCRF E1) — the collection plane.

Distinct from the `/api/ecrf/*` authoring API: this namespace deploys a
published study for data collection and captures subject data into the
separate clinical-data (PHI) store, with a full audit trail. Writes require
the `data_entry`/`admin` role; the future Data Collector UI (E4) consumes
this API (it never touches the database directly — D5).
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.repository import ClinicalError, ClinicalRepository, FormSnapshot
from ..persistence.database import get_db_session
from ..persistence.ecrf_repository import EcrfRepository
from .auth import DataEntryUser

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

    # ── form instances + data ────────────────────────────────────────────

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

    return router
