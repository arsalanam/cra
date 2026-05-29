"""eCRF authoring API (E0) — studies + versioned form definitions.

The control-plane surface the future eCRF-Renderer UI consumes. Reads
require an authenticated session (applied at app level); writes are
gated per the permission matrix in `rbac-design.md` §4.5 — see comments
on each handler for the specific permission.

No subject data here — this is form *metadata* only.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from ..agent.specialists import ecrf_design
from ..auth import SessionPayload
from ..auth.rbac import Permission
from ..domain.ecrf import FormDefinition, StudyDraft, VisitSchedule
from ..ecrf import form_to_odm_xml
from ..persistence.database import get_db_session
from ..persistence.ecrf_repository import EcrfError, EcrfRepository
from ..persistence.models import EcrfFormDefinition
from .auth import CurrentUser
from .authz import require_permission_scoped

logger = logging.getLogger(__name__)


# ── DTOs ──────────────────────────────────────────────────────────────────────


class StudyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    protocol_id: str | None = None
    description: str | None = None


class StudyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    protocol_id: str | None = None
    description: str | None = None
    status: str | None = None


class StudyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    protocol_id: str | None
    description: str | None
    status: str
    created_at: datetime


class FormOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    study_id: str
    name: str
    version: int
    status: str
    title: str
    created_at: datetime
    published_at: datetime | None


class FormDetailOut(FormOut):
    definition: FormDefinition


def _form_detail(form: EcrfFormDefinition) -> FormDetailOut:
    return FormDetailOut(
        **FormOut.model_validate(form).model_dump(),
        definition=EcrfRepository.parse_definition(form),
    )


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_text: str
    instructions: str | None = None


def create_ecrf_router() -> APIRouter:
    router = APIRouter(prefix="/ecrf", tags=["ecrf"])

    # ── AI draft-from-protocol (E3) ────────────────────────────────────────
    # skill.ecrf_design (global) — same gate as the dispatcher uses for the
    # ecrf_design specialist; admins + study_designers only.

    @router.post("/draft", response_model=StudyDraft)
    async def draft_forms(
        body: DraftIn,
        user: SessionPayload = require_permission_scoped(Permission.SKILL_ECRF_DESIGN),
    ) -> StudyDraft:
        """Draft a study's CRFs from protocol text (review-only; saves nothing)."""
        if not body.protocol_text.strip():
            raise HTTPException(422, "protocol_text is required")
        draft, _meta = await ecrf_design.draft_from_protocol(body.protocol_text, body.instructions)
        return draft

    # ── studies ──────────────────────────────────────────────────────────
    # Reads stay open to any authenticated user (the app-level current_user
    # is still enforced by `/api` mounting). Writes require study-author
    # rights at the relevant study; creation is global.

    @router.post("/studies", response_model=StudyOut, status_code=201)
    async def create_study(
        body: StudyIn,
        user: SessionPayload = require_permission_scoped(Permission.STUDY_CREATE),
    ) -> StudyOut:
        async with get_db_session() as session:
            study = await EcrfRepository(session).create_study(
                name=body.name,
                protocol_id=body.protocol_id,
                description=body.description,
                created_by=user.sub,
            )
            return StudyOut.model_validate(study)

    @router.get("/studies", response_model=list[StudyOut])
    async def list_studies(user: CurrentUser) -> list[StudyOut]:
        async with get_db_session() as session:
            return [
                StudyOut.model_validate(s) for s in await EcrfRepository(session).list_studies()
            ]

    @router.get("/studies/{study_id}", response_model=StudyOut)
    async def get_study(study_id: str, user: CurrentUser) -> StudyOut:
        async with get_db_session() as session:
            study = await EcrfRepository(session).get_study(study_id)
            if study is None:
                raise HTTPException(404, "Study not found")
            return StudyOut.model_validate(study)

    @router.put("/studies/{study_id}", response_model=StudyOut)
    async def update_study(
        study_id: str,
        body: StudyUpdate,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_AUTHOR, resource_param="study_id"
        ),
    ) -> StudyOut:
        async with get_db_session() as session:
            try:
                study = await EcrfRepository(session).update_study(
                    study_id,
                    name=body.name,
                    protocol_id=body.protocol_id,
                    description=body.description,
                    status=body.status,
                )
            except EcrfError as e:
                raise HTTPException(404, str(e)) from e
            return StudyOut.model_validate(study)

    @router.put("/studies/{study_id}/schedule", response_model=StudyOut)
    async def set_schedule(
        study_id: str,
        body: VisitSchedule,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_AUTHOR, resource_param="study_id"
        ),
    ) -> StudyOut:
        async with get_db_session() as session:
            try:
                study = await EcrfRepository(session).update_study(study_id, schedule=body)
            except EcrfError as e:
                raise HTTPException(404, str(e)) from e
            return StudyOut.model_validate(study)

    # ── form definitions ──────────────────────────────────────────────────

    @router.post("/studies/{study_id}/forms", response_model=FormOut, status_code=201)
    async def create_form(
        study_id: str,
        body: FormDefinition,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_AUTHOR, resource_param="study_id"
        ),
    ) -> FormOut:
        async with get_db_session() as session:
            try:
                form = await EcrfRepository(session).create_form(
                    study_id, body, created_by=user.sub
                )
            except EcrfError as e:
                raise HTTPException(409, str(e)) from e
            return FormOut.model_validate(form)

    @router.get("/studies/{study_id}/forms", response_model=list[FormOut])
    async def list_forms(study_id: str, user: CurrentUser) -> list[FormOut]:
        async with get_db_session() as session:
            forms = await EcrfRepository(session).list_forms(study_id)
            return [FormOut.model_validate(f) for f in forms]

    @router.get("/forms/{form_id}", response_model=FormDetailOut)
    async def get_form(form_id: str, user: CurrentUser) -> FormDetailOut:
        async with get_db_session() as session:
            form = await EcrfRepository(session).get_form(form_id)
            if form is None:
                raise HTTPException(404, "Form not found")
            return _form_detail(form)

    @router.put("/forms/{form_id}", response_model=FormOut)
    async def update_form(
        form_id: str,
        body: FormDefinition,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_AUTHOR, resource_param="form_id"
        ),
    ) -> FormOut:
        async with get_db_session() as session:
            try:
                form = await EcrfRepository(session).update_form_draft(form_id, body)
            except EcrfError as e:
                raise HTTPException(409, str(e)) from e
            return FormOut.model_validate(form)

    @router.post("/forms/{form_id}/publish", response_model=FormOut)
    async def publish_form(
        form_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_PUBLISH, resource_param="form_id"
        ),
    ) -> FormOut:
        async with get_db_session() as session:
            try:
                form = await EcrfRepository(session).publish_form(form_id)
            except EcrfError as e:
                raise HTTPException(409, str(e)) from e
            return FormOut.model_validate(form)

    @router.post("/forms/{form_id}/new-version", response_model=FormOut, status_code=201)
    async def new_version(
        form_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.STUDY_AUTHOR, resource_param="form_id"
        ),
    ) -> FormOut:
        async with get_db_session() as session:
            try:
                form = await EcrfRepository(session).new_version(form_id, created_by=user.sub)
            except EcrfError as e:
                raise HTTPException(404, str(e)) from e
            return FormOut.model_validate(form)

    @router.get("/forms/{form_id}/export.odm.xml")
    async def export_odm(form_id: str) -> Response:
        async with get_db_session() as session:
            repo = EcrfRepository(session)
            form = await repo.get_form(form_id)
            if form is None:
                raise HTTPException(404, "Form not found")
            study = await repo.get_study(form.study_id)
            definition = EcrfRepository.parse_definition(form)
            xml = form_to_odm_xml(
                definition,
                study_name=study.name if study else form.name,
                version=form.version,
                study_oid=f"S.{form.study_id}",
                protocol_id=study.protocol_id if study else None,
            )
        filename = f"{form.name}_v{form.version}.odm.xml"
        return Response(
            content=xml,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
