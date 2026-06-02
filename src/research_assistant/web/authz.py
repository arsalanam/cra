"""Resource→scope resolvers + scoped `require_permission` plumbing (RBAC-2).

The simple `require_permission(perm)` in `web/auth.py` answers the global
case (skill gating, admin-only operations). RBAC-2 needs the scoped case:
"may this user `data.enter` on FORM-INSTANCE X?" requires resolving X
to its `(study_id, site_id)` via the clinical store, then asking the
user's RoleAssignment rows whether any of them grant `data.enter` at a
scope that covers it.

Resolvers live here rather than in `auth/rbac.py` because they touch the
clinical-data DB (which `auth/rbac.py` deliberately doesn't import — that
module is pure data/logic).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, HTTPException, Request

from ..auth import SessionPayload
from ..auth.rbac import Permission
from ..config import get_settings
from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.models import FormInstance, StudyDeployment, Subject
from ..persistence.database import get_db_session
from ..persistence.user_repository import UserRepository
from .auth import CurrentUser

logger = logging.getLogger(__name__)


# Unified scope tuple: `(study_id, site_id, sr_review_id)`. Each slot is
# None when the resource doesn't pin that hierarchy. eCRF resolvers fill
# the first two; SR resolvers fill the third. Same shape across both lets
# `require_permission_scoped` route everything through one code path.
Scope = tuple[str | None, str | None, str | None]


# ── Resolvers ────────────────────────────────────────────────────────────


async def resolve_deployment_scope(deployment_id: str) -> Scope:
    """Deployment lives at study scope only (no site context until a
    subject is named). Returns the underlying research_study_id.
    """
    async with get_clinical_session() as s:
        deployment = await s.get(StudyDeployment, deployment_id)
        if deployment is None:
            raise HTTPException(404, "Deployment not found")
        return (deployment.research_study_id, None, None)


async def resolve_subject_scope(subject_id: str) -> Scope:
    """Subject → (deployment.research_study_id, site_id)."""
    async with get_clinical_session() as s:
        subject = await s.get(Subject, subject_id)
        if subject is None:
            raise HTTPException(404, "Subject not found")
        deployment = await s.get(StudyDeployment, subject.deployment_id)
        study_id = deployment.research_study_id if deployment else None
        return (study_id, subject.site_id, None)


async def resolve_form_instance_scope(form_instance_id: str) -> Scope:
    """FormInstance → its subject's (research_study_id, site_id).

    The clinical store has no FK to the research-app study, so we walk
    form_instance → subject → deployment.research_study_id and pull
    subject.site_id directly.
    """
    async with get_clinical_session() as s:
        fi = await s.get(FormInstance, form_instance_id)
        if fi is None:
            raise HTTPException(404, "Form instance not found")
        subject = await s.get(Subject, fi.subject_id)
        if subject is None:
            # Orphaned form instance — shouldn't happen because of the FK
            # cascade, but guard anyway so we never accidentally satisfy
            # any role with a degraded scope.
            raise HTTPException(409, "Form instance has no subject context")
        deployment = await s.get(StudyDeployment, subject.deployment_id)
        study_id = deployment.research_study_id if deployment else None
        return (study_id, subject.site_id, None)


async def resolve_query_scope(query_id: str) -> Scope:
    """Query (discrepancy) row → its form_instance's scope."""
    from ..persistence.clinical.models import Query as ClinicalQuery

    async with get_clinical_session() as s:
        query = await s.get(ClinicalQuery, query_id)
        if query is None:
            raise HTTPException(404, "Query not found")
        return await resolve_form_instance_scope(query.form_instance_id)


async def resolve_adverse_event_scope(ae_id: str) -> Scope:
    """AdverseEvent → its subject's scope (top-6 #4 safety subsystem)."""
    from ..persistence.clinical.models import AdverseEvent

    async with get_clinical_session() as s:
        ae = await s.get(AdverseEvent, ae_id)
        if ae is None:
            raise HTTPException(404, "Adverse event not found")
        return await resolve_subject_scope(ae.subject_id)


