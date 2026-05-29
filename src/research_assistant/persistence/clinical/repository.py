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
    AdverseEvent,
    AuditEntry,
    CapaAction,
    DeployedForm,
    EventInstance,
    FormInstance,
    ItemData,
    ParticipantAccess,
    ProtocolDeviation,
    Query,
    QueryResponse,
    Signature,
    Site,
    StudyDeployment,
    Subject,
    SubjectSignature,
    Verification,
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
        deadline = compute_reporting_deadline(
            is_serious=is_serious, reported_at=reported_at
        )

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
            raise ClinicalError(
                "classification must be 'major', 'minor', or 'critical'."
            )
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
                raise ClinicalError(
                    "classification must be 'major', 'minor', or 'critical'."
                )
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
        stmt = select(ProtocolDeviation).order_by(
            ProtocolDeviation.discovered_at.desc()
        )
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
            raise ClinicalError(
                "Cannot add CAPA — deviation is closed. Reopen it first."
            )
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

    async def complete_capa(
        self, capa_id: str, *, actor_sub: str | None = None
    ) -> CapaAction:
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
            raise ClinicalError(
                f"Cannot close — {len(open_capas)} CAPA action(s) still open."
            )
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
