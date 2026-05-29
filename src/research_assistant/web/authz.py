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
            perms = await UserRepository(db).effective_permissions_for_sub(
                user.sub,
                study_id=study_id,
                site_id=site_id,
                sr_review_id=sr_review_id,
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
    "resolve_deployment_scope",
    "resolve_ecrf_form_scope",
    "resolve_ecrf_study_scope",
    "resolve_form_instance_scope",
    "resolve_query_scope",
    "resolve_subject_scope",
]