async def resolve_deviation_scope(deviation_id: str) -> Scope:
    """ProtocolDeviation → its subject's scope, or the deployment scope
    when the deviation is subject-less (e.g. a central-supply event)."""
    from ..persistence.clinical.models import ProtocolDeviation

    async with get_clinical_session() as s:
        dev = await s.get(ProtocolDeviation, deviation_id)
        if dev is None:
            raise HTTPException(404, "Deviation not found")
        if dev.subject_id is not None:
            return await resolve_subject_scope(dev.subject_id)
        return await resolve_deployment_scope(dev.deployment_id)


async def resolve_capa_scope(capa_id: str) -> Scope:
    """CapaAction → its parent deviation's scope."""
    from ..persistence.clinical.models import CapaAction

    async with get_clinical_session() as s:
        capa = await s.get(CapaAction, capa_id)
        if capa is None:
            raise HTTPException(404, "CAPA action not found")
        return await resolve_deviation_scope(capa.deviation_id)


async def resolve_screening_log_scope(log_id: str) -> Scope:
    """ScreeningLog → (deployment_id, site_id, None). Site is included so
    site-scoped coordinators can be granted SCREENING_UPDATE without
    cross-site reach (the rbac matrix grants update at deployment scope
    today, but the site lift gives the design room to tighten later)."""
    from ..persistence.clinical.models import ScreeningLog

    async with get_clinical_session() as s:
        log = await s.get(ScreeningLog, log_id)
        if log is None:
            raise HTTPException(404, "Screening log not found")
        return (log.deployment_id, log.site_id, None)


async def resolve_visit_schedule_scope(schedule_id: str) -> Scope:
    """VisitSchedule → its deployment scope (P1 #4)."""
    from ..persistence.clinical.models import VisitSchedule

    async with get_clinical_session() as s:
        schedule = await s.get(VisitSchedule, schedule_id)
        if schedule is None:
            raise HTTPException(404, "Visit schedule not found")
        return await resolve_deployment_scope(schedule.deployment_id)


async def resolve_planned_visit_scope(planned_visit_id: str) -> Scope:
    """PlannedVisit → its subject's scope (P1 #4)."""
    from ..persistence.clinical.models import PlannedVisit

    async with get_clinical_session() as s:
        pv = await s.get(PlannedVisit, planned_visit_id)
        if pv is None:
            raise HTTPException(404, "Planned visit not found")
        return await resolve_subject_scope(pv.subject_id)


async def resolve_participant_access_scope(access_id: str) -> Scope:
    """ParticipantAccess → its subject's scope (P1 #4)."""
    from ..persistence.clinical.models import ParticipantAccess

    async with get_clinical_session() as s:
        access = await s.get(ParticipantAccess, access_id)
        if access is None:
            raise HTTPException(404, "Participant access not found")
        return await resolve_subject_scope(access.subject_id)


async def resolve_source_document_scope(doc_id: str) -> Scope:
    """SourceDocument → its deployment scope (P1 #5)."""
    from ..persistence.clinical.models import SourceDocument

    async with get_clinical_session() as s:
        doc = await s.get(SourceDocument, doc_id)
        if doc is None:
            raise HTTPException(404, "Source document not found")
        return await resolve_deployment_scope(doc.deployment_id)


async def resolve_extraction_mapping_scope(mapping_id: str) -> Scope:
    """ExtractionMapping → its deployment scope (P1 #5)."""
    from ..persistence.clinical.models import ExtractionMapping

    async with get_clinical_session() as s:
        mapping = await s.get(ExtractionMapping, mapping_id)
        if mapping is None:
            raise HTTPException(404, "Extraction mapping not found")
        return await resolve_deployment_scope(mapping.deployment_id)


