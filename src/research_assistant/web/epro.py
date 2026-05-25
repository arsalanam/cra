"""Participant ePRO API (eCRF E4b).

Token-authenticated (NOT Cognito): a participant opens a magic link
(`/epro.html?token=…`); the token, issued by a site and scoped to one subject,
is the credential. Consent is required before any data entry. All writes are
audited with `source="epro"`. Participants can only touch their own subject's
ePRO-flagged forms.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from ..domain.ecrf import FormDefinition
from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.models import ParticipantAccess
from ..persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
    HardCheckError,
    LockedError,
)

logger = logging.getLogger(__name__)


class SubmitDataIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str | None]
    mark_complete: bool = False


async def _require_participant(repo: ClinicalRepository, token: str) -> ParticipantAccess:
    access = await repo.resolve_participant(token)
    if access is None:
        raise HTTPException(401, "Invalid or revoked access token.")
    return access


def _require_consent(access: ParticipantAccess) -> None:
    if access.consent_at is None:
        raise HTTPException(403, "Consent is required before entering data.")


def create_epro_router() -> APIRouter:
    router = APIRouter(prefix="/epro", tags=["epro"])

    @router.get("/session")
    async def session(token: str) -> dict[str, Any]:
        async with get_clinical_session() as s:
            repo = ClinicalRepository(s)
            access = await _require_participant(repo, token)
            subject = await repo.get_subject(access.subject_id)
            forms = await repo.list_epro_forms(access.deployment_id)
            instances = await repo.list_form_instances(access.subject_id)
            return {
                "subject_code": subject.subject_code if subject else "",
                "consent_required": access.consent_at is None,
                "forms": [{"id": f.id, "title": f.title, "version": f.version} for f in forms],
                "instances": [
                    {"id": fi.id, "deployed_form_id": fi.deployed_form_id, "status": fi.status}
                    for fi in instances
                ],
            }

    @router.post("/consent")
    async def consent(token: str) -> dict[str, Any]:
        async with get_clinical_session() as s:
            repo = ClinicalRepository(s)
            access = await _require_participant(repo, token)
            await repo.record_consent(access)
            return {"consented": True}

    @router.get("/deployed-forms/{deployed_form_id}")
    async def deployed_form(deployed_form_id: str, token: str) -> dict[str, Any]:
        async with get_clinical_session() as s:
            repo = ClinicalRepository(s)
            access = await _require_participant(repo, token)
            df = await repo.get_deployed_form(deployed_form_id)
            if df is None or df.deployment_id != access.deployment_id:
                raise HTTPException(404, "Form not found")
            definition = FormDefinition.model_validate(json.loads(df.definition_json))
            if not definition.epro:
                raise HTTPException(403, "This form is not participant-fillable.")
            return {
                "id": df.id,
                "title": df.title,
                "version": df.version,
                "definition": definition.model_dump(),
            }

    @router.post("/forms")
    async def open_form(token: str, deployed_form_id: str) -> dict[str, Any]:
        async with get_clinical_session() as s:
            repo = ClinicalRepository(s)
            access = await _require_participant(repo, token)
            _require_consent(access)
            df = await repo.get_deployed_form(deployed_form_id)
            if df is None or df.deployment_id != access.deployment_id:
                raise HTTPException(404, "Form not found")
            definition = FormDefinition.model_validate(json.loads(df.definition_json))
            if not definition.epro:
                raise HTTPException(403, "This form is not participant-fillable.")
            fi = await repo.open_form_instance(
                access.subject_id,
                deployed_form_id=deployed_form_id,
                actor_sub=f"participant:{access.subject_id}",
            )
            return {"id": fi.id, "deployed_form_id": fi.deployed_form_id, "status": fi.status}

    async def _owned_instance(repo: ClinicalRepository, access: ParticipantAccess, fi_id: str):  # type: ignore[no-untyped-def]
        found = await repo.get_form_instance(fi_id)
        if found is None or found[0].subject_id != access.subject_id:
            raise HTTPException(404, "Form instance not found")
        return found

    @router.get("/form-instances/{form_instance_id}")
    async def get_instance(form_instance_id: str, token: str) -> dict[str, Any]:
        async with get_clinical_session() as s:
            repo = ClinicalRepository(s)
            access = await _require_participant(repo, token)
            fi, items = await _owned_instance(repo, access, form_instance_id)
            return {
                "id": fi.id,
                "status": fi.status,
                "items": [{"item_id": i.item_id, "value": i.value} for i in items],
            }

    @router.put("/form-instances/{form_instance_id}/data")
    async def submit(form_instance_id: str, token: str, body: SubmitDataIn) -> dict[str, Any]:
        async with get_clinical_session() as s:
            repo = ClinicalRepository(s)
            access = await _require_participant(repo, token)
            _require_consent(access)
            await _owned_instance(repo, access, form_instance_id)
            try:
                fi = await repo.submit_item_data(
                    form_instance_id,
                    body.values,
                    actor_sub=f"participant:{access.subject_id}",
                    mark_complete=body.mark_complete,
                    source="epro",
                )
            except HardCheckError as e:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Some answers need fixing.",
                        "failures": [
                            {"item_id": f.item_id, "message": f.message} for f in e.failures
                        ],
                    },
                ) from e
            except LockedError as e:
                raise HTTPException(409, str(e)) from e
            except ClinicalError as e:
                raise HTTPException(404, str(e)) from e
            return {"id": fi.id, "status": fi.status}

    return router
