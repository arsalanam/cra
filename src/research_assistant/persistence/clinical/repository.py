"""Capture repository for the clinical-data store (eCRF E1).

Every data-affecting method routes through `_audit(...)`, which appends an
`AuditEntry` in the same transaction as the change — so no write path can
bypass the audit trail (ALCOA+ / Part 11). The trail is insert-only here;
DB-level immutability (triggers / revoked grants) is an E6 hardening step.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.ecrf import FormDefinition
from ...ecrf.edit_checks import CheckResult, evaluate_form, required_blank_items
from .models import (
    AuditEntry,
    DeployedForm,
    EventInstance,
    FormInstance,
    ItemData,
    ParticipantAccess,
    Query,
    QueryResponse,
    Signature,
    Site,
    StudyDeployment,
    Subject,
)

logger = logging.getLogger(__name__)


class ClinicalError(Exception):
    """Invalid capture operation (e.g. unknown subject, cross-deployment site)."""


class LockedError(ClinicalError):
    """Edit attempted on a signed/locked form instance — unlock it first."""


class HardCheckError(Exception):
    """One or more HARD edit checks failed — the submit is rejected atomically."""

    def __init__(self, failures: list[CheckResult]) -> None:
        self.failures = failures
        super().__init__(f"{len(failures)} hard edit-check(s) failed")


@dataclass(frozen=True)
class FormSnapshot:
    """A published research form, ready to snapshot into the clinical store."""

    form_def_id: str
    form_name: str
    version: int
    title: str
    definition_json: str


class ClinicalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── audit-writer seam ───────────────────────────────────────────────────

    def _audit(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        actor_sub: str | None,
        actor_role: str | None = None,
        form_instance_id: str | None = None,
        item_id: str | None = None,
        old_value: str | None = None,
        new_value: str | None = None,
        reason: str | None = None,
        source: str = "edc",
    ) -> None:
        self._s.add(
            AuditEntry(
                entity_type=entity_type,
                entity_id=entity_id,
                form_instance_id=form_instance_id,
                action=action,
                item_id=item_id,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
                actor_sub=actor_sub,
                actor_role=actor_role,
                source=source,
            )
        )

    # ── deployment / sites / subjects ────────────────────────────────────────

    async def deploy_study(
        self,
        *,
        research_study_id: str,
        name: str,
        forms: Sequence[FormSnapshot],
        actor_sub: str | None = None,
    ) -> StudyDeployment:
        deployment = StudyDeployment(
            research_study_id=research_study_id, name=name, created_by=actor_sub
        )
        self._s.add(deployment)
        await self._s.flush()
        self._audit(
            entity_type="study_deployment",
            entity_id=deployment.id,
            action="deploy",
            actor_sub=actor_sub,
        )
        for f in forms:
            self._s.add(
                DeployedForm(
                    deployment_id=deployment.id,
                    form_def_id=f.form_def_id,
                    form_name=f.form_name,
                    version=f.version,
                    title=f.title,
                    definition_json=f.definition_json,
                )
            )
        await self._s.flush()
        return deployment

    async def get_deployment(self, deployment_id: str) -> StudyDeployment | None:
        return await self._s.get(StudyDeployment, deployment_id)

    async def list_deployments(self) -> list[StudyDeployment]:
        rows = await self._s.scalars(
            select(StudyDeployment).order_by(StudyDeployment.created_at.desc())
        )
        return list(rows.all())

    async def list_deployed_forms(self, deployment_id: str) -> list[DeployedForm]:
        rows = await self._s.scalars(
            select(DeployedForm).where(DeployedForm.deployment_id == deployment_id)
        )
        return list(rows.all())

    async def get_deployed_form(self, deployed_form_id: str) -> DeployedForm | None:
        return await self._s.get(DeployedForm, deployed_form_id)

    async def add_site(
        self,
        deployment_id: str,
        *,
        name: str,
        code: str | None = None,
        actor_sub: str | None = None,
    ) -> Site:
        if await self._s.get(StudyDeployment, deployment_id) is None:
            raise ClinicalError(f"Deployment {deployment_id!r} not found")
        site = Site(deployment_id=deployment_id, name=name, code=code)
        self._s.add(site)
        await self._s.flush()
        self._audit(entity_type="site", entity_id=site.id, action="create", actor_sub=actor_sub)
        return site

    async def list_sites(self, deployment_id: str) -> list[Site]:
        rows = await self._s.scalars(select(Site).where(Site.deployment_id == deployment_id))
        return list(rows.all())

    async def add_subject(
        self,
        deployment_id: str,
        *,
        site_id: str,
        subject_code: str,
        actor_sub: str | None = None,
    ) -> Subject:
        site = await self._s.get(Site, site_id)
        if site is None or site.deployment_id != deployment_id:
            raise ClinicalError("Site not found in this deployment")
        subject = Subject(
            deployment_id=deployment_id,
            site_id=site_id,
            subject_code=subject_code,
            created_by=actor_sub,
        )
        self._s.add(subject)
        await self._s.flush()
        self._audit(
            entity_type="subject", entity_id=subject.id, action="create", actor_sub=actor_sub
        )
        return subject

    async def list_subjects(self, deployment_id: str) -> list[Subject]:
        rows = await self._s.scalars(select(Subject).where(Subject.deployment_id == deployment_id))
        return list(rows.all())

    async def get_subject(self, subject_id: str) -> Subject | None:
        return await self._s.get(Subject, subject_id)

    # ── participant ePRO access (E4b) ────────────────────────────────────────

    @staticmethod
    def _hash_token(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    async def issue_participant_access(
        self, subject_id: str, *, actor_sub: str | None = None
    ) -> tuple[ParticipantAccess, str]:
        """Create a magic-link token for a subject. Returns (row, RAW token).

        The raw token is shown once (to build the participant link); only its
        hash is stored.
        """
        subject = await self._s.get(Subject, subject_id)
        if subject is None:
            raise ClinicalError(f"Subject {subject_id!r} not found")
        raw = secrets.token_urlsafe(32)
        access = ParticipantAccess(
            subject_id=subject_id,
            deployment_id=subject.deployment_id,
            token_hash=self._hash_token(raw),
            created_by=actor_sub,
        )
        self._s.add(access)
        await self._s.flush()
        self._audit(
            entity_type="participant_access",
            entity_id=access.id,
            action="issue",
            actor_sub=actor_sub,
        )
        return access, raw

    async def resolve_participant(self, raw_token: str) -> ParticipantAccess | None:
        """Return the active access for a raw token, or None."""
        if not raw_token:
            return None
        return (
            await self._s.scalars(
                select(ParticipantAccess).where(
                    ParticipantAccess.token_hash == self._hash_token(raw_token),
                    ParticipantAccess.status == "active",
                )
            )
        ).first()

    async def record_consent(self, access: ParticipantAccess) -> ParticipantAccess:
        if access.consent_at is None:
            access.consent_at = datetime.now(UTC)
            await self._s.flush()
            self._audit(
                entity_type="participant_access",
                entity_id=access.id,
                action="consent",
                actor_sub=f"participant:{access.subject_id}",
                source="epro",
            )
        return access

    async def list_epro_forms(self, deployment_id: str) -> list[DeployedForm]:
        """Deployed forms flagged epro=true in their snapshot definition."""
        forms = await self.list_deployed_forms(deployment_id)
        out: list[DeployedForm] = []
        for f in forms:
            try:
                if FormDefinition.model_validate(json.loads(f.definition_json)).epro:
                    out.append(f)
            except Exception:
                logger.warning("Could not parse deployed form %s for ePRO filter", f.id)
        return out

    # ── events / form instances / data ───────────────────────────────────────

    async def open_event(
        self, subject_id: str, *, event_id: str, name: str, actor_sub: str | None = None
    ) -> EventInstance:
        if await self._s.get(Subject, subject_id) is None:
            raise ClinicalError(f"Subject {subject_id!r} not found")
        event = EventInstance(subject_id=subject_id, event_id=event_id, name=name)
        self._s.add(event)
        await self._s.flush()
        self._audit(
            entity_type="event_instance", entity_id=event.id, action="create", actor_sub=actor_sub
        )
        return event

    async def open_form_instance(
        self,
        subject_id: str,
        *,
        deployed_form_id: str,
        event_instance_id: str | None = None,
        actor_sub: str | None = None,
    ) -> FormInstance:
        if await self._s.get(Subject, subject_id) is None:
            raise ClinicalError(f"Subject {subject_id!r} not found")
        if await self._s.get(DeployedForm, deployed_form_id) is None:
            raise ClinicalError(f"Deployed form {deployed_form_id!r} not found")
        fi = FormInstance(
            subject_id=subject_id,
            deployed_form_id=deployed_form_id,
            event_instance_id=event_instance_id,
            status="blank",
            created_by=actor_sub,
        )
        self._s.add(fi)
        await self._s.flush()
        self._audit(
            entity_type="form_instance",
            entity_id=fi.id,
            form_instance_id=fi.id,
            action="create",
            actor_sub=actor_sub,
        )
        return fi

    async def _load_definition(self, fi: FormInstance) -> FormDefinition | None:
        """Parse the snapshotted form definition for a form instance."""
        df = await self._s.get(DeployedForm, fi.deployed_form_id)
        if df is None:
            return None
        try:
            return FormDefinition.model_validate(json.loads(df.definition_json))
        except Exception:
            logger.warning("Could not parse deployed form %s", fi.deployed_form_id, exc_info=True)
            return None

    async def submit_item_data(
        self,
        form_instance_id: str,
        values: dict[str, str | None],
        *,
        actor_sub: str | None = None,
        reason: str | None = None,
        mark_complete: bool = False,
        source: str = "edc",
    ) -> FormInstance:
        """Create/update item values, auditing each change; update form status.

        Runs the form's edit checks against the merged values first: any HARD
        failure (or a required item left blank when `mark_complete`) raises
        `HardCheckError` and NOTHING is persisted. SOFT failures persist the
        value and open/auto-close auto-queries. A `reason` is recorded on
        updates (required after a value's first commit per Part 11).
        """
        fi = await self._s.get(FormInstance, form_instance_id)
        if fi is None:
            raise ClinicalError(f"Form instance {form_instance_id!r} not found")
        if fi.status in ("signed", "locked"):
            raise LockedError(f"Form instance is {fi.status}; unlock it before editing")

        existing = {
            d.item_id: d
            for d in (
                await self._s.scalars(
                    select(ItemData).where(ItemData.form_instance_id == form_instance_id)
                )
            ).all()
        }

        # Edit-check gate — evaluate against the MERGED value set before
        # persisting anything (atomic reject on hard failure).
        merged: dict[str, str | None] = {iid: d.value for iid, d in existing.items()}
        merged.update(values)
        definition = await self._load_definition(fi)
        results = evaluate_form(definition, merged) if definition is not None else []
        hard_failures = [r for r in results if not r.passed and r.severity == "hard"]
        if mark_complete and definition is not None:
            hard_failures += [
                CheckResult(
                    item_id=iid,
                    check_id="required",
                    severity="hard",
                    passed=False,
                    message="This field is required to complete the form.",
                )
                for iid in required_blank_items(definition, merged)
            ]
        if hard_failures:
            raise HardCheckError(hard_failures)

        changed = False
        for item_id, value in values.items():
            current = existing.get(item_id)
            if current is None:
                self._s.add(
                    ItemData(
                        form_instance_id=form_instance_id,
                        item_id=item_id,
                        value=value,
                        entered_by=actor_sub,
                    )
                )
                self._audit(
                    entity_type="item_data",
                    entity_id=f"{form_instance_id}:{item_id}",
                    form_instance_id=form_instance_id,
                    action="create",
                    item_id=item_id,
                    new_value=value,
                    actor_sub=actor_sub,
                    source=source,
                )
                changed = True
            elif current.value != value:
                old = current.value
                current.value = value
                self._audit(
                    entity_type="item_data",
                    entity_id=current.id,
                    form_instance_id=form_instance_id,
                    action="update",
                    item_id=item_id,
                    old_value=old,
                    new_value=value,
                    reason=reason,
                    actor_sub=actor_sub,
                    source=source,
                )
                changed = True

        new_status = (
            "complete" if mark_complete else ("in_progress" if changed or existing else fi.status)
        )
        if new_status != fi.status:
            old_status = fi.status
            fi.status = new_status
            self._audit(
                entity_type="form_instance",
                entity_id=fi.id,
                form_instance_id=fi.id,
                action="status",
                old_value=old_status,
                new_value=new_status,
                actor_sub=actor_sub,
                source=source,
            )

        await self._reconcile_auto_queries(
            fi, [r for r in results if r.severity == "soft"], actor_sub
        )
        await self._s.flush()
        return fi

    # ── queries / discrepancies ──────────────────────────────────────────────

    async def _reconcile_auto_queries(
        self, fi: FormInstance, soft_results: list[CheckResult], actor_sub: str | None
    ) -> None:
        """Open an auto-query for each failing soft check; close ones that now pass."""
        open_autos = {
            (q.item_id, q.check_id): q
            for q in (
                await self._s.scalars(
                    select(Query).where(
                        Query.form_instance_id == fi.id,
                        Query.query_type == "auto",
                        Query.status != "closed",
                    )
                )
            ).all()
        }
        for r in soft_results:
            key = (r.item_id, r.check_id)
            if not r.passed and key not in open_autos:
                q = Query(
                    form_instance_id=fi.id,
                    subject_id=fi.subject_id,
                    item_id=r.item_id,
                    check_id=r.check_id,
                    query_type="auto",
                    severity="soft",
                    status="open",
                    text=r.message,
                )
                self._s.add(q)
                await self._s.flush()
                self._audit(
                    entity_type="query",
                    entity_id=q.id,
                    form_instance_id=fi.id,
                    action="open",
                    item_id=r.item_id,
                    new_value=r.message,
                    actor_sub=actor_sub,
                    source="system",
                )
            elif r.passed and key in open_autos:
                q = open_autos[key]
                q.status = "closed"
                self._s.add(
                    QueryResponse(query_id=q.id, text="Auto-resolved: edit check now passes.")
                )
                self._audit(
                    entity_type="query",
                    entity_id=q.id,
                    form_instance_id=fi.id,
                    action="close",
                    item_id=r.item_id,
                    actor_sub=actor_sub,
                    source="system",
                )

    async def create_manual_query(
        self, form_instance_id: str, *, item_id: str, text: str, actor_sub: str | None = None
    ) -> Query:
        fi = await self._s.get(FormInstance, form_instance_id)
        if fi is None:
            raise ClinicalError(f"Form instance {form_instance_id!r} not found")
        q = Query(
            form_instance_id=form_instance_id,
            subject_id=fi.subject_id,
            item_id=item_id,
            query_type="manual",
            status="open",
            text=text,
            created_by=actor_sub,
        )
        self._s.add(q)
        await self._s.flush()
        self._audit(
            entity_type="query",
            entity_id=q.id,
            form_instance_id=form_instance_id,
            action="open",
            item_id=item_id,
            new_value=text,
            actor_sub=actor_sub,
        )
        return q

    async def respond_query(
        self, query_id: str, *, text: str, author_sub: str | None = None
    ) -> Query:
        q = await self._s.get(Query, query_id)
        if q is None:
            raise ClinicalError(f"Query {query_id!r} not found")
        self._s.add(QueryResponse(query_id=query_id, text=text, author_sub=author_sub))
        q.status = "answered"
        await self._s.flush()
        self._audit(
            entity_type="query",
            entity_id=q.id,
            form_instance_id=q.form_instance_id,
            action="respond",
            item_id=q.item_id,
            new_value=text,
            actor_sub=author_sub,
        )
        return q

    async def close_query(self, query_id: str, *, actor_sub: str | None = None) -> Query:
        q = await self._s.get(Query, query_id)
        if q is None:
            raise ClinicalError(f"Query {query_id!r} not found")
        q.status = "closed"
        await self._s.flush()
        self._audit(
            entity_type="query",
            entity_id=q.id,
            form_instance_id=q.form_instance_id,
            action="close",
            item_id=q.item_id,
            actor_sub=actor_sub,
        )
        return q

    async def list_queries(self, form_instance_id: str) -> list[Query]:
        rows = await self._s.scalars(
            select(Query)
            .where(Query.form_instance_id == form_instance_id)
            .order_by(Query.created_at)
        )
        return list(rows.all())

    # ── e-signatures + lock (E5) ─────────────────────────────────────────────

    async def _content_hash(self, form_instance_id: str) -> str:
        items = (
            await self._s.scalars(
                select(ItemData).where(ItemData.form_instance_id == form_instance_id)
            )
        ).all()
        payload = json.dumps(sorted((i.item_id, i.value) for i in items), ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    async def sign_form_instance(
        self, form_instance_id: str, *, meaning: str, signer_sub: str | None = None
    ) -> Signature:
        """Sign a COMPLETE form instance, binding the signature to its data, and
        lock it (status -> signed). Errors unless the form is complete."""
        fi = await self._s.get(FormInstance, form_instance_id)
        if fi is None:
            raise ClinicalError(f"Form instance {form_instance_id!r} not found")
        if fi.status != "complete":
            raise ClinicalError(f"Form must be complete to sign (it is {fi.status})")
        sig = Signature(
            form_instance_id=form_instance_id,
            signer_sub=signer_sub,
            meaning=meaning,
            content_hash=await self._content_hash(form_instance_id),
        )
        self._s.add(sig)
        fi.status = "signed"
        await self._s.flush()
        self._audit(
            entity_type="form_instance",
            entity_id=fi.id,
            form_instance_id=fi.id,
            action="sign",
            old_value="complete",
            new_value="signed",
            reason=meaning,
            actor_sub=signer_sub,
        )
        return sig

    async def unlock_form_instance(
        self, form_instance_id: str, *, reason: str, actor_sub: str | None = None
    ) -> FormInstance:
        """Void the signature(s) and reopen a signed form for editing (audited)."""
        fi = await self._s.get(FormInstance, form_instance_id)
        if fi is None:
            raise ClinicalError(f"Form instance {form_instance_id!r} not found")
        if fi.status not in ("signed", "locked"):
            raise ClinicalError(f"Form instance is {fi.status}, not locked")
        sigs = (
            await self._s.scalars(
                select(Signature).where(
                    Signature.form_instance_id == form_instance_id,
                    Signature.voided == False,  # noqa: E712
                )
            )
        ).all()
        now = datetime.now(UTC)
        for sig in sigs:
            sig.voided = True
            sig.voided_at = now
        fi.status = "in_progress"
        await self._s.flush()
        self._audit(
            entity_type="form_instance",
            entity_id=fi.id,
            form_instance_id=fi.id,
            action="unlock",
            old_value="signed",
            new_value="in_progress",
            reason=reason,
            actor_sub=actor_sub,
        )
        return fi

    async def list_signatures(self, form_instance_id: str) -> list[Signature]:
        rows = await self._s.scalars(
            select(Signature)
            .where(Signature.form_instance_id == form_instance_id)
            .order_by(Signature.signed_at)
        )
        return list(rows.all())

    async def list_form_instances(self, subject_id: str) -> list[FormInstance]:
        rows = await self._s.scalars(
            select(FormInstance)
            .where(FormInstance.subject_id == subject_id)
            .order_by(FormInstance.created_at)
        )
        return list(rows.all())

    async def get_form_instance(
        self, form_instance_id: str
    ) -> tuple[FormInstance, list[ItemData]] | None:
        fi = await self._s.get(FormInstance, form_instance_id)
        if fi is None:
            return None
        items = list(
            (
                await self._s.scalars(
                    select(ItemData)
                    .where(ItemData.form_instance_id == form_instance_id)
                    .order_by(ItemData.item_id)
                )
            ).all()
        )
        return fi, items

    async def get_form_instance_audit(self, form_instance_id: str) -> list[AuditEntry]:
        rows = await self._s.scalars(
            select(AuditEntry)
            .where(AuditEntry.form_instance_id == form_instance_id)
            .order_by(AuditEntry.created_at, AuditEntry.id)
        )
        return list(rows.all())