async def resolve_ip_scope(ip_id: str) -> Scope:
    """InvestigationalProduct → its deployment scope (P2 #3)."""
    from ..persistence.clinical.models import InvestigationalProduct

    async with get_clinical_session() as s:
        ip = await s.get(InvestigationalProduct, ip_id)
        if ip is None:
            raise HTTPException(404, "Investigational product not found")
        return await resolve_deployment_scope(ip.deployment_id)


async def resolve_dispensation_scope(dispensation_id: str) -> Scope:
    """DrugDispensation → its subject's scope (P2 #3). Used by the return
    endpoint so site-scoped coordinators can only return kits they
    dispensed."""
    from ..persistence.clinical.models import DrugDispensation

    async with get_clinical_session() as s:
        disp = await s.get(DrugDispensation, dispensation_id)
        if disp is None:
            raise HTTPException(404, "Drug dispensation not found")
        return await resolve_subject_scope(disp.subject_id)


async def resolve_lab_batch_scope(batch_id: str) -> Scope:
    """LabBatch → its deployment scope (P2 #6)."""
    from ..persistence.clinical.models import LabBatch

    async with get_clinical_session() as s:
        batch = await s.get(LabBatch, batch_id)
        if batch is None:
            raise HTTPException(404, "Lab batch not found")
        return await resolve_deployment_scope(batch.deployment_id)


# ecrf StudyOut / FormOut path params resolve straight to (study_id, None).
async def resolve_ecrf_study_scope(study_id: str) -> Scope:
    return (study_id, None, None)


async def resolve_ecrf_form_scope(form_id: str) -> Scope:
    """Form definition belongs to an EcrfStudy (in the research DB)."""
    from ..persistence.models import EcrfFormDefinition

    async with get_db_session() as s:
        form = await s.get(EcrfFormDefinition, form_id)
        if form is None:
            raise HTTPException(404, "Form not found")
        return (form.study_id, None, None)


# SR-review resolvers — parallel scope namespace from eCRF. A researcher
# scoped to study X does NOT accidentally satisfy an sr_review check at X
# because the rbac module compares scope_type strictly.


async def resolve_sr_project_scope(project_id: str) -> Scope:
    """SR project id IS its own scope id."""
    return (None, None, project_id)


async def resolve_sr_candidate_scope(candidate_id: str) -> Scope:
    """SrCandidate → its parent project's sr_review scope."""
    from ..persistence.models import SrCandidate

    async with get_db_session() as s:
        cand = await s.get(SrCandidate, candidate_id)
        if cand is None:
            raise HTTPException(404, "SR candidate not found")
        return (None, None, cand.sr_review_id)


# Path-param-name → resolver. The require_permission factory below uses
# this to fetch the right resource id off the request, run it through the
# resolver, and pass the resulting scope to the rbac module.
ResolverFn = Callable[[str], Awaitable[Scope]]

RESOURCE_RESOLVERS: dict[str, ResolverFn] = {
    "deployment_id": resolve_deployment_scope,
    "subject_id": resolve_subject_scope,
    "form_instance_id": resolve_form_instance_scope,
    "query_id": resolve_query_scope,
    "study_id": resolve_ecrf_study_scope,
    "form_id": resolve_ecrf_form_scope,
    "sr_project_id": resolve_sr_project_scope,
    "sr_candidate_id": resolve_sr_candidate_scope,
    "ae_id": resolve_adverse_event_scope,
    "deviation_id": resolve_deviation_scope,
    "capa_id": resolve_capa_scope,
    "log_id": resolve_screening_log_scope,
    "schedule_id": resolve_visit_schedule_scope,
    "planned_visit_id": resolve_planned_visit_scope,
    "access_id": resolve_participant_access_scope,
    "doc_id": resolve_source_document_scope,
    "mapping_id": resolve_extraction_mapping_scope,
    "ip_id": resolve_ip_scope,
    "dispensation_id": resolve_dispensation_scope,
    "batch_id": resolve_lab_batch_scope,
}


