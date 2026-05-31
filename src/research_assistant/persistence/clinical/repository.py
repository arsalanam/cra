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
import uuid as _uuid_mod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.ecrf import FormDefinition
from ...ecrf.edit_checks import CheckResult, evaluate_form, required_blank_items
from .models import (
    AdverseEvent,
    Allocation,
    AuditEntry,
    CapaAction,
    CodeBreakEvent,
    DeployedForm,
    DrugDispensation,
    DrugReceipt,
    DrugReturn,
    EventInstance,
    ExtractionFill,
    ExtractionMapping,
    FormInstance,
    InvestigationalProduct,
    ItemData,
    ParticipantAccess,
    ParticipantContact,
    PlannedVisit,
    ProtocolDeviation,
    Query,
    QueryResponse,
    RandomizationSchedule,
    ScheduledVisit,
    ScreeningLog,
    SentReminder,
    Signature,
    Site,
    SourceDocument,
    SourceRow,
    StudyDeployment,
    StudyLock,
    Subject,
    SubjectSignature,
    Verification,
    VisitSchedule,
)
from .recruitment_terminology import (
    CONSENT_STATUSES,
    CONSORT_EXCLUSION_REASONS,
    ELIGIBILITY_STATUSES,
    ENROLMENT_STATUSES,
)
from .safety_rules import (
    SeriousReason,
    auto_classify_serious,
    compute_reporting_deadline,
)

logger = logging.getLogger(__name__)


class ClinicalError(Exception):
    """Invalid capture operation (e.g. unknown subject, cross-deployment site)."""


class LockedError(ClinicalError):
    """Edit attempted on a signed/locked form instance — unlock it first."""


