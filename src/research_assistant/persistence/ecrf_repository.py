"""Repository for eCRF studies + versioned form definitions (E0).

Encapsulates the publish/version lifecycle so the immutability rule lives in
one place: a published `EcrfFormDefinition` is never mutated — amendments
create a new version, and publishing supersedes the prior published version
of the same (study, name).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.ecrf import FormDefinition, VisitSchedule
from .models import EcrfFormDefinition, EcrfStudy


class EcrfError(Exception):
    """Raised on invalid lifecycle operations (e.g. editing a published form)."""


class EcrfRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── studies ───────────────────────────────────────────────────────────

    async def create_study(
        self,
        *,
        name: str,
        protocol_id: str | None = None,
        description: str | None = None,
        created_by: str | None = None,
    ) -> EcrfStudy:
        study = EcrfStudy(
            name=name,
            protocol_id=protocol_id,
            description=description,
            created_by=created_by,
        )
        self._s.add(study)
        await self._s.flush()
        return study

    async def get_study(self, study_id: str) -> EcrfStudy | None:
        return await self._s.get(EcrfStudy, study_id)

    async def list_studies(self) -> list[EcrfStudy]:
        rows = await self._s.scalars(select(EcrfStudy).order_by(EcrfStudy.created_at.desc()))
        return list(rows.all())

    async def update_study(
        self,
        study_id: str,
        *,
        name: str | None = None,
        protocol_id: str | None = None,
        description: str | None = None,
        status: str | None = None,
        schedule: VisitSchedule | None = None,
    ) -> EcrfStudy:
        study = await self._s.get(EcrfStudy, study_id)
        if study is None:
            raise EcrfError(f"Study {study_id!r} not found")
        if name is not None:
            study.name = name
        if protocol_id is not None:
            study.protocol_id = protocol_id
        if description is not None:
            study.description = description
        if status is not None:
            study.status = status
        if schedule is not None:
            study.schedule_json = schedule.model_dump_json()
        await self._s.flush()
        return study

    # ── form definitions ───────────────────────────────────────────────────

    async def _max_version(self, study_id: str, name: str) -> int:
        result = await self._s.scalar(
            select(func.max(EcrfFormDefinition.version)).where(
                EcrfFormDefinition.study_id == study_id,
                EcrfFormDefinition.name == name,
            )
        )
        return int(result or 0)

    async def create_form(
        self, study_id: str, definition: FormDefinition, *, created_by: str | None = None
    ) -> EcrfFormDefinition:
        """Create v1 draft of a new form. Errors if the form name already exists."""
        if await self.get_study(study_id) is None:
            raise EcrfError(f"Study {study_id!r} not found")
        if await self._max_version(study_id, definition.name) > 0:
            raise EcrfError(
                f"Form {definition.name!r} already exists in this study — "
                "use new_version to amend it."
            )
        form = EcrfFormDefinition(
            study_id=study_id,
            name=definition.name,
            version=1,
            status="draft",
            title=definition.title,
            definition_json=definition.model_dump_json(),
            created_by=created_by,
        )
        self._s.add(form)
        await self._s.flush()
        return form

    async def update_form_draft(
        self, form_id: str, definition: FormDefinition
    ) -> EcrfFormDefinition:
        """Replace a draft's definition. Errors if the form is not a draft."""
        form = await self._s.get(EcrfFormDefinition, form_id)
        if form is None:
            raise EcrfError(f"Form {form_id!r} not found")
        if form.status != "draft":
            raise EcrfError(
                f"Form {form_id!r} is {form.status}, not editable — create a new version"
            )
        if definition.name != form.name:
            raise EcrfError("Cannot rename a form across versions")
        form.title = definition.title
        form.definition_json = definition.model_dump_json()
        await self._s.flush()
        return form

    async def publish_form(self, form_id: str) -> EcrfFormDefinition:
        """Publish a draft (immutable thereafter); supersede the prior published version."""
        form = await self._s.get(EcrfFormDefinition, form_id)
        if form is None:
            raise EcrfError(f"Form {form_id!r} not found")
        if form.status != "draft":
            raise EcrfError(f"Form {form_id!r} is already {form.status}")
        # Supersede any currently-published version of the same form.
        prior = await self._s.scalars(
            select(EcrfFormDefinition).where(
                EcrfFormDefinition.study_id == form.study_id,
                EcrfFormDefinition.name == form.name,
                EcrfFormDefinition.status == "published",
            )
        )
        for p in prior.all():
            p.status = "superseded"
        form.status = "published"
        form.published_at = datetime.now(UTC)
        await self._s.flush()
        return form

    async def new_version(
        self, form_id: str, *, created_by: str | None = None
    ) -> EcrfFormDefinition:
        """Clone a form into a fresh draft at the next version (for amendments)."""
        source = await self._s.get(EcrfFormDefinition, form_id)
        if source is None:
            raise EcrfError(f"Form {form_id!r} not found")
        next_version = await self._max_version(source.study_id, source.name) + 1
        draft = EcrfFormDefinition(
            study_id=source.study_id,
            name=source.name,
            version=next_version,
            status="draft",
            title=source.title,
            definition_json=source.definition_json,
            created_by=created_by,
        )
        self._s.add(draft)
        await self._s.flush()
        return draft

    async def get_form(self, form_id: str) -> EcrfFormDefinition | None:
        return await self._s.get(EcrfFormDefinition, form_id)

    async def list_forms(self, study_id: str) -> list[EcrfFormDefinition]:
        rows = await self._s.scalars(
            select(EcrfFormDefinition)
            .where(EcrfFormDefinition.study_id == study_id)
            .order_by(EcrfFormDefinition.name, EcrfFormDefinition.version)
        )
        return list(rows.all())

    @staticmethod
    def parse_definition(form: EcrfFormDefinition) -> FormDefinition:
        """Deserialise a row's stored definition back into the validated model."""
        return FormDefinition.model_validate(json.loads(form.definition_json))