# ── Scoped require_permission ────────────────────────────────────────────


def require_permission_scoped(
    perm: Permission,
    *,
    resource_param: str | None = None,
) -> Any:
    """Build a FastAPI dependency that requires `perm` at the scope
    derived from the request path.

    `resource_param` names a path parameter on the route (e.g.
    `"form_instance_id"`); the resolver registered for that name fetches
    the resource and returns its `(study_id, site_id)` scope. The user
    must hold a role that grants `perm` at the global level, the resolved
    study, or (for site-level resources) the resolved site.

    `resource_param=None` ⇒ global check, equivalent to the
    `require_permission(perm)` in `web/auth.py`.

    Auth-disabled short-circuits open, mirroring `current_user`.
    """
    resolver = RESOURCE_RESOLVERS.get(resource_param) if resource_param else None
    if resource_param is not None and resolver is None:
        raise RuntimeError(
            f"No resolver registered for path param {resource_param!r}. "
            f"Add one to web.authz.RESOURCE_RESOLVERS."
        )

    async def _dep(request: Request, user: CurrentUser) -> SessionPayload:
        from ..persistence.user_admin_repository import UserAdminRepository
        from ..services.user_admin import CLINICAL_WRITE_PERMS, is_onboarded

        settings = get_settings()
        if not settings.auth_enabled:
            return user

        study_id: str | None = None
        site_id: str | None = None
        sr_review_id: str | None = None
        if resolver is not None:
            assert resource_param is not None
            raw = request.path_params.get(resource_param)
            if raw is None:
                raise HTTPException(
                    500,
                    f"Route is missing the {resource_param!r} path parameter "
                    f"that require_permission_scoped tried to resolve.",
                )
            study_id, site_id, sr_review_id = await resolver(str(raw))

        async with get_db_session() as db:
            user_repo = UserRepository(db)
            perms = await user_repo.effective_permissions_for_sub(
                user.sub,
                study_id=study_id,
                site_id=site_id,
                sr_review_id=sr_review_id,
            )
            # Sprint U3 — onboarding gate. When the caller is going for a
            # clinical-write permission, also verify they've completed
            # onboarding (profile filled + Complete clicked). Read-only
            # perms are unaffected; admin / research-tier work continues
            # for anyone who has the role.
            if perm in CLINICAL_WRITE_PERMS:
                local = await user_repo.get_by_sub(user.sub)
                if local is not None:
                    admin_repo = UserAdminRepository(db)
                    profile = await admin_repo.get_profile(local.id)
                    if not is_onboarded(profile):
                        raise HTTPException(
                            status_code=403,
                            detail=(
                                "Onboarding required — complete your profile at "
                                "/onboarding.html before performing clinical-data "
                                "actions. (ICH E6 §4.2.4; 21 CFR Part 11 §11.10(d))"
                            ),
                        )
        if perm not in perms:
            scope_bits = []
            if study_id:
                scope_bits.append(f"study={study_id}")
            if site_id:
                scope_bits.append(f"site={site_id}")
            if sr_review_id:
                scope_bits.append(f"sr_review={sr_review_id}")
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Permission required: {perm.value} ({', '.join(scope_bits)})."
                    if scope_bits
                    else f"Permission required: {perm.value}."
                ),
            )
        return user

    return Depends(_dep)


__all__ = [
    "RESOURCE_RESOLVERS",
    "Scope",
    "require_permission_scoped",
    "resolve_adverse_event_scope",
    "resolve_capa_scope",
    "resolve_deployment_scope",
    "resolve_deviation_scope",
    "resolve_ecrf_form_scope",
    "resolve_ecrf_study_scope",
    "resolve_form_instance_scope",
    "resolve_query_scope",
    "resolve_subject_scope",
]
