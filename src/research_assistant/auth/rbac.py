"""Permission catalogue + role → permission matrix (RBAC-1).

Pure data + a small resolver function. No DB, no FastAPI — the persistence
layer projects `RoleAssignment` rows into `(role, scope_type, scope_id)`
tuples and asks this module what permissions they collectively grant for a
requested resource scope (per `rbac-design.md` §4.1 and §4.5).

The matrix here IS the source of truth referenced by the design doc — keep
the two in sync when adding a permission, a role, or a row.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Final


class Permission(StrEnum):
    """All authorization checks resolve to one of these strings.

    Grouped roughly by subsystem. New permissions go in the matching block
    plus a row (or column) in `ROLE_PERMISSIONS`.
    """

    # ── Specialist skills (gated in the dispatcher) ──────────────────────
    SKILL_META_ANALYSIS = "skill.meta_analysis"
    SKILL_GENERAL_QA = "skill.general_qa"
    SKILL_SEARCH_STRATEGY = "skill.search_strategy"
    SKILL_SR_PROTOCOL = "skill.sr_protocol"
    SKILL_RISK_OF_BIAS = "skill.risk_of_bias"
    SKILL_ECRF_DESIGN = "skill.ecrf_design"

    # ── Library (cached publications + RAG) ──────────────────────────────
    LIBRARY_READ = "library.read"
    LIBRARY_WRITE = "library.write"

    # ── Living-review watches ────────────────────────────────────────────
    WATCH_READ = "watch.read"
    WATCH_MANAGE = "watch.manage"

    # ── eCRF studies / forms ─────────────────────────────────────────────
    STUDY_READ = "study.read"
    STUDY_AUTHOR = "study.author"
    STUDY_PUBLISH = "study.publish"
    STUDY_CREATE = "study.create"
    DEPLOYMENT_MANAGE = "deployment.manage"

    # ── eCRF data capture ────────────────────────────────────────────────
    DATA_READ = "data.read"
    DATA_ENTER = "data.enter"

    # ── eCRF queries / discrepancy management ────────────────────────────
    QUERY_RAISE = "query.raise"
    QUERY_RESPOND = "query.respond"
    QUERY_CLOSE = "query.close"

    # ── eCRF monitoring ──────────────────────────────────────────────────
    SDV_VERIFY = "sdv.verify"

    # ── eCRF forms / casebook lock + signing ─────────────────────────────
    FORM_SIGN = "form.sign"
    FORM_UNLOCK = "form.unlock"
    CASEBOOK_SIGNOFF = "casebook.signoff"
    SUBJECT_UNLOCK = "subject.unlock"

    # ── Audit trail ──────────────────────────────────────────────────────
    AUDIT_READ = "audit.read"

    # ── Platform administration ──────────────────────────────────────────
    USER_MANAGE = "user.manage"
    SOURCE_MANAGE = "source.manage"


class Role(StrEnum):
    """Canonical role names. Free-text role strings stored historically (e.g.
    'data_entry') are mapped onto these via `normalize_legacy_role`.
    """

    # System / research (global scope)
    ADMIN = "admin"
    RESEARCHER = "researcher"
    STUDENT = "student"
    AUDITOR = "auditor"

    # eCRF (study- or site-scoped)
    STUDY_DESIGNER = "study_designer"
    PRINCIPAL_INVESTIGATOR = "principal_investigator"
    COORDINATOR = "coordinator"
    DATA_MANAGER = "data_manager"
    MONITOR = "monitor"


class ScopeType(StrEnum):
    """Per `rbac-design.md` §4.1 — broader scope satisfies narrower checks."""

    GLOBAL = "global"
    STUDY = "study"
    SITE = "site"


# ── Permission groupings used to compose the matrix ──────────────────────


_ALL_PERMS: Final[frozenset[Permission]] = frozenset(Permission)

_EVIDENCE_SKILLS: Final[frozenset[Permission]] = frozenset(
    {
        Permission.SKILL_META_ANALYSIS,
        Permission.SKILL_GENERAL_QA,
        Permission.SKILL_SEARCH_STRATEGY,
        Permission.SKILL_SR_PROTOCOL,
        Permission.SKILL_RISK_OF_BIAS,
    }
)

# Student tier: the meta-analysis workflow plus `general_qa` so that the
# dispatcher's default-fallback path (any message that isn't a workflow
# trigger) doesn't 403 a student typing "hi" or "explain forest plot".
# `general_qa` is hallucination-guarded by `_reject_clinical_synthesis`.
_STUDENT_SKILLS: Final[frozenset[Permission]] = frozenset(
    {Permission.SKILL_META_ANALYSIS, Permission.SKILL_GENERAL_QA}
)


# ── Role → permission matrix (matches rbac-design.md §4.5) ───────────────


ROLE_PERMISSIONS: Final[dict[Role, frozenset[Permission]]] = {
    Role.ADMIN: _ALL_PERMS,
    Role.RESEARCHER: _EVIDENCE_SKILLS
    | frozenset(
        {
            Permission.LIBRARY_READ,
            Permission.LIBRARY_WRITE,
            Permission.WATCH_READ,
            Permission.WATCH_MANAGE,
        }
    ),
    Role.STUDENT: _STUDENT_SKILLS,
    Role.AUDITOR: frozenset(
        {
            Permission.DATA_READ,
            Permission.AUDIT_READ,
            Permission.LIBRARY_READ,
        }
    ),
    Role.STUDY_DESIGNER: frozenset(
        {
            Permission.SKILL_ECRF_DESIGN,
            Permission.STUDY_READ,
            Permission.STUDY_AUTHOR,
            Permission.STUDY_PUBLISH,
            Permission.STUDY_CREATE,
            Permission.DEPLOYMENT_MANAGE,
            Permission.DATA_READ,
        }
    ),
    Role.PRINCIPAL_INVESTIGATOR: frozenset(
        {
            Permission.STUDY_READ,
            Permission.DATA_READ,
            Permission.QUERY_RESPOND,
            Permission.QUERY_CLOSE,
            Permission.FORM_SIGN,
            Permission.CASEBOOK_SIGNOFF,
            Permission.AUDIT_READ,
        }
    ),
    Role.COORDINATOR: frozenset(
        {
            Permission.STUDY_READ,
            Permission.DATA_READ,
            Permission.DATA_ENTER,
            Permission.QUERY_RESPOND,
        }
    ),
    Role.DATA_MANAGER: frozenset(
        {
            Permission.STUDY_READ,
            Permission.DATA_READ,
            Permission.QUERY_RAISE,
            Permission.QUERY_RESPOND,
            Permission.QUERY_CLOSE,
            Permission.FORM_UNLOCK,
            Permission.SUBJECT_UNLOCK,
            Permission.SDV_VERIFY,
            Permission.AUDIT_READ,
        }
    ),
    Role.MONITOR: frozenset(
        {
            Permission.STUDY_READ,
            Permission.DATA_READ,
            Permission.QUERY_RAISE,
            Permission.SDV_VERIFY,
            Permission.AUDIT_READ,
        }
    ),
}


# Legacy role strings (pre-RBAC-1) that have been folded into the catalogue.
# Anything not listed here passes through unchanged; if it doesn't match a
# `Role` value it grants no permissions.
_LEGACY_ROLE_ALIASES: Final[dict[str, Role]] = {
    # eCRF E1 used a flat 'data_entry' role for coordinators. RBAC-1 keeps
    # the alias so existing rows + invitations keep working until they're
    # migrated to scoped `coordinator` assignments in RBAC-2.
    "data_entry": Role.COORDINATOR,
}


def normalize_legacy_role(role: str) -> Role | None:
    """Map a stored role string to a `Role`. Returns None if unknown."""
    if role in _LEGACY_ROLE_ALIASES:
        return _LEGACY_ROLE_ALIASES[role]
    try:
        return Role(role)
    except ValueError:
        return None


def permissions_for_role(role: Role | str) -> frozenset[Permission]:
    """Return the permission set for a role. Unknown role → empty set."""
    if isinstance(role, str):
        resolved = normalize_legacy_role(role)
        if resolved is None:
            return frozenset()
        role = resolved
    return ROLE_PERMISSIONS.get(role, frozenset())


def assignment_applies(
    scope_type: str,
    scope_id: str | None,
    *,
    study_id: str | None,
    site_id: str | None,
) -> bool:
    """True iff an assignment with the given scope satisfies a check at the
    requested `(study_id, site_id)`.

    Semantics (`rbac-design.md` §4.1):

    | assignment scope | global check | study check  | site check  |
    |------------------|--------------|--------------|-------------|
    | global           | ✓            | ✓            | ✓           |
    | study=S          | –            | ✓ iff S==Sₑ  | ✓ iff Sₑ is the study of the requested site |
    | site=T           | –            | –            | ✓ iff T==Tₑ |

    For RBAC-1, callers only ever pass `study_id=None, site_id=None` (global
    checks for skill gating + admin). The study/site arms exist so RBAC-2 can
    drop in eCRF resource→scope resolvers without re-touching this module.
    Note: the "study assignment satisfies a site check whose site belongs to
    that study" semantics requires the caller to provide both `study_id` and
    `site_id` for site-level checks, since this module has no DB access to
    look up the parent study of a site.
    """
    if scope_type == ScopeType.GLOBAL:
        return True
    if scope_type == ScopeType.STUDY:
        return scope_id is not None and scope_id == study_id
    if scope_type == ScopeType.SITE:
        return scope_id is not None and scope_id == site_id
    return False


def effective_permissions(
    assignments: Iterable[tuple[str, str, str | None]],
    *,
    study_id: str | None = None,
    site_id: str | None = None,
) -> frozenset[Permission]:
    """Aggregate the permissions granted by a user's role assignments after
    filtering to those whose scope satisfies the requested resource.

    `assignments` is an iterable of `(role, scope_type, scope_id)` tuples —
    the repo layer projects `RoleAssignment` rows to this shape so this
    module stays DB-agnostic.
    """
    perms: set[Permission] = set()
    for role, scope_type, scope_id in assignments:
        if not assignment_applies(scope_type, scope_id, study_id=study_id, site_id=site_id):
            continue
        perms.update(permissions_for_role(role))
    return frozenset(perms)


# ── Dispatcher gate map (used by agent/dispatcher.py) ────────────────────


# Maps `WORKFLOW_NAME` to the permission a caller must hold globally to be
# routed to that specialist. The mapping mirrors `specialists/__init__.py`
# and must stay in sync — adding a new specialist requires both a new
# `Permission.SKILL_*` and a new entry here.
SKILL_PERMISSION: Final[dict[str, Permission]] = {
    "meta_analysis": Permission.SKILL_META_ANALYSIS,
    "general_qa": Permission.SKILL_GENERAL_QA,
    "search_strategy": Permission.SKILL_SEARCH_STRATEGY,
    "sr_protocol": Permission.SKILL_SR_PROTOCOL,
    "risk_of_bias": Permission.SKILL_RISK_OF_BIAS,
    "ecrf_design": Permission.SKILL_ECRF_DESIGN,
}


__all__ = [
    "Permission",
    "Role",
    "ROLE_PERMISSIONS",
    "SKILL_PERMISSION",
    "ScopeType",
    "assignment_applies",
    "effective_permissions",
    "normalize_legacy_role",
    "permissions_for_role",
]