class StudyLockedError(ClinicalError):
    """Operation attempted while the deployment-wide study database is
    locked (eCRF E7 — validation pack). All writes/signs/SDV are refused
    until a `data_manager` unlocks the study."""


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

    # ── source-data verification (E6) ────────────────────────────────────────

    async def verify_items(
        self, form_instance_id: str, item_ids: Sequence[str], *, verifier_sub: str | None = None
    ) -> int:
        """Mark items source-verified (idempotent per item). Returns count added."""
        if await self._s.get(FormInstance, form_instance_id) is None:
            raise ClinicalError(f"Form instance {form_instance_id!r} not found")
        already = {
            v.item_id
            for v in (
                await self._s.scalars(
                    select(Verification).where(Verification.form_instance_id == form_instance_id)
                )
            ).all()
        }
        added = 0
        for item_id in item_ids:
            if item_id in already:
                continue
            self._s.add(
                Verification(
                    form_instance_id=form_instance_id, item_id=item_id, verified_by=verifier_sub
                )
            )
            self._audit(
                entity_type="verification",
                entity_id=f"{form_instance_id}:{item_id}",
                form_instance_id=form_instance_id,
                action="verify",
                item_id=item_id,
                actor_sub=verifier_sub,
            )
            added += 1
        await self._s.flush()
        return added

    async def list_verifications(self, form_instance_id: str) -> list[Verification]:
        rows = await self._s.scalars(
            select(Verification).where(Verification.form_instance_id == form_instance_id)
        )
        return list(rows.all())

    # ── subject-casebook sign-off + lock (E6) ────────────────────────────────

    async def sign_subject(
        self, subject_id: str, *, meaning: str, signer_sub: str | None = None
    ) -> SubjectSignature:
        """Sign off a subject casebook and lock all its form instances.

        Requires the subject to have at least one form instance and all of them
        to be complete or signed (none blank/in-progress)."""
        subject = await self._s.get(Subject, subject_id)
        if subject is None:
            raise ClinicalError(f"Subject {subject_id!r} not found")
        instances = await self.list_form_instances(subject_id)
        if not instances:
            raise ClinicalError("Subject has no form instances to sign off")
        unfinished = [fi for fi in instances if fi.status not in ("complete", "signed")]
        if unfinished:
            raise ClinicalError("All form instances must be complete or signed before sign-off")

        sig = SubjectSignature(subject_id=subject_id, signer_sub=signer_sub, meaning=meaning)
        self._s.add(sig)
        for fi in instances:
            fi.status = "locked"
        subject.status = "locked"
        await self._s.flush()
        self._audit(
            entity_type="subject",
            entity_id=subject_id,
            action="sign_subject",
            new_value="locked",
            reason=meaning,
            actor_sub=signer_sub,
        )
        return sig

    async def unlock_subject(
        self, subject_id: str, *, reason: str, actor_sub: str | None = None
    ) -> Subject:
        """Reopen a locked subject casebook: void signatures, reopen instances."""
        subject = await self._s.get(Subject, subject_id)
        if subject is None:
            raise ClinicalError(f"Subject {subject_id!r} not found")
        if subject.status != "locked":
            raise ClinicalError(f"Subject is {subject.status}, not locked")
        now = datetime.now(UTC)
        instances = await self.list_form_instances(subject_id)
        for fi in instances:
            fi.status = "in_progress"
            for sig in (
                await self._s.scalars(
                    select(Signature).where(
                        Signature.form_instance_id == fi.id,
                        Signature.voided == False,  # noqa: E712
                    )
                )
            ).all():
                sig.voided = True
                sig.voided_at = now
        for ssig in (
            await self._s.scalars(
                select(SubjectSignature).where(
                    SubjectSignature.subject_id == subject_id,
                    SubjectSignature.voided == False,  # noqa: E712
                )
            )
        ).all():
            ssig.voided = True
            ssig.voided_at = now
        subject.status = "enrolled"
        await self._s.flush()
        self._audit(
            entity_type="subject",
            entity_id=subject_id,
            action="unlock_subject",
            old_value="locked",
            new_value="enrolled",
            reason=reason,
            actor_sub=actor_sub,
        )
        return subject

    async def list_subject_signatures(self, subject_id: str) -> list[SubjectSignature]:
        rows = await self._s.scalars(
            select(SubjectSignature)
            .where(SubjectSignature.subject_id == subject_id)
            .order_by(SubjectSignature.signed_at)
        )
        return list(rows.all())

    # ── Study-level lock (E7 — validation pack) ─────────────────────────────

    async def get_active_study_lock(self, deployment_id: str) -> StudyLock | None:
        """Return the currently-active lock for a deployment, or None."""
        rows = await self._s.scalars(
            select(StudyLock)
            .where(StudyLock.deployment_id == deployment_id)
            .where(StudyLock.unlocked_at.is_(None))
            .order_by(StudyLock.locked_at.desc())
        )
        return rows.first()

    async def is_study_locked(self, deployment_id: str) -> bool:
        return (await self.get_active_study_lock(deployment_id)) is not None

    async def lock_study(
        self,
        deployment_id: str,
        *,
        reason: str,
        actor_sub: str | None,
        require_no_open_queries: bool = True,
    ) -> StudyLock:
        """Lock the whole deployment for analysis. Refuses while open
        (non-closed) queries exist unless `require_no_open_queries=False`."""
        if await self._s.get(StudyDeployment, deployment_id) is None:
            raise ClinicalError(f"Deployment {deployment_id!r} not found")
        if await self.is_study_locked(deployment_id):
            raise ClinicalError(f"Deployment {deployment_id!r} is already locked")
        if require_no_open_queries:
            open_q = await self._s.scalars(
                select(Query)
                .join(FormInstance, Query.form_instance_id == FormInstance.id)
                .join(Subject, FormInstance.subject_id == Subject.id)
                .where(Subject.deployment_id == deployment_id)
                .where(Query.status != "closed")
                .limit(1)
            )
            if open_q.first() is not None:
                raise ClinicalError(
                    "Cannot lock: at least one open query exists. "
                    "Resolve all queries first or pass require_no_open_queries=False."
                )
        lock = StudyLock(
            deployment_id=deployment_id,
            lock_reason=reason,
            locked_by_sub=actor_sub,
        )
        self._s.add(lock)
        await self._s.flush()
        self._audit(
            entity_type="study_deployment",
            entity_id=deployment_id,
            action="study_lock",
            new_value="locked",
            reason=reason,
            actor_sub=actor_sub,
        )
        return lock

    async def unlock_study(
        self,
        deployment_id: str,
        *,
        reason: str,
        actor_sub: str | None,
    ) -> StudyLock:
        """Release the deployment-wide lock (audited)."""
        lock = await self.get_active_study_lock(deployment_id)
        if lock is None:
            raise ClinicalError(f"Deployment {deployment_id!r} is not locked")
        lock.unlocked_at = datetime.now(UTC)
        lock.unlocked_by_sub = actor_sub
        lock.unlock_reason = reason
        await self._s.flush()
        self._audit(
            entity_type="study_deployment",
            entity_id=deployment_id,
            action="study_unlock",
            old_value="locked",
            new_value="unlocked",
            reason=reason,
            actor_sub=actor_sub,
        )
        return lock

    async def list_study_locks(self, deployment_id: str) -> list[StudyLock]:
        rows = await self._s.scalars(
            select(StudyLock)
            .where(StudyLock.deployment_id == deployment_id)
            .order_by(StudyLock.locked_at)
        )
        return list(rows.all())

    async def deployment_id_for_form_instance(self, form_instance_id: str) -> str | None:
        fi = await self._s.get(FormInstance, form_instance_id)
        if fi is None:
            return None
        subj = await self._s.get(Subject, fi.subject_id)
        return subj.deployment_id if subj is not None else None

    async def deployment_id_for_subject(self, subject_id: str) -> str | None:
        subj = await self._s.get(Subject, subject_id)
        return subj.deployment_id if subj is not None else None

    async def deployment_id_for_adverse_event(self, ae_id: str) -> str | None:
        ae = await self._s.get(AdverseEvent, ae_id)
        return ae.deployment_id if ae is not None else None

    async def deployment_id_for_deviation(self, deviation_id: str) -> str | None:
        dev = await self._s.get(ProtocolDeviation, deviation_id)
        return dev.deployment_id if dev is not None else None

    async def deployment_id_for_capa(self, capa_id: str) -> str | None:
        capa = await self._s.get(CapaAction, capa_id)
        if capa is None:
            return None
        return await self.deployment_id_for_deviation(capa.deviation_id)

    async def deployment_id_for_query(self, query_id: str) -> str | None:
        query = await self._s.get(Query, query_id)
        if query is None:
            return None
        # `Query.subject_id` is stored at write time so we can resolve
        # the deployment without re-traversing the form-instance.
        subj = await self._s.get(Subject, query.subject_id)
        return subj.deployment_id if subj is not None else None

    async def assert_study_unlocked(self, deployment_id: str) -> None:
        if await self.is_study_locked(deployment_id):
            raise StudyLockedError(
                f"Deployment {deployment_id!r} is locked. "
                "Unlock the study before further writes / signatures / SDV."
            )

    # ── IRT / Randomisation (E8) ────────────────────────────────────────

    async def get_active_schedule(self, deployment_id: str) -> RandomizationSchedule | None:
        rows = await self._s.scalars(
            select(RandomizationSchedule)
            .where(RandomizationSchedule.deployment_id == deployment_id)
            .where(RandomizationSchedule.status == "active")
            .order_by(RandomizationSchedule.created_at.desc())
        )
        return rows.first()

    async def create_randomization_schedule(
        self,
        deployment_id: str,
        *,
        algorithm: str,
        arms: list[str],
        ratio: list[int] | None = None,
        block_sizes: list[int] | None = None,
        strata_factors: list[str] | None = None,
        weights: dict[str, float] | None = None,
        seed: int,
        blinding: str = "open_label",
        sequence_json: str | None = None,
        minimisation_state_json: str | None = None,
        actor_sub: str | None = None,
    ) -> RandomizationSchedule:
        if await self._s.get(StudyDeployment, deployment_id) is None:
            raise ClinicalError(f"Deployment {deployment_id!r} not found")
        if await self.get_active_schedule(deployment_id) is not None:
            raise ClinicalError(
                f"Deployment {deployment_id!r} already has an active "
                "randomisation schedule. Close it before generating a new one."
            )
        schedule = RandomizationSchedule(
            deployment_id=deployment_id,
            algorithm=algorithm,
            arms_json=json.dumps(arms),
            ratio_json=json.dumps(ratio or [1] * len(arms)),
            block_sizes_json=json.dumps(block_sizes) if block_sizes else None,
            strata_factors_json=json.dumps(strata_factors) if strata_factors else None,
            weights_json=json.dumps(weights) if weights else None,
            seed=seed,
            blinding=blinding,
            sequence_json=sequence_json,
            minimisation_state_json=minimisation_state_json,
            created_by_sub=actor_sub,
        )
        self._s.add(schedule)
        await self._s.flush()
        self._audit(
            entity_type="randomization_schedule",
            entity_id=schedule.id,
            action="create",
            new_value=algorithm,
            actor_sub=actor_sub,
            reason=f"arms={arms!r} seed={seed} blinding={blinding}",
        )
        return schedule

    async def close_randomization_schedule(
        self,
        schedule_id: str,
        *,
        actor_sub: str | None = None,
    ) -> RandomizationSchedule:
        schedule = await self._s.get(RandomizationSchedule, schedule_id)
        if schedule is None:
            raise ClinicalError(f"Schedule {schedule_id!r} not found")
        if schedule.status == "closed":
            raise ClinicalError(f"Schedule {schedule_id!r} is already closed")
        schedule.status = "closed"
        schedule.closed_at = datetime.now(UTC)
        await self._s.flush()
        self._audit(
            entity_type="randomization_schedule",
            entity_id=schedule.id,
            action="close",
            old_value="active",
            new_value="closed",
            actor_sub=actor_sub,
        )
        return schedule

    async def get_allocation_for_subject(self, subject_id: str) -> Allocation | None:
        rows = await self._s.scalars(select(Allocation).where(Allocation.subject_id == subject_id))
        return rows.first()

    async def persist_allocation(
        self,
        *,
        schedule: RandomizationSchedule,
        subject: Subject,
        arm: str,
        stratum_label: str | None,
        factor_values: dict[str, str] | None,
        sequence_position: int | None,
        actor_sub: str | None,
        updated_minimisation_state_json: str | None = None,
    ) -> Allocation:
        """Persist an allocation row + update the schedule's minimisation
        state when present. Refuses 409 on a double-allocation attempt."""
        if await self.get_allocation_for_subject(subject.id) is not None:
            raise ClinicalError(f"Subject {subject.subject_code!r} is already randomised.")
        unblinded = schedule.blinding == "open_label"
        allocation = Allocation(
            deployment_id=schedule.deployment_id,
            schedule_id=schedule.id,
            subject_id=subject.id,
            arm=arm,
            stratum_label=stratum_label,
            factor_values_json=json.dumps(factor_values) if factor_values else None,
            sequence_position=sequence_position,
            allocated_by_sub=actor_sub,
            unblinded=unblinded,
            unblinded_at=datetime.now(UTC) if unblinded else None,
        )
        self._s.add(allocation)
        if updated_minimisation_state_json is not None:
            schedule.minimisation_state_json = updated_minimisation_state_json
        await self._s.flush()
        self._audit(
            entity_type="allocation",
            entity_id=allocation.id,
            action="allocate",
            new_value=arm,
            reason=(
                f"subject={subject.subject_code!r} stratum={stratum_label!r} "
                f"blinding={schedule.blinding}"
            ),
            actor_sub=actor_sub,
        )
        return allocation

    async def list_allocations(self, deployment_id: str) -> list[Allocation]:
        rows = await self._s.scalars(
            select(Allocation)
            .where(Allocation.deployment_id == deployment_id)
            .order_by(Allocation.allocated_at)
        )
        return list(rows.all())

    async def code_break(
        self,
        subject_id: str,
        *,
        reason: str,
        actor_sub: str | None,
    ) -> CodeBreakEvent:
        """PI-triggered emergency unblinding. Flips Allocation.unblinded
        + creates a CodeBreakEvent row + audits."""
        allocation = await self.get_allocation_for_subject(subject_id)
        if allocation is None:
            raise ClinicalError(f"Subject {subject_id!r} has no allocation to break.")
        if allocation.unblinded:
            raise ClinicalError(f"Subject {subject_id!r} is already unblinded.")
        now = datetime.now(UTC)
        event = CodeBreakEvent(
            deployment_id=allocation.deployment_id,
            subject_id=subject_id,
            allocation_id=allocation.id,
            reason=reason,
            broken_at=now,
            broken_by_sub=actor_sub,
        )
        allocation.unblinded = True
        allocation.unblinded_at = now
        self._s.add(event)
        await self._s.flush()
        self._audit(
            entity_type="code_break_event",
            entity_id=event.id,
            action="code_break",
            new_value="unblinded",
            reason=reason,
            actor_sub=actor_sub,
        )
        return event

    async def list_code_break_events(self, deployment_id: str) -> list[CodeBreakEvent]:
        rows = await self._s.scalars(
            select(CodeBreakEvent)
            .where(CodeBreakEvent.deployment_id == deployment_id)
            .order_by(CodeBreakEvent.broken_at)
        )
        return list(rows.all())

    async def deployment_id_for_schedule(self, schedule_id: str) -> str | None:
        schedule = await self._s.get(RandomizationSchedule, schedule_id)
        return schedule.deployment_id if schedule is not None else None

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

    # ── Adverse events (top-6 #4 safety subsystem) ──────────────────────────

    async def record_adverse_event(
        self,
        subject_id: str,
        *,
        term_text: str,
        severity_grade: int,
        outcome: str,
        relationship_to_intervention: str,
        start_date: datetime,
        end_date: datetime | None = None,
        hospitalisation_flag: bool = False,
        life_threatening_flag: bool = False,
        persistent_disability_flag: bool = False,
        congenital_anomaly_flag: bool = False,
        other_medically_significant_flag: bool = False,
        narrative: str | None = None,
        form_instance_id: str | None = None,
        actor_sub: str | None = None,
    ) -> AdverseEvent:
        """Capture a new AE; auto-classifies serious + sets the 24h deadline."""
        subject = await self._s.get(Subject, subject_id)
        if subject is None:
            raise ClinicalError(f"Subject {subject_id!r} not found.")
        if severity_grade < 1 or severity_grade > 5:
            raise ClinicalError("severity_grade must be 1–5 (CTCAE scale).")

        is_serious, reasons = auto_classify_serious(
            severity_grade=severity_grade,
            outcome=outcome,
            hospitalisation_flag=hospitalisation_flag,
            life_threatening_flag=life_threatening_flag,
            persistent_disability_flag=persistent_disability_flag,
            congenital_anomaly_flag=congenital_anomaly_flag,
            other_medically_significant_flag=other_medically_significant_flag,
        )
        reported_at = datetime.now(UTC)
        deadline = compute_reporting_deadline(is_serious=is_serious, reported_at=reported_at)

        ae = AdverseEvent(
            subject_id=subject_id,
            deployment_id=subject.deployment_id,
            form_instance_id=form_instance_id,
            term_text=term_text,
            severity_grade=severity_grade,
            outcome=outcome,
            relationship_to_intervention=relationship_to_intervention,
            start_date=start_date,
            end_date=end_date,
            is_serious=is_serious,
            serious_reasons_json=json.dumps(reasons),
            reported_at=reported_at,
            reportable_deadline=deadline,
            recorded_by=actor_sub,
            narrative=narrative,
        )
        self._s.add(ae)
        await self._s.flush()
        self._audit(
            entity_type="adverse_event",
            entity_id=ae.id,
            action="create",
            actor_sub=actor_sub,
            new_value=f"severity={severity_grade}, serious={is_serious}",
        )
        return ae

    async def get_adverse_event(self, ae_id: str) -> AdverseEvent | None:
        return await self._s.get(AdverseEvent, ae_id)

    async def list_adverse_events(
        self,
        *,
        subject_id: str | None = None,
        deployment_id: str | None = None,
        serious_only: bool = False,
    ) -> list[AdverseEvent]:
        stmt = select(AdverseEvent).order_by(AdverseEvent.reported_at.desc())
        if subject_id is not None:
            stmt = stmt.where(AdverseEvent.subject_id == subject_id)
        if deployment_id is not None:
            stmt = stmt.where(AdverseEvent.deployment_id == deployment_id)
        if serious_only:
            stmt = stmt.where(AdverseEvent.is_serious.is_(True))
        return list((await self._s.scalars(stmt)).all())

    async def list_overdue_serious_aes(self, deployment_id: str) -> list[AdverseEvent]:
        """Serious AEs past their 24h escalation deadline that haven't
        been reported to authority yet — drives the platform's safety
        dashboard."""
        now = datetime.now(UTC)
        stmt = (
            select(AdverseEvent)
            .where(
                AdverseEvent.deployment_id == deployment_id,
                AdverseEvent.is_serious.is_(True),
                AdverseEvent.reportable_deadline.is_not(None),
                AdverseEvent.reportable_deadline < now,
                AdverseEvent.reported_to_authority_at.is_(None),
            )
            .order_by(AdverseEvent.reportable_deadline.asc())
        )
        return list((await self._s.scalars(stmt)).all())

    async def reclassify_adverse_event(
        self,
        ae_id: str,
        *,
        is_serious: bool | None = None,
        serious_reasons: list[SeriousReason] | None = None,
        outcome: str | None = None,
        severity_grade: int | None = None,
        meddra_pt: str | None = None,
        narrative: str | None = None,
        actor_sub: str | None = None,
    ) -> AdverseEvent:
        """PI/DM override of the auto-classification.

        Only the fields explicitly passed are touched. Setting
        `is_serious=False` clears the reportable_deadline; setting it to
        True (re)computes it from `reported_at`. Every change is audited.
        """
        ae = await self._s.get(AdverseEvent, ae_id)
        if ae is None:
            raise ClinicalError(f"AdverseEvent {ae_id!r} not found.")
        old_is_serious = ae.is_serious
        if severity_grade is not None:
            if severity_grade < 1 or severity_grade > 5:
                raise ClinicalError("severity_grade must be 1–5.")
            ae.severity_grade = severity_grade
        if outcome is not None:
            ae.outcome = outcome
        if is_serious is not None:
            ae.is_serious = is_serious
            if is_serious and serious_reasons is None:
                # Re-derive reasons from the existing fields.
                _, reasons = auto_classify_serious(
                    severity_grade=ae.severity_grade,
                    outcome=ae.outcome,
                )
                ae.serious_reasons_json = json.dumps(reasons)
            ae.reportable_deadline = compute_reporting_deadline(
                is_serious=is_serious, reported_at=ae.reported_at
            )
        if serious_reasons is not None:
            ae.serious_reasons_json = json.dumps(serious_reasons)
        if meddra_pt is not None:
            ae.meddra_pt = meddra_pt
        if narrative is not None:
            ae.narrative = narrative
        ae.classified_by = actor_sub
        await self._s.flush()
        self._audit(
            entity_type="adverse_event",
            entity_id=ae.id,
            action="classify",
            actor_sub=actor_sub,
            old_value=f"serious={old_is_serious}",
            new_value=f"serious={ae.is_serious}, severity={ae.severity_grade}",
        )
        return ae

    async def mark_ae_reported_to_authority(
        self, ae_id: str, *, actor_sub: str | None = None
    ) -> AdverseEvent:
        """Stamp the AE as reported (3500A generated + acknowledged).

        Clears the reportable_deadline so the overdue endpoint stops
        surfacing it. Idempotent — re-stamping is a no-op.
        """
        ae = await self._s.get(AdverseEvent, ae_id)
        if ae is None:
            raise ClinicalError(f"AdverseEvent {ae_id!r} not found.")
        if ae.reported_to_authority_at is not None:
            return ae
        ae.reported_to_authority_at = datetime.now(UTC)
        ae.reportable_deadline = None
        await self._s.flush()
        self._audit(
            entity_type="adverse_event",
            entity_id=ae.id,
            action="reported_to_authority",
            actor_sub=actor_sub,
        )
        return ae

    # ── Protocol deviations + CAPA ──────────────────────────────────────────

    async def record_deviation(
        self,
        *,
        deployment_id: str,
        subject_id: str | None,
        classification: str,
        category: str,
        description: str,
        root_cause: str | None = None,
        actor_sub: str | None = None,
    ) -> ProtocolDeviation:
        if classification not in ("major", "minor", "critical"):
            raise ClinicalError("classification must be 'major', 'minor', or 'critical'.")
        dev = ProtocolDeviation(
            subject_id=subject_id,
            deployment_id=deployment_id,
            classification=classification,
            category=category,
            description=description,
            root_cause=root_cause,
            discovered_by=actor_sub,
        )
        self._s.add(dev)
        await self._s.flush()
        self._audit(
            entity_type="protocol_deviation",
            entity_id=dev.id,
            action="create",
            actor_sub=actor_sub,
            new_value=f"classification={classification}, category={category}",
        )
        return dev

    async def reclassify_deviation(
        self,
        deviation_id: str,
        *,
        classification: str | None = None,
        category: str | None = None,
        root_cause: str | None = None,
        actor_sub: str | None = None,
    ) -> ProtocolDeviation:
        dev = await self._s.get(ProtocolDeviation, deviation_id)
        if dev is None:
            raise ClinicalError(f"Deviation {deviation_id!r} not found.")
        old_classification = dev.classification
        if classification is not None:
            if classification not in ("major", "minor", "critical"):
                raise ClinicalError("classification must be 'major', 'minor', or 'critical'.")
            dev.classification = classification
        if category is not None:
            dev.category = category
        if root_cause is not None:
            dev.root_cause = root_cause
        dev.classified_by = actor_sub
        await self._s.flush()
        self._audit(
            entity_type="protocol_deviation",
            entity_id=dev.id,
            action="classify",
            actor_sub=actor_sub,
            old_value=f"classification={old_classification}",
            new_value=f"classification={dev.classification}",
        )
        return dev

    async def get_deviation(self, deviation_id: str) -> ProtocolDeviation | None:
        return await self._s.get(ProtocolDeviation, deviation_id)

    async def list_deviations(
        self,
        *,
        deployment_id: str | None = None,
        subject_id: str | None = None,
        status: str | None = None,
    ) -> list[ProtocolDeviation]:
        stmt = select(ProtocolDeviation).order_by(ProtocolDeviation.discovered_at.desc())
        if deployment_id is not None:
            stmt = stmt.where(ProtocolDeviation.deployment_id == deployment_id)
        if subject_id is not None:
            stmt = stmt.where(ProtocolDeviation.subject_id == subject_id)
        if status is not None:
            stmt = stmt.where(ProtocolDeviation.status == status)
        return list((await self._s.scalars(stmt)).all())

    async def add_capa(
        self,
        deviation_id: str,
        *,
        action_text: str,
        owner_sub: str | None = None,
        due_date: datetime | None = None,
        actor_sub: str | None = None,
    ) -> CapaAction:
        dev = await self._s.get(ProtocolDeviation, deviation_id)
        if dev is None:
            raise ClinicalError(f"Deviation {deviation_id!r} not found.")
        if dev.status == "closed":
            raise ClinicalError("Cannot add CAPA — deviation is closed. Reopen it first.")
        capa = CapaAction(
            deviation_id=deviation_id,
            action_text=action_text,
            owner_sub=owner_sub,
            due_date=due_date,
            created_by=actor_sub,
        )
        self._s.add(capa)
        # Adding a CAPA flips the parent deviation to under_capa if it
        # was open. Idempotent — already-under_capa stays under_capa.
        if dev.status == "open":
            dev.status = "under_capa"
        await self._s.flush()
        self._audit(
            entity_type="capa_action",
            entity_id=capa.id,
            action="create",
            actor_sub=actor_sub,
            reason=f"deviation={deviation_id}",
        )
        return capa

    async def complete_capa(self, capa_id: str, *, actor_sub: str | None = None) -> CapaAction:
        capa = await self._s.get(CapaAction, capa_id)
        if capa is None:
            raise ClinicalError(f"CAPA {capa_id!r} not found.")
        if capa.status == "completed":
            return capa
        capa.status = "completed"
        capa.completed_at = datetime.now(UTC)
        capa.completed_by = actor_sub
        await self._s.flush()
        self._audit(
            entity_type="capa_action",
            entity_id=capa.id,
            action="complete",
            actor_sub=actor_sub,
        )
        return capa

    async def list_capas(self, deviation_id: str) -> list[CapaAction]:
        stmt = (
            select(CapaAction)
            .where(CapaAction.deviation_id == deviation_id)
            .order_by(CapaAction.created_at)
        )
        return list((await self._s.scalars(stmt)).all())

    async def close_deviation(
        self, deviation_id: str, *, actor_sub: str | None = None
    ) -> ProtocolDeviation:
        """PI closes a deviation — refused if any CAPA is still open."""
        dev = await self._s.get(ProtocolDeviation, deviation_id)
        if dev is None:
            raise ClinicalError(f"Deviation {deviation_id!r} not found.")
        if dev.status == "closed":
            return dev
        capas = await self.list_capas(deviation_id)
        open_capas = [c for c in capas if c.status != "completed"]
        if open_capas:
            raise ClinicalError(f"Cannot close — {len(open_capas)} CAPA action(s) still open.")
        dev.status = "closed"
        dev.resolved_at = datetime.now(UTC)
        dev.resolved_by = actor_sub
        await self._s.flush()
        self._audit(
            entity_type="protocol_deviation",
            entity_id=dev.id,
            action="close",
            actor_sub=actor_sub,
        )
        return dev

    # ── Recruitment / screening log (P1 #3) ────────────────────────────

    async def record_screening(
        self,
        deployment_id: str,
        *,
        screening_code: str,
        screening_date: datetime | None = None,
        site_id: str | None = None,
        age_band: str | None = None,
        sex: str | None = None,
        race: str | None = None,
        ethnicity: str | None = None,
        dob_year: int | None = None,
        notes: str = "",
        actor_sub: str | None = None,
    ) -> ScreeningLog:
        """Create a new screening-log row at eligibility_status='pending'."""
        deployment = await self._s.get(StudyDeployment, deployment_id)
        if deployment is None:
            raise ClinicalError(f"Deployment {deployment_id!r} not found.")
        if site_id is not None:
            site = await self._s.get(Site, site_id)
            if site is None or site.deployment_id != deployment_id:
                raise ClinicalError(f"Site {site_id!r} not found in deployment {deployment_id!r}.")
        # Enforce unique screening_code per deployment at the app layer too —
        # gives a clear error message instead of an IntegrityError.
        existing_stmt = select(ScreeningLog).where(
            ScreeningLog.deployment_id == deployment_id,
            ScreeningLog.screening_code == screening_code,
        )
        if (await self._s.scalars(existing_stmt)).first() is not None:
            raise ClinicalError(
                f"Screening code {screening_code!r} already exists in this deployment."
            )
        log = ScreeningLog(
            deployment_id=deployment_id,
            site_id=site_id,
            screening_code=screening_code,
            screening_date=screening_date or datetime.now(UTC),
            age_band=age_band,
            sex=sex,
            race=race,
            ethnicity=ethnicity,
            dob_year=dob_year,
            notes=notes,
            recorded_by_sub=actor_sub,
            updated_by_sub=actor_sub,
        )
        self._s.add(log)
        await self._s.flush()
        self._audit(
            entity_type="screening_log",
            entity_id=log.id,
            action="create",
            actor_sub=actor_sub,
            new_value=f"code={screening_code}",
        )
        return log

    async def get_screening_log(self, log_id: str) -> ScreeningLog | None:
        return await self._s.get(ScreeningLog, log_id)

    async def list_screening_logs(
        self,
        *,
        deployment_id: str,
        site_id: str | None = None,
        eligibility_status: str | None = None,
        consent_status: str | None = None,
        enrolment_status: str | None = None,
    ) -> list[ScreeningLog]:
        stmt = (
            select(ScreeningLog)
            .where(ScreeningLog.deployment_id == deployment_id)
            .order_by(ScreeningLog.screening_date.desc(), ScreeningLog.recorded_at.desc())
        )
        if site_id is not None:
            stmt = stmt.where(ScreeningLog.site_id == site_id)
        if eligibility_status is not None:
            stmt = stmt.where(ScreeningLog.eligibility_status == eligibility_status)
        if consent_status is not None:
            stmt = stmt.where(ScreeningLog.consent_status == consent_status)
        if enrolment_status is not None:
            stmt = stmt.where(ScreeningLog.enrolment_status == enrolment_status)
        return list((await self._s.scalars(stmt)).all())

    async def update_screening_eligibility(
        self,
        log_id: str,
        *,
        eligibility_status: str,
        exclusion_reason_code: str | None = None,
        exclusion_reason_text: str = "",
        actor_sub: str | None = None,
    ) -> ScreeningLog:
        """Set eligibility_status. When marking screen_failure, MUST supply
        a CONSORT-coded reason."""
        if eligibility_status not in ELIGIBILITY_STATUSES:
            raise ClinicalError(
                f"Invalid eligibility_status {eligibility_status!r}; choose one of "
                f"{', '.join(ELIGIBILITY_STATUSES)}."
            )
        log = await self.get_screening_log(log_id)
        if log is None:
            raise ClinicalError(f"Screening log {log_id!r} not found.")
        if eligibility_status == "screen_failure":
            if not exclusion_reason_code:
                raise ClinicalError("exclusion_reason_code required when marking screen_failure.")
            if exclusion_reason_code not in CONSORT_EXCLUSION_REASONS:
                raise ClinicalError(
                    f"Unknown exclusion_reason_code {exclusion_reason_code!r}; choose one of "
                    f"{', '.join(CONSORT_EXCLUSION_REASONS)}."
                )
        old = log.eligibility_status
        log.eligibility_status = eligibility_status
        log.exclusion_reason_code = (
            exclusion_reason_code if eligibility_status == "screen_failure" else None
        )
        log.exclusion_reason_text = (
            exclusion_reason_text if eligibility_status == "screen_failure" else ""
        )
        log.updated_by_sub = actor_sub
        await self._s.flush()
        self._audit(
            entity_type="screening_log",
            entity_id=log.id,
            action="eligibility",
            actor_sub=actor_sub,
            old_value=old,
            new_value=eligibility_status,
        )
        return log

    async def update_screening_consent(
        self,
        log_id: str,
        *,
        consent_status: str,
        consent_date: datetime | None = None,
        actor_sub: str | None = None,
    ) -> ScreeningLog:
        if consent_status not in CONSENT_STATUSES:
            raise ClinicalError(
                f"Invalid consent_status {consent_status!r}; choose one of "
                f"{', '.join(CONSENT_STATUSES)}."
            )
        log = await self.get_screening_log(log_id)
        if log is None:
            raise ClinicalError(f"Screening log {log_id!r} not found.")
        if log.eligibility_status != "eligible" and consent_status == "consented":
            raise ClinicalError("Cannot mark consented before eligibility=eligible.")
        old = log.consent_status
        log.consent_status = consent_status
        log.consent_date = consent_date or (
            datetime.now(UTC) if consent_status == "consented" else None
        )
        log.updated_by_sub = actor_sub
        await self._s.flush()
        self._audit(
            entity_type="screening_log",
            entity_id=log.id,
            action="consent",
            actor_sub=actor_sub,
            old_value=old,
            new_value=consent_status,
        )
        return log

    async def update_screening_enrolment(
        self,
        log_id: str,
        *,
        enrolment_status: str,
        enrolled_subject_id: str | None = None,
        enrolment_date: datetime | None = None,
        actor_sub: str | None = None,
    ) -> ScreeningLog:
        if enrolment_status not in ENROLMENT_STATUSES:
            raise ClinicalError(
                f"Invalid enrolment_status {enrolment_status!r}; choose one of "
                f"{', '.join(ENROLMENT_STATUSES)}."
            )
        log = await self.get_screening_log(log_id)
        if log is None:
            raise ClinicalError(f"Screening log {log_id!r} not found.")
        if enrolment_status == "enrolled":
            if log.consent_status != "consented":
                raise ClinicalError("Cannot mark enrolled before consent_status=consented.")
            if enrolled_subject_id is None:
                raise ClinicalError("enrolled_subject_id required when marking enrolled.")
            subject = await self._s.get(Subject, enrolled_subject_id)
            if subject is None:
                raise ClinicalError(f"Subject {enrolled_subject_id!r} not found.")
            if subject.deployment_id != log.deployment_id:
                raise ClinicalError("Subject and screening log belong to different deployments.")
        old = log.enrolment_status
        log.enrolment_status = enrolment_status
        log.enrolled_subject_id = enrolled_subject_id if enrolment_status == "enrolled" else None
        log.enrolment_date = enrolment_date or (
            datetime.now(UTC) if enrolment_status == "enrolled" else None
        )
        log.updated_by_sub = actor_sub
        await self._s.flush()
        self._audit(
            entity_type="screening_log",
            entity_id=log.id,
            action="enrolment",
            actor_sub=actor_sub,
            old_value=old,
            new_value=enrolment_status,
        )
        return log

    async def recruitment_funnel(
        self,
        deployment_id: str,
        *,
        site_id: str | None = None,
    ) -> dict[str, object]:
        """Compute the funnel rollup for a deployment.

        Returns the four canonical stage counts plus per-week per-site
        breakdowns + per-reason exclusion counts. Counts are computed
        in-Python for portability across SQLite (test fixtures) and
        Postgres (runtime) — the screening_logs table is bounded by
        per-deployment lifetime screening volume, which is small enough
        (≤ low thousands) that a single fetch + dict aggregation is fine.
        """
        logs = await self.list_screening_logs(deployment_id=deployment_id, site_id=site_id)
        screened = len(logs)
        eligible = sum(1 for log in logs if log.eligibility_status == "eligible")
        consented = sum(1 for log in logs if log.consent_status == "consented")
        enrolled = sum(1 for log in logs if log.enrolment_status == "enrolled")

        # Per-reason breakdown (excluded subjects only).
        per_reason: dict[str, int] = {}
        for log in logs:
            if log.eligibility_status == "screen_failure" and log.exclusion_reason_code:
                per_reason[log.exclusion_reason_code] = (
                    per_reason.get(log.exclusion_reason_code, 0) + 1
                )

        # Per-week × per-site breakdown.
        per_week_site: dict[str, dict[str, dict[str, int]]] = {}
        for log in logs:
            week = log.screening_date.strftime("%Y-W%V")
            site = log.site_id or "(unassigned)"
            week_bucket = per_week_site.setdefault(week, {})
            site_bucket = week_bucket.setdefault(
                site,
                {"screened": 0, "eligible": 0, "consented": 0, "enrolled": 0},
            )
            site_bucket["screened"] += 1
            if log.eligibility_status == "eligible":
                site_bucket["eligible"] += 1
            if log.consent_status == "consented":
                site_bucket["consented"] += 1
            if log.enrolment_status == "enrolled":
                site_bucket["enrolled"] += 1

        return {
            "deployment_id": deployment_id,
            "totals": {
                "screened": screened,
                "eligible": eligible,
                "consented": consented,
                "enrolled": enrolled,
            },
            "screen_failures_by_reason": per_reason,
            "per_week_per_site": per_week_site,
        }

    # ── Visit scheduling + participant reminders (P1 #4) ───────────────

    _ALLOWED_REMINDER_CHANNELS = ("email", "sms")

    async def create_visit_schedule(
        self,
        deployment_id: str,
        *,
        name: str,
        description: str = "",
        actor_sub: str | None = None,
    ) -> VisitSchedule:
        deployment = await self._s.get(StudyDeployment, deployment_id)
        if deployment is None:
            raise ClinicalError(f"Deployment {deployment_id!r} not found.")
        existing = await self._s.scalar(
            select(VisitSchedule).where(
                VisitSchedule.deployment_id == deployment_id,
                VisitSchedule.name == name,
            )
        )
        if existing is not None:
            raise ClinicalError(f"Visit schedule named {name!r} already exists in this deployment.")
        schedule = VisitSchedule(
            deployment_id=deployment_id,
            name=name,
            description=description,
            created_by_sub=actor_sub,
        )
        self._s.add(schedule)
        await self._s.flush()
        self._audit(
            entity_type="visit_schedule",
            entity_id=schedule.id,
            action="create",
            actor_sub=actor_sub,
            new_value=name,
        )
        return schedule

    async def set_active_visit_schedule(
        self,
        schedule_id: str,
        *,
        actor_sub: str | None = None,
    ) -> VisitSchedule:
        """Deactivate any other active schedule in the deployment, then
        activate this one."""
        schedule = await self._s.get(VisitSchedule, schedule_id)
        if schedule is None:
            raise ClinicalError(f"Visit schedule {schedule_id!r} not found.")
        # Deactivate siblings.
        siblings = await self._s.scalars(
            select(VisitSchedule).where(
                VisitSchedule.deployment_id == schedule.deployment_id,
                VisitSchedule.id != schedule.id,
                VisitSchedule.is_active.is_(True),
            )
        )
        for sibling in siblings:
            sibling.is_active = False
        schedule.is_active = True
        await self._s.flush()
        self._audit(
            entity_type="visit_schedule",
            entity_id=schedule.id,
            action="activate",
            actor_sub=actor_sub,
        )
        return schedule

    async def list_visit_schedules(self, deployment_id: str) -> list[VisitSchedule]:
        rows = await self._s.scalars(
            select(VisitSchedule)
            .where(VisitSchedule.deployment_id == deployment_id)
            .order_by(VisitSchedule.created_at)
        )
        return list(rows)

    async def get_active_visit_schedule(self, deployment_id: str) -> VisitSchedule | None:
        result: VisitSchedule | None = await self._s.scalar(
            select(VisitSchedule).where(
                VisitSchedule.deployment_id == deployment_id,
                VisitSchedule.is_active.is_(True),
            )
        )
        return result

    async def add_scheduled_visit(
        self,
        schedule_id: str,
        *,
        visit_name: str,
        day_offset: int,
        window_before_days: int = 0,
        window_after_days: int = 0,
        reminder_offsets: list[int] | None = None,
        ordering: int = 0,
        actor_sub: str | None = None,
    ) -> ScheduledVisit:
        schedule = await self._s.get(VisitSchedule, schedule_id)
        if schedule is None:
            raise ClinicalError(f"Visit schedule {schedule_id!r} not found.")
        if window_before_days < 0 or window_after_days < 0:
            raise ClinicalError("window_before_days and window_after_days must be non-negative.")
        offsets = reminder_offsets if reminder_offsets is not None else [-7, -1, 0]
        for offset in offsets:
            if offset > 0:
                raise ClinicalError(
                    "reminder_offsets are days BEFORE the due date — use non-positive ints."
                )
        existing = await self._s.scalar(
            select(ScheduledVisit).where(
                ScheduledVisit.schedule_id == schedule_id,
                ScheduledVisit.visit_name == visit_name,
            )
        )
        if existing is not None:
            raise ClinicalError(f"Visit named {visit_name!r} already exists in this schedule.")
        visit = ScheduledVisit(
            schedule_id=schedule_id,
            visit_name=visit_name,
            day_offset=day_offset,
            window_before_days=window_before_days,
            window_after_days=window_after_days,
            reminder_offsets_json=json.dumps(sorted(offsets)),
            ordering=ordering,
        )
        self._s.add(visit)
        await self._s.flush()
        self._audit(
            entity_type="scheduled_visit",
            entity_id=visit.id,
            action="create",
            actor_sub=actor_sub,
            new_value=f"name={visit_name},day={day_offset}",
        )
        return visit

    async def list_scheduled_visits(self, schedule_id: str) -> list[ScheduledVisit]:
        rows = await self._s.scalars(
            select(ScheduledVisit)
            .where(ScheduledVisit.schedule_id == schedule_id)
            .order_by(ScheduledVisit.ordering, ScheduledVisit.day_offset)
        )
        return list(rows)

    async def generate_planned_visits(
        self,
        subject_id: str,
        *,
        baseline_date: datetime | None = None,
        actor_sub: str | None = None,
    ) -> list[PlannedVisit]:
        """Generate PlannedVisit rows for `subject_id` against the
        deployment's ACTIVE visit schedule. Idempotent: visits that
        already exist for this subject + scheduled_visit are skipped.

        `baseline_date` overrides Subject.baseline_date (which itself
        falls back to Subject.created_at when unset).
        """
        subject = await self._s.get(Subject, subject_id)
        if subject is None:
            raise ClinicalError(f"Subject {subject_id!r} not found.")
        schedule = await self.get_active_visit_schedule(subject.deployment_id)
        if schedule is None:
            raise ClinicalError(
                "No active visit schedule for this deployment — create + activate one first."
            )
        baseline = baseline_date or subject.baseline_date or subject.created_at
        if baseline.tzinfo is None:
            baseline = baseline.replace(tzinfo=UTC)
        # Persist the baseline if the caller passed one explicitly.
        if baseline_date is not None:
            subject.baseline_date = baseline_date
        visits = await self.list_scheduled_visits(schedule.id)
        from datetime import timedelta

        created: list[PlannedVisit] = []
        for sv in visits:
            existing = await self._s.scalar(
                select(PlannedVisit).where(
                    PlannedVisit.subject_id == subject_id,
                    PlannedVisit.scheduled_visit_id == sv.id,
                )
            )
            if existing is not None:
                continue
            planned = baseline + timedelta(days=sv.day_offset)
            row = PlannedVisit(
                subject_id=subject_id,
                scheduled_visit_id=sv.id,
                planned_date=planned,
                window_start=planned - timedelta(days=sv.window_before_days),
                window_end=planned + timedelta(days=sv.window_after_days),
            )
            self._s.add(row)
            created.append(row)
        await self._s.flush()
        if created:
            self._audit(
                entity_type="subject",
                entity_id=subject_id,
                action="planned_visits_generated",
                actor_sub=actor_sub,
                new_value=f"count={len(created)}",
            )
        return created

    async def list_planned_visits(
        self,
        *,
        subject_id: str | None = None,
        deployment_id: str | None = None,
        status: str | None = None,
    ) -> list[PlannedVisit]:
        stmt = select(PlannedVisit).order_by(PlannedVisit.planned_date)
        if subject_id is not None:
            stmt = stmt.where(PlannedVisit.subject_id == subject_id)
        elif deployment_id is not None:
            subject_ids_stmt = select(Subject.id).where(Subject.deployment_id == deployment_id)
            stmt = stmt.where(PlannedVisit.subject_id.in_(subject_ids_stmt))
        if status is not None:
            stmt = stmt.where(PlannedVisit.status == status)
        return list((await self._s.scalars(stmt)).all())

    async def update_planned_visit(
        self,
        planned_visit_id: str,
        *,
        planned_date: datetime | None = None,
        status: str | None = None,
        override_reason: str = "",
        actor_sub: str | None = None,
    ) -> PlannedVisit:
        row = await self._s.get(PlannedVisit, planned_visit_id)
        if row is None:
            raise ClinicalError(f"Planned visit {planned_visit_id!r} not found.")
        if status is not None and status not in (
            "pending",
            "completed",
            "missed",
            "cancelled",
        ):
            raise ClinicalError(
                f"Invalid status {status!r}; choose pending | completed | missed | cancelled."
            )
        if planned_date is not None and planned_date != row.planned_date:
            if not override_reason:
                raise ClinicalError("override_reason required when shifting planned_date.")
            from datetime import timedelta

            sv = await self._s.get(ScheduledVisit, row.scheduled_visit_id)
            if sv is not None:
                row.window_start = planned_date - timedelta(days=sv.window_before_days)
                row.window_end = planned_date + timedelta(days=sv.window_after_days)
            row.planned_date = planned_date
            row.override_reason = override_reason
        if status is not None:
            old_status = row.status
            row.status = status
            if status == "completed":
                row.completed_at = datetime.now(UTC)
            self._audit(
                entity_type="planned_visit",
                entity_id=row.id,
                action="status",
                actor_sub=actor_sub,
                old_value=old_status,
                new_value=status,
            )
        else:
            self._audit(
                entity_type="planned_visit",
                entity_id=row.id,
                action="reschedule",
                actor_sub=actor_sub,
                new_value=override_reason,
            )
        await self._s.flush()
        return row

    async def upsert_participant_contact(
        self,
        participant_access_id: str,
        *,
        email: str | None = None,
        phone: str | None = None,
        preferred_channel: str = "email",
        opt_in_channels: list[str] | None = None,
        opt_out: bool = False,
        actor_sub: str | None = None,
    ) -> ParticipantContact:
        access = await self._s.get(ParticipantAccess, participant_access_id)
        if access is None:
            raise ClinicalError(f"Participant access {participant_access_id!r} not found.")
        if preferred_channel not in ("email", "sms", "none"):
            raise ClinicalError(f"Invalid preferred_channel {preferred_channel!r}.")
        for ch in opt_in_channels or []:
            if ch not in self._ALLOWED_REMINDER_CHANNELS:
                raise ClinicalError(
                    f"Invalid opt_in channel {ch!r}; choose from "
                    f"{', '.join(self._ALLOWED_REMINDER_CHANNELS)}."
                )
        contact = await self._s.scalar(
            select(ParticipantContact).where(
                ParticipantContact.participant_access_id == participant_access_id
            )
        )
        if contact is None:
            contact = ParticipantContact(
                participant_access_id=participant_access_id,
                subject_id=access.subject_id,
            )
            self._s.add(contact)
        contact.email = email
        contact.phone = phone
        contact.preferred_channel = preferred_channel
        contact.opt_in_channels_json = json.dumps(opt_in_channels or [])
        if opt_out:
            contact.opt_out_at = datetime.now(UTC)
        else:
            contact.opt_out_at = None
        await self._s.flush()
        self._audit(
            entity_type="participant_contact",
            entity_id=contact.id,
            action="upsert",
            actor_sub=actor_sub,
            new_value=preferred_channel,
        )
        return contact

    async def get_participant_contact(self, subject_id: str) -> ParticipantContact | None:
        result: ParticipantContact | None = await self._s.scalar(
            select(ParticipantContact).where(ParticipantContact.subject_id == subject_id)
        )
        return result

    async def compute_due_reminders(
        self, deployment_id: str, *, now: datetime | None = None
    ) -> list[dict[str, object]]:
        """Read the reminder queue: planned visits × reminder offsets ×
        opted-in participant channels where no SentReminder row exists
        for that (planned_visit, offset, channel) yet AND the reminder
        target time has passed.

        Returns list of dicts the send helper drains. Each dict carries:
          - planned_visit_id, subject_id, channel, offset_days
          - recipient (email or phone)
          - visit_name, planned_date (for the email body)

        The caller is responsible for actually writing the SentReminder row
        + dispatching to the provider.
        """
        from datetime import timedelta

        current = now or datetime.now(UTC)
        # Pull pending planned visits in the future or recent past so we
        # don't iterate the whole history.
        stmt = (
            select(PlannedVisit)
            .where(PlannedVisit.status == "pending")
            .order_by(PlannedVisit.planned_date)
        )
        subject_ids_stmt = select(Subject.id).where(Subject.deployment_id == deployment_id)
        stmt = stmt.where(PlannedVisit.subject_id.in_(subject_ids_stmt))
        planned_visits = list((await self._s.scalars(stmt)).all())
        if not planned_visits:
            return []
        # Bulk-load scheduled visit metadata.
        sv_ids = {pv.scheduled_visit_id for pv in planned_visits}
        sv_rows = await self._s.scalars(select(ScheduledVisit).where(ScheduledVisit.id.in_(sv_ids)))
        sv_by_id = {sv.id: sv for sv in sv_rows}
        # Bulk-load already-sent reminders for these planned visits.
        sent_rows = await self._s.scalars(
            select(SentReminder).where(
                SentReminder.planned_visit_id.in_(pv.id for pv in planned_visits)
            )
        )
        sent_keys = {(sr.planned_visit_id, sr.offset_days, sr.channel) for sr in sent_rows}
        # Bulk-load participant contacts.
        subject_ids = {pv.subject_id for pv in planned_visits}
        contact_rows = await self._s.scalars(
            select(ParticipantContact).where(ParticipantContact.subject_id.in_(subject_ids))
        )
        contact_by_subject: dict[str, ParticipantContact] = {c.subject_id: c for c in contact_rows}
        out: list[dict[str, object]] = []
        for pv in planned_visits:
            contact = contact_by_subject.get(pv.subject_id)
            if contact is None or contact.opt_out_at is not None:
                continue
            try:
                opt_in = json.loads(contact.opt_in_channels_json) or []
            except json.JSONDecodeError:
                opt_in = []
            if not opt_in:
                continue
            sv = sv_by_id.get(pv.scheduled_visit_id)
            if sv is None:
                continue
            try:
                offsets = json.loads(sv.reminder_offsets_json) or []
            except json.JSONDecodeError:
                offsets = []
            for offset in offsets:
                # offset is days BEFORE due_date; target_at = due_date + offset.
                # Normalise to aware-UTC for portability across SQLite test
                # fixtures (which strip tzinfo) and Postgres (which preserves it).
                planned = pv.planned_date
                if planned.tzinfo is None:
                    planned = planned.replace(tzinfo=UTC)
                target_at = planned + timedelta(days=offset)
                if target_at > current:
                    continue
                # Pick first opted-in channel matching preferred order.
                channels: list[str] = []
                if contact.preferred_channel in opt_in:
                    channels.append(contact.preferred_channel)
                channels.extend(c for c in opt_in if c not in channels)
                for channel in channels:
                    if (pv.id, offset, channel) in sent_keys:
                        continue
                    recipient = contact.email if channel == "email" else contact.phone
                    if not recipient:
                        continue
                    out.append(
                        {
                            "planned_visit_id": pv.id,
                            "subject_id": pv.subject_id,
                            "channel": channel,
                            "offset_days": offset,
                            "recipient": recipient,
                            "visit_name": sv.visit_name,
                            "planned_date": pv.planned_date.isoformat(),
                            "target_at": target_at.isoformat(),
                        }
                    )
                    break  # only one channel per (planned_visit, offset)
        return out

    async def record_sent_reminder(
        self,
        *,
        planned_visit_id: str,
        subject_id: str,
        channel: str,
        offset_days: int,
        provider: str,
        status: str,
        recipient: str | None = None,
        error: str | None = None,
    ) -> SentReminder:
        """Write a SentReminder row. Idempotent: returns the existing row
        when the unique (planned_visit_id, offset_days, channel) is hit."""
        existing = await self._s.scalar(
            select(SentReminder).where(
                SentReminder.planned_visit_id == planned_visit_id,
                SentReminder.offset_days == offset_days,
                SentReminder.channel == channel,
            )
        )
        if existing is not None:
            return existing
        row = SentReminder(
            planned_visit_id=planned_visit_id,
            subject_id=subject_id,
            channel=channel,
            offset_days=offset_days,
            provider=provider,
            status=status,
            recipient=recipient,
            error=error,
            sent_at=datetime.now(UTC) if status == "sent" else None,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def list_sent_reminders(
        self,
        *,
        deployment_id: str | None = None,
        subject_id: str | None = None,
        since: datetime | None = None,
    ) -> list[SentReminder]:
        stmt = select(SentReminder).order_by(SentReminder.queued_at.desc())
        if subject_id is not None:
            stmt = stmt.where(SentReminder.subject_id == subject_id)
        elif deployment_id is not None:
            subject_ids_stmt = select(Subject.id).where(Subject.deployment_id == deployment_id)
            stmt = stmt.where(SentReminder.subject_id.in_(subject_ids_stmt))
        if since is not None:
            stmt = stmt.where(SentReminder.queued_at >= since)
        return list((await self._s.scalars(stmt)).all())

    # ── Source-document extraction (P1 #5) ─────────────────────────────

    async def ingest_source_document(
        self,
        deployment_id: str,
        *,
        filename: str,
        raw_bytes: bytes,
        subject_code_field: str | None = None,
        actor_sub: str | None = None,
    ) -> tuple[SourceDocument, int]:
        """Parse a CSV upload + persist SourceDocument + SourceRow rows.

        Content-hash deduplicated: re-uploading the same file in the
        same deployment returns the existing row and `(0, ...)` rows
        added. Returns `(document, rows_added)`.

        `subject_code_field` is OPTIONAL at upload time — if supplied,
        each row's value of that column is denormalised onto
        `SourceRow.subject_code_hint` for fast apply-time lookup.
        """
        deployment = await self._s.get(StudyDeployment, deployment_id)
        if deployment is None:
            raise ClinicalError(f"Deployment {deployment_id!r} not found.")
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        existing = await self._s.scalar(
            select(SourceDocument).where(
                SourceDocument.deployment_id == deployment_id,
                SourceDocument.content_hash == content_hash,
            )
        )
        if existing is not None:
            return existing, 0
        # Parse CSV using stdlib (bounded uploads — keep dependencies light).
        import csv
        import io

        text = raw_bytes.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        headers = list(reader.fieldnames or [])
        if not headers:
            raise ClinicalError("CSV must have a header row (first line of column names).")
        if subject_code_field is not None and subject_code_field not in headers:
            raise ClinicalError(
                f"subject_code_field {subject_code_field!r} not in CSV headers "
                f"({', '.join(headers)})."
            )
        doc = SourceDocument(
            deployment_id=deployment_id,
            filename=filename,
            content_hash=content_hash,
            row_count=0,
            headers_json=json.dumps(headers),
            uploaded_by_sub=actor_sub,
        )
        self._s.add(doc)
        await self._s.flush()
        rows_added = 0
        for idx, row in enumerate(reader):
            payload = {k: (v if v is not None else "") for k, v in row.items()}
            self._s.add(
                SourceRow(
                    source_document_id=doc.id,
                    row_index=idx,
                    payload_json=json.dumps(payload),
                    subject_code_hint=(
                        payload.get(subject_code_field) if subject_code_field else None
                    ),
                )
            )
            rows_added += 1
        doc.row_count = rows_added
        await self._s.flush()
        self._audit(
            entity_type="source_document",
            entity_id=doc.id,
            action="ingest",
            actor_sub=actor_sub,
            new_value=f"rows={rows_added}",
        )
        return doc, rows_added

    async def get_source_document(self, doc_id: str) -> SourceDocument | None:
        return await self._s.get(SourceDocument, doc_id)

    async def list_source_documents(self, deployment_id: str) -> list[SourceDocument]:
        rows = await self._s.scalars(
            select(SourceDocument)
            .where(SourceDocument.deployment_id == deployment_id)
            .order_by(SourceDocument.uploaded_at.desc())
        )
        return list(rows)

    async def list_source_rows(self, doc_id: str) -> list[SourceRow]:
        rows = await self._s.scalars(
            select(SourceRow)
            .where(SourceRow.source_document_id == doc_id)
            .order_by(SourceRow.row_index)
        )
        return list(rows)

    async def create_extraction_mapping(
        self,
        deployment_id: str,
        *,
        deployed_form_id: str,
        name: str = "default",
        subject_code_field: str,
        mapping: dict[str, str],
        notes: str = "",
        actor_sub: str | None = None,
    ) -> ExtractionMapping:
        """Create a new mapping spec. If a mapping for the same
        (deployment, deployed_form) already exists, this CREATES A NEW
        VERSION (auto-bumped) and deactivates the prior one.

        `mapping` is `{source_field: item_id}` — the source CSV column
        name → the form definition's Item id. Empty mappings are
        permitted (for staged authoring) but apply will skip them.
        """
        df = await self._s.get(DeployedForm, deployed_form_id)
        if df is None or df.deployment_id != deployment_id:
            raise ClinicalError(f"Deployed form {deployed_form_id!r} not found in this deployment.")
        prior = await self._s.scalars(
            select(ExtractionMapping).where(
                ExtractionMapping.deployment_id == deployment_id,
                ExtractionMapping.deployed_form_id == deployed_form_id,
            )
        )
        prior_rows = list(prior)
        next_version = max((m.version for m in prior_rows), default=0) + 1
        # Deactivate prior active versions.
        for m in prior_rows:
            if m.is_active:
                m.is_active = False
        new_mapping = ExtractionMapping(
            deployment_id=deployment_id,
            deployed_form_id=deployed_form_id,
            name=name,
            version=next_version,
            subject_code_field=subject_code_field,
            mapping_json=json.dumps(mapping),
            is_active=True,
            notes=notes,
            created_by_sub=actor_sub,
        )
        self._s.add(new_mapping)
        await self._s.flush()
        self._audit(
            entity_type="extraction_mapping",
            entity_id=new_mapping.id,
            action="create",
            actor_sub=actor_sub,
            new_value=f"version={next_version}",
        )
        return new_mapping

    async def get_active_mapping(
        self, deployment_id: str, deployed_form_id: str
    ) -> ExtractionMapping | None:
        result: ExtractionMapping | None = await self._s.scalar(
            select(ExtractionMapping).where(
                ExtractionMapping.deployment_id == deployment_id,
                ExtractionMapping.deployed_form_id == deployed_form_id,
                ExtractionMapping.is_active.is_(True),
            )
        )
        return result

    async def list_extraction_mappings(
        self, deployment_id: str, deployed_form_id: str | None = None
    ) -> list[ExtractionMapping]:
        stmt = select(ExtractionMapping).where(ExtractionMapping.deployment_id == deployment_id)
        if deployed_form_id is not None:
            stmt = stmt.where(ExtractionMapping.deployed_form_id == deployed_form_id)
        stmt = stmt.order_by(ExtractionMapping.deployed_form_id, ExtractionMapping.version.desc())
        return list((await self._s.scalars(stmt)).all())

    async def dry_run_extraction(
        self,
        mapping_id: str,
        *,
        source_document_id: str,
    ) -> list[dict[str, object]]:
        """Return a per-row preview of what `apply_extraction_to_subjects`
        would write. Each entry: `{subject_code, source_row_id,
        proposed_items: [{item_id, source_field, value}]}`. No DB writes.
        """
        mapping = await self._s.get(ExtractionMapping, mapping_id)
        if mapping is None:
            raise ClinicalError(f"Mapping {mapping_id!r} not found.")
        try:
            spec: dict[str, str] = json.loads(mapping.mapping_json) or {}
        except json.JSONDecodeError as e:
            raise ClinicalError(f"Mapping {mapping_id!r} has malformed mapping_json: {e}") from e
        rows = await self.list_source_rows(source_document_id)
        out: list[dict[str, object]] = []
        for row in rows:
            try:
                payload: dict[str, object] = json.loads(row.payload_json) or {}
            except json.JSONDecodeError:
                continue
            subject_code = payload.get(mapping.subject_code_field) or row.subject_code_hint
            items: list[dict[str, object]] = []
            for source_field, item_id in spec.items():
                if source_field not in payload:
                    continue
                items.append(
                    {
                        "item_id": item_id,
                        "source_field": source_field,
                        "value": payload[source_field],
                    }
                )
            out.append(
                {
                    "subject_code": subject_code,
                    "source_row_id": row.id,
                    "proposed_items": items,
                }
            )
        return out

    async def apply_extraction_to_subjects(
        self,
        mapping_id: str,
        *,
        source_document_id: str,
        actor_sub: str | None = None,
    ) -> dict[str, int]:
        """Apply the mapping to every SourceRow in the document that
        matches a Subject in the deployment. Creates a FormInstance (if
        absent) + ItemData rows + ExtractionFill audit rows.

        Returns `{subjects_filled, items_written, source_rows_unmatched}`.
        Idempotent at the (form_instance_id, item_id) level — re-applying
        the same source row to the same subject overwrites existing
        ItemData (the ExtractionFill audit row is still appended so the
        provenance chain stays complete).
        """
        mapping = await self._s.get(ExtractionMapping, mapping_id)
        if mapping is None:
            raise ClinicalError(f"Mapping {mapping_id!r} not found.")
        deployed_form = await self._s.get(DeployedForm, mapping.deployed_form_id)
        if deployed_form is None:
            raise ClinicalError("Deployed form not found.")
        try:
            spec: dict[str, str] = json.loads(mapping.mapping_json) or {}
        except json.JSONDecodeError as e:
            raise ClinicalError(f"Mapping has malformed mapping_json: {e}") from e
        rows = await self.list_source_rows(source_document_id)

        subject_codes = {
            (json.loads(r.payload_json).get(mapping.subject_code_field) or r.subject_code_hint)
            for r in rows
        }
        subject_codes.discard(None)
        subjects_by_code: dict[str, Subject] = {}
        for code in subject_codes:
            if not code:
                continue
            subject = await self._s.scalar(
                select(Subject).where(
                    Subject.deployment_id == mapping.deployment_id,
                    Subject.subject_code == code,
                )
            )
            if subject is not None:
                subjects_by_code[str(code)] = subject

        items_written = 0
        subjects_filled: set[str] = set()
        unmatched = 0
        for row in rows:
            try:
                payload = json.loads(row.payload_json) or {}
            except json.JSONDecodeError:
                unmatched += 1
                continue
            code = payload.get(mapping.subject_code_field) or row.subject_code_hint
            if not code or str(code) not in subjects_by_code:
                unmatched += 1
                continue
            subject = subjects_by_code[str(code)]
            # Find-or-create a FormInstance for this subject + deployed_form.
            fi = await self._s.scalar(
                select(FormInstance).where(
                    FormInstance.subject_id == subject.id,
                    FormInstance.deployed_form_id == deployed_form.id,
                )
            )
            if fi is None:
                fi = FormInstance(
                    subject_id=subject.id,
                    deployed_form_id=deployed_form.id,
                    created_by=actor_sub,
                )
                self._s.add(fi)
                await self._s.flush()
            for source_field, item_id in spec.items():
                if source_field not in payload:
                    continue
                value = str(payload[source_field]) if payload[source_field] is not None else None
                existing_item = await self._s.scalar(
                    select(ItemData).where(
                        ItemData.form_instance_id == fi.id,
                        ItemData.item_id == item_id,
                    )
                )
                if existing_item is None:
                    item = ItemData(
                        form_instance_id=fi.id,
                        item_id=item_id,
                        value=value,
                        entered_by=actor_sub,
                    )
                    self._s.add(item)
                    await self._s.flush()
                else:
                    existing_item.value = value
                    item = existing_item
                self._s.add(
                    ExtractionFill(
                        source_row_id=row.id,
                        source_field=source_field,
                        mapping_id=mapping.id,
                        mapping_version=mapping.version,
                        target_kind="item_data",
                        target_id=item.id,
                        applied_value=value,
                        applied_by_sub=actor_sub,
                    )
                )
                items_written += 1
            subjects_filled.add(subject.id)
        await self._s.flush()
        self._audit(
            entity_type="extraction_mapping",
            entity_id=mapping.id,
            action="apply",
            actor_sub=actor_sub,
            new_value=(
                f"subjects={len(subjects_filled)},items={items_written},unmatched={unmatched}"
            ),
        )
        return {
            "subjects_filled": len(subjects_filled),
            "items_written": items_written,
            "source_rows_unmatched": unmatched,
        }

    async def apply_extraction_to_table(
        self,
        mapping_id: str,
        *,
        source_document_id: str,
        actor_sub: str | None = None,
    ) -> list[dict[str, object]]:
        """Produce a flat extraction-table output for retrospective
        studies / IPD meta-analyses. NO eCRF write — just the per-row
        per-field projection + ExtractionFill audit rows tying each
        cell back to its source.

        Returns a list of dicts: `{subject_code, source_row_id,
        <item_id>: <value>, ...}`. The caller renders or exports.
        """
        mapping = await self._s.get(ExtractionMapping, mapping_id)
        if mapping is None:
            raise ClinicalError(f"Mapping {mapping_id!r} not found.")
        try:
            spec: dict[str, str] = json.loads(mapping.mapping_json) or {}
        except json.JSONDecodeError as e:
            raise ClinicalError(f"Mapping has malformed mapping_json: {e}") from e
        rows = await self.list_source_rows(source_document_id)
        out: list[dict[str, object]] = []
        for row in rows:
            try:
                payload = json.loads(row.payload_json) or {}
            except json.JSONDecodeError:
                continue
            code = payload.get(mapping.subject_code_field) or row.subject_code_hint
            extraction_cell_id = str(_uuid_mod.uuid4())
            cells: dict[str, object] = {
                "subject_code": code,
                "source_row_id": row.id,
                "_extraction_cell_id": extraction_cell_id,
            }
            for source_field, item_id in spec.items():
                if source_field not in payload:
                    continue
                cells[item_id] = payload[source_field]
                self._s.add(
                    ExtractionFill(
                        source_row_id=row.id,
                        source_field=source_field,
                        mapping_id=mapping.id,
                        mapping_version=mapping.version,
                        target_kind="extraction_cell",
                        target_id=extraction_cell_id,
                        applied_value=(
                            str(payload[source_field])
                            if payload[source_field] is not None
                            else None
                        ),
                        applied_by_sub=actor_sub,
                    )
                )
            out.append(cells)
        await self._s.flush()
        self._audit(
            entity_type="extraction_mapping",
            entity_id=mapping.id,
            action="apply_to_table",
            actor_sub=actor_sub,
            new_value=f"rows={len(out)}",
        )
        return out

    async def list_extraction_fills(
        self,
        *,
        deployment_id: str | None = None,
        target_id: str | None = None,
        source_document_id: str | None = None,
    ) -> list[ExtractionFill]:
        stmt = select(ExtractionFill).order_by(ExtractionFill.applied_at.desc())
        if target_id is not None:
            stmt = stmt.where(ExtractionFill.target_id == target_id)
        elif source_document_id is not None:
            row_ids_stmt = select(SourceRow.id).where(
                SourceRow.source_document_id == source_document_id
            )
            stmt = stmt.where(ExtractionFill.source_row_id.in_(row_ids_stmt))
        elif deployment_id is not None:
            mapping_ids_stmt = select(ExtractionMapping.id).where(
                ExtractionMapping.deployment_id == deployment_id
            )
            stmt = stmt.where(ExtractionFill.mapping_id.in_(mapping_ids_stmt))
        return list((await self._s.scalars(stmt)).all())

    # ── Drug accountability (P2 #3) ────────────────────────────────────

    _ALLOWED_RETURN_REASONS = (
        "end_of_visit",
        "end_of_treatment",
        "early_termination",
        "adverse_event",
        "other",
    )

    async def register_investigational_product(
        self,
        deployment_id: str,
        *,
        drug_name: str,
        strength: str,
        units: str = "tablet",
        kit_id_pattern: str | None = None,
        notes: str = "",
        actor_sub: str | None = None,
    ) -> InvestigationalProduct:
        deployment = await self._s.get(StudyDeployment, deployment_id)
        if deployment is None:
            raise ClinicalError(f"Deployment {deployment_id!r} not found.")
        existing = await self._s.scalar(
            select(InvestigationalProduct).where(
                InvestigationalProduct.deployment_id == deployment_id,
                InvestigationalProduct.drug_name == drug_name,
                InvestigationalProduct.strength == strength,
            )
        )
        if existing is not None:
            raise ClinicalError(
                f"IP {drug_name!r} at {strength!r} already registered in this deployment."
            )
        ip = InvestigationalProduct(
            deployment_id=deployment_id,
            drug_name=drug_name,
            strength=strength,
            units=units,
            kit_id_pattern=kit_id_pattern,
            notes=notes,
            created_by_sub=actor_sub,
        )
        self._s.add(ip)
        await self._s.flush()
        self._audit(
            entity_type="investigational_product",
            entity_id=ip.id,
            action="create",
            actor_sub=actor_sub,
            new_value=f"{drug_name} {strength}",
        )
        return ip

    async def list_investigational_products(
        self, deployment_id: str
    ) -> list[InvestigationalProduct]:
        rows = await self._s.scalars(
            select(InvestigationalProduct)
            .where(InvestigationalProduct.deployment_id == deployment_id)
            .order_by(InvestigationalProduct.created_at)
        )
        return list(rows)

    async def record_drug_receipt(
        self,
        deployment_id: str,
        *,
        ip_id: str,
        lot_number: str,
        quantity_received: int,
        site_id: str | None = None,
        expiry_date: datetime | None = None,
        packing_slip_ref: str | None = None,
        temp_excursion_flag: bool = False,
        notes: str = "",
        actor_sub: str | None = None,
    ) -> DrugReceipt:
        if quantity_received <= 0:
            raise ClinicalError("quantity_received must be positive.")
        ip = await self._s.get(InvestigationalProduct, ip_id)
        if ip is None or ip.deployment_id != deployment_id:
            raise ClinicalError(
                f"Investigational product {ip_id!r} not registered in this deployment."
            )
        if site_id is not None:
            site = await self._s.get(Site, site_id)
            if site is None or site.deployment_id != deployment_id:
                raise ClinicalError(f"Site {site_id!r} not in deployment {deployment_id!r}.")
        receipt = DrugReceipt(
            deployment_id=deployment_id,
            site_id=site_id,
            ip_id=ip_id,
            lot_number=lot_number,
            expiry_date=expiry_date,
            quantity_received=quantity_received,
            packing_slip_ref=packing_slip_ref,
            temp_excursion_flag=temp_excursion_flag,
            notes=notes,
            received_by_sub=actor_sub,
        )
        self._s.add(receipt)
        await self._s.flush()
        self._audit(
            entity_type="drug_receipt",
            entity_id=receipt.id,
            action="create",
            actor_sub=actor_sub,
            new_value=(
                f"lot={lot_number},qty={quantity_received}"
                f"{',temp_excursion' if temp_excursion_flag else ''}"
            ),
        )
        return receipt

    async def list_drug_receipts(
        self,
        *,
        deployment_id: str,
        ip_id: str | None = None,
        lot_number: str | None = None,
    ) -> list[DrugReceipt]:
        stmt = (
            select(DrugReceipt)
            .where(DrugReceipt.deployment_id == deployment_id)
            .order_by(DrugReceipt.received_at.desc())
        )
        if ip_id is not None:
            stmt = stmt.where(DrugReceipt.ip_id == ip_id)
        if lot_number is not None:
            stmt = stmt.where(DrugReceipt.lot_number == lot_number)
        return list((await self._s.scalars(stmt)).all())

    async def _lot_inventory(
        self,
        deployment_id: str,
        ip_id: str,
        lot_number: str,
    ) -> int:
        """Running inventory for a (deployment, IP, lot): received +
        returned - dispensed - lost - used."""
        received_stmt = select(func.coalesce(func.sum(DrugReceipt.quantity_received), 0)).where(
            DrugReceipt.deployment_id == deployment_id,
            DrugReceipt.ip_id == ip_id,
            DrugReceipt.lot_number == lot_number,
        )
        dispensed_stmt = select(
            func.coalesce(func.sum(DrugDispensation.quantity_dispensed), 0)
        ).where(
            DrugDispensation.deployment_id == deployment_id,
            DrugDispensation.ip_id == ip_id,
            DrugDispensation.lot_number == lot_number,
        )
        # Returns are joined back into inventory ONLY for the unused +
        # unlost remainder (the bottle came back full enough to redispense).
        return_remainder_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        DrugReturn.quantity_returned
                        - DrugReturn.quantity_used
                        - DrugReturn.quantity_lost
                    ),
                    0,
                )
            )
            .join(
                DrugDispensation,
                DrugReturn.dispensation_id == DrugDispensation.id,
            )
            .where(
                DrugDispensation.deployment_id == deployment_id,
                DrugDispensation.ip_id == ip_id,
                DrugDispensation.lot_number == lot_number,
            )
        )
        received = int(await self._s.scalar(received_stmt) or 0)
        dispensed = int(await self._s.scalar(dispensed_stmt) or 0)
        return_remainder = int(await self._s.scalar(return_remainder_stmt) or 0)
        return received + return_remainder - dispensed

    async def record_drug_dispensation(
        self,
        deployment_id: str,
        *,
        subject_id: str,
        ip_id: str,
        lot_number: str,
        kit_id: str,
        quantity_dispensed: int,
        planned_visit_id: str | None = None,
        notes: str = "",
        actor_sub: str | None = None,
    ) -> DrugDispensation:
        if quantity_dispensed <= 0:
            raise ClinicalError("quantity_dispensed must be positive.")
        subject = await self._s.get(Subject, subject_id)
        if subject is None or subject.deployment_id != deployment_id:
            raise ClinicalError(
                f"Subject {subject_id!r} not found in deployment {deployment_id!r}."
            )
        ip = await self._s.get(InvestigationalProduct, ip_id)
        if ip is None or ip.deployment_id != deployment_id:
            raise ClinicalError(
                f"Investigational product {ip_id!r} not registered in this deployment."
            )
        available = await self._lot_inventory(deployment_id, ip_id, lot_number)
        if available < quantity_dispensed:
            raise ClinicalError(
                f"Cannot dispense {quantity_dispensed} — only {available} "
                f"{ip.units}(s) of lot {lot_number} in inventory."
            )
        dispensation = DrugDispensation(
            deployment_id=deployment_id,
            subject_id=subject_id,
            ip_id=ip_id,
            lot_number=lot_number,
            kit_id=kit_id,
            quantity_dispensed=quantity_dispensed,
            planned_visit_id=planned_visit_id,
            notes=notes,
            dispensed_by_sub=actor_sub,
        )
        self._s.add(dispensation)
        await self._s.flush()
        self._audit(
            entity_type="drug_dispensation",
            entity_id=dispensation.id,
            action="create",
            actor_sub=actor_sub,
            new_value=f"kit={kit_id},qty={quantity_dispensed}",
        )
        return dispensation

    async def list_drug_dispensations(
        self,
        *,
        deployment_id: str | None = None,
        subject_id: str | None = None,
    ) -> list[DrugDispensation]:
        stmt = select(DrugDispensation).order_by(DrugDispensation.dispensed_at.desc())
        if subject_id is not None:
            stmt = stmt.where(DrugDispensation.subject_id == subject_id)
        elif deployment_id is not None:
            stmt = stmt.where(DrugDispensation.deployment_id == deployment_id)
        return list((await self._s.scalars(stmt)).all())

    async def record_drug_return(
        self,
        dispensation_id: str,
        *,
        quantity_returned: int,
        quantity_used: int = 0,
        quantity_lost: int = 0,
        return_reason: str = "end_of_visit",
        notes: str = "",
        actor_sub: str | None = None,
    ) -> DrugReturn:
        if return_reason not in self._ALLOWED_RETURN_REASONS:
            raise ClinicalError(
                f"Invalid return_reason {return_reason!r}; choose from "
                f"{', '.join(self._ALLOWED_RETURN_REASONS)}."
            )
        if quantity_returned < 0 or quantity_used < 0 or quantity_lost < 0:
            raise ClinicalError("Quantities must be non-negative.")
        dispensation = await self._s.get(DrugDispensation, dispensation_id)
        if dispensation is None:
            raise ClinicalError(f"Dispensation {dispensation_id!r} not found.")
        # The used + lost + returned-unopened must not exceed what was
        # dispensed. "returned" is the count the subject brought back at
        # all (full + partial + empty); "used" is the consumed count;
        # "lost" is the missing count; the unopened remainder is
        # `returned - used - lost`.
        if quantity_used + quantity_lost > quantity_returned:
            raise ClinicalError("quantity_used + quantity_lost cannot exceed quantity_returned.")
        if quantity_returned > dispensation.quantity_dispensed:
            raise ClinicalError(
                f"quantity_returned ({quantity_returned}) exceeds dispensed "
                f"quantity ({dispensation.quantity_dispensed})."
            )
        ret = DrugReturn(
            deployment_id=dispensation.deployment_id,
            subject_id=dispensation.subject_id,
            dispensation_id=dispensation_id,
            kit_id=dispensation.kit_id,
            quantity_returned=quantity_returned,
            quantity_used=quantity_used,
            quantity_lost=quantity_lost,
            return_reason=return_reason,
            notes=notes,
            returned_by_sub=actor_sub,
        )
        self._s.add(ret)
        await self._s.flush()
        self._audit(
            entity_type="drug_return",
            entity_id=ret.id,
            action="create",
            actor_sub=actor_sub,
            new_value=(
                f"kit={dispensation.kit_id},"
                f"ret={quantity_returned},used={quantity_used},lost={quantity_lost}"
            ),
        )
        return ret

    async def list_drug_returns(
        self,
        *,
        deployment_id: str | None = None,
        subject_id: str | None = None,
    ) -> list[DrugReturn]:
        stmt = select(DrugReturn).order_by(DrugReturn.returned_at.desc())
        if subject_id is not None:
            stmt = stmt.where(DrugReturn.subject_id == subject_id)
        elif deployment_id is not None:
            stmt = stmt.where(DrugReturn.deployment_id == deployment_id)
        return list((await self._s.scalars(stmt)).all())

    async def drug_reconciliation(
        self,
        deployment_id: str,
    ) -> dict[str, object]:
        """Per (IP, lot) running inventory + activity rollup.

        Returns: {by_lot: {f"{ip_id}|{lot}": {received, dispensed,
        returned, used, lost, current_inventory}}, totals: {...}}.
        Computed in-Python from the receipt/dispensation/return tables
        — portable across SQLite (test fixtures) and Postgres
        (runtime).
        """
        receipts = await self.list_drug_receipts(deployment_id=deployment_id)
        dispensations = await self.list_drug_dispensations(deployment_id=deployment_id)
        returns_with_dispensation = await self._s.execute(
            select(DrugReturn, DrugDispensation)
            .join(
                DrugDispensation,
                DrugReturn.dispensation_id == DrugDispensation.id,
            )
            .where(DrugDispensation.deployment_id == deployment_id)
        )
        by_lot: dict[str, dict[str, int]] = {}

        def _key(ip_id: str, lot: str) -> str:
            return f"{ip_id}|{lot}"

        for r in receipts:
            bucket = by_lot.setdefault(
                _key(r.ip_id, r.lot_number),
                {
                    "received": 0,
                    "dispensed": 0,
                    "returned": 0,
                    "used": 0,
                    "lost": 0,
                    "current_inventory": 0,
                },
            )
            bucket["received"] += int(r.quantity_received)
        for d in dispensations:
            bucket = by_lot.setdefault(
                _key(d.ip_id, d.lot_number),
                {
                    "received": 0,
                    "dispensed": 0,
                    "returned": 0,
                    "used": 0,
                    "lost": 0,
                    "current_inventory": 0,
                },
            )
            bucket["dispensed"] += int(d.quantity_dispensed)
        for ret, disp in returns_with_dispensation.all():
            bucket = by_lot.setdefault(
                _key(disp.ip_id, disp.lot_number),
                {
                    "received": 0,
                    "dispensed": 0,
                    "returned": 0,
                    "used": 0,
                    "lost": 0,
                    "current_inventory": 0,
                },
            )
            bucket["returned"] += int(ret.quantity_returned)
            bucket["used"] += int(ret.quantity_used)
            bucket["lost"] += int(ret.quantity_lost)
        # current_inventory = received + (returned - used - lost) - dispensed
        for bucket in by_lot.values():
            bucket["current_inventory"] = (
                bucket["received"]
                + (bucket["returned"] - bucket["used"] - bucket["lost"])
                - bucket["dispensed"]
            )
        totals = {
            "received": sum(b["received"] for b in by_lot.values()),
            "dispensed": sum(b["dispensed"] for b in by_lot.values()),
            "returned": sum(b["returned"] for b in by_lot.values()),
            "used": sum(b["used"] for b in by_lot.values()),
            "lost": sum(b["lost"] for b in by_lot.values()),
            "current_inventory": sum(b["current_inventory"] for b in by_lot.values()),
        }
        return {
            "deployment_id": deployment_id,
            "totals": totals,
            "by_lot": by_lot,
        }
