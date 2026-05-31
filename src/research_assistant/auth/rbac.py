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
    SKILL_SAP_DRAFTER = "skill.sap_drafter"
    SKILL_MANUSCRIPT_DRAFTER = "skill.manuscript_drafter"
    SKILL_REGISTRATION_DRAFTER = "skill.registration_drafter"
    SKILL_IRB_DRAFTER = "skill.irb_drafter"
    SKILL_CSR_DRAFTER = "skill.csr_drafter"
    SKILL_GRADE_DRAFTER = "skill.grade_drafter"
    SKILL_TRIAL_STATS = "skill.trial_stats"

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

    # ── Study-level lock (E7 — validation pack) ──────────────────────────
    STUDY_LOCK = "study.lock"

    # ── IRT / randomisation (E8 — RCT enabler) ───────────────────────────
    RANDOMIZATION_GENERATE = "randomization.generate"
    RANDOMIZATION_ALLOCATE = "randomization.allocate"
    RANDOMIZATION_READ = "randomization.read"
    RANDOMIZATION_CODEBREAK = "randomization.codebreak"

    # ── eCRF safety subsystem (AE/SAE + protocol deviations + CAPA) ──────
    AE_RECORD = "ae.record"
    AE_CLASSIFY = "ae.classify"
    SAE_REPORT = "sae.report"
    DEVIATION_RECORD = "deviation.record"
    DEVIATION_CLASSIFY = "deviation.classify"
    CAPA_AUTHOR = "capa.author"
    CAPA_CLOSE = "capa.close"

    # ── Recruitment / screening logs (P1 #3) ─────────────────────────────
    SCREENING_RECORD = "screening.record"
    SCREENING_UPDATE = "screening.update"
    SCREENING_READ = "screening.read"

    # ── Visit scheduling + reminders (P1 #4) ─────────────────────────────
    VISIT_SCHEDULE_AUTHOR = "visit_schedule.author"
    VISIT_SCHEDULE_READ = "visit_schedule.read"
    VISIT_UPDATE = "visit.update"
    PARTICIPANT_CONTACT_MANAGE = "participant_contact.manage"
    REMINDER_READ = "reminder.read"
    REMINDER_SEND = "reminder.send"

    # ── CDISC submission pipeline (SDTM → ADaM → TLF) ────────────────────
    CDISC_DERIVE = "cdisc.derive"
    CDISC_READ = "cdisc.read"
    CDISC_EXPORT = "cdisc.export"

    # ── Audit trail ──────────────────────────────────────────────────────
    AUDIT_READ = "audit.read"

    # ── SR screening (project-scoped) ────────────────────────────────────
    SR_CREATE = "sr.create"
    SR_MANAGE = "sr.manage"
    SR_READ = "sr.read"
    SR_SCREEN = "sr.screen"
    SR_ADJUDICATE = "sr.adjudicate"
    SR_AI_ASSIST = "sr.ai_assist"
    PRISMA_READ = "prisma.read"

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

    # SR screening (project-scoped — see ScopeType.SR_REVIEW)
    REVIEWER_1 = "reviewer_1"
    REVIEWER_2 = "reviewer_2"
    ADJUDICATOR = "adjudicator"


class ScopeType(StrEnum):
    """Per `rbac-design.md` §4.1 — broader scope satisfies narrower checks.

    Three hierarchies share the same enum: the eCRF hierarchy
    `global ⊃ study ⊃ site`, plus the SR-screening tree `global ⊃ sr_review`.
    Scopes from different hierarchies don't satisfy each other (a `study`
    grant doesn't help an `sr_review` check) — only `global` does.
    """

    GLOBAL = "global"
    STUDY = "study"
    SITE = "site"
    SR_REVIEW = "sr_review"


# ── Permission groupings used to compose the matrix ──────────────────────


_ALL_PERMS: Final[frozenset[Permission]] = frozenset(Permission)

_EVIDENCE_SKILLS: Final[frozenset[Permission]] = frozenset(
    {
        Permission.SKILL_META_ANALYSIS,
        Permission.SKILL_GENERAL_QA,
        Permission.SKILL_SEARCH_STRATEGY,
        Permission.SKILL_SR_PROTOCOL,
        Permission.SKILL_RISK_OF_BIAS,
        # Prospective-trial design tools live in the same researcher tier
        # as the literature-review skills. Restricted tiers (student) are
        # explicitly excluded.
        Permission.SKILL_SAP_DRAFTER,
        # IMRaD manuscript drafter + reviewer-response loop — composes the
        # other workflows' outputs into a journal-shaped artefact.
        Permission.SKILL_MANUSCRIPT_DRAFTER,
        # Start-up tier: trial-registration drafter (CT.gov + EU CTR /
        # CTIS) and IRB-packet drafter (protocol synopsis + ICF).
        Permission.SKILL_REGISTRATION_DRAFTER,
        Permission.SKILL_IRB_DRAFTER,
        # Analysis-finale tier: CSR (ICH E3) drafter — composes the
        # other workflows' outputs into the regulator-submission
        # Clinical Study Report.
        Permission.SKILL_CSR_DRAFTER,
        # SR/MA submission-tier: GRADE certainty grading + PRISMA 2020
        # reporting checklist — journal-mandated alongside the
        # meta-analysis manuscript.
        Permission.SKILL_GRADE_DRAFTER,
        # Post-lock analysis tier: trial-specific statistical workflow
        # (K-M / log-rank / Cox PH / MMRM / binary / subgroup forest).
        # Composes ADaM datasets into the regulator-readable analysis
        # numbers that feed the CSR Efficacy section.
        Permission.SKILL_TRIAL_STATS,
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
            # Researchers can spin up SR projects and assign reviewers, and
            # see project-level state; the per-project screening permissions
            # come from project membership (reviewer_1/2/adjudicator), not
            # from being a researcher globally.
            Permission.SR_CREATE,
            Permission.SR_MANAGE,
            Permission.SR_READ,
            Permission.SR_AI_ASSIST,
            Permission.PRISMA_READ,
        }
    ),
    Role.STUDENT: _STUDENT_SKILLS,
    Role.AUDITOR: frozenset(
        {
            Permission.DATA_READ,
            Permission.AUDIT_READ,
            Permission.LIBRARY_READ,
            Permission.SR_READ,
            Permission.PRISMA_READ,
            Permission.CDISC_READ,
            Permission.RANDOMIZATION_READ,
            # Recruitment / screening logs: auditor reads, doesn't mutate.
            Permission.SCREENING_READ,
            # Visit schedule + reminders: read-only for audit trails.
            Permission.VISIT_SCHEDULE_READ,
            Permission.REMINDER_READ,
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
            # Visit schedule: designer authors the schedule alongside the
            # form definitions.
            Permission.VISIT_SCHEDULE_AUTHOR,
            Permission.VISIT_SCHEDULE_READ,
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
            # Safety oversight: PI classifies AEs as serious, signs off
            # the IND safety report, and closes deviations once CAPA
            # actions are complete.
            Permission.AE_CLASSIFY,
            Permission.SAE_REPORT,
            Permission.CAPA_CLOSE,
            # CDISC: PI reads derived datasets + signs off the submission
            # bundle. Derivation itself is data-manager territory.
            Permission.CDISC_READ,
            Permission.CDISC_EXPORT,
            # E8 (IRT): PI requests allocation calls + holds the
            # emergency code-break authority. The schedule itself is
            # generated by the data_manager.
            Permission.RANDOMIZATION_ALLOCATE,
            Permission.RANDOMIZATION_READ,
            Permission.RANDOMIZATION_CODEBREAK,
            # Recruitment / screening: PI updates eligibility / consent /
            # enrolment status; reads the log + funnel rollup.
            Permission.SCREENING_UPDATE,
            Permission.SCREENING_READ,
            # Visit schedule + reminders: PI reads schedule + marks
            # visits complete; reads reminder audit trail.
            Permission.VISIT_SCHEDULE_READ,
            Permission.VISIT_UPDATE,
            Permission.REMINDER_READ,
        }
    ),
    Role.COORDINATOR: frozenset(
        {
            Permission.STUDY_READ,
            Permission.DATA_READ,
            Permission.DATA_ENTER,
            Permission.QUERY_RESPOND,
            # Coordinators capture AEs and log deviations at the point of
            # care; classification + closure are higher-tier actions.
            Permission.AE_RECORD,
            Permission.DEVIATION_RECORD,
            # E8 (IRT): coordinators trigger the randomisation call at
            # enrolment. They read the allocation as well (blinded or
            # unblinded per the deployment's blinding state).
            Permission.RANDOMIZATION_ALLOCATE,
            Permission.RANDOMIZATION_READ,
            # Recruitment / screening: coordinators record screenings,
            # update eligibility / consent / enrolment at the point of
            # contact, and read the funnel rollup.
            Permission.SCREENING_RECORD,
            Permission.SCREENING_UPDATE,
            Permission.SCREENING_READ,
            # Visit calendar: coordinator marks visits complete + reschedules
            # with override reason; manages participant contact info for
            # reminders.
            Permission.VISIT_SCHEDULE_READ,
            Permission.VISIT_UPDATE,
            Permission.PARTICIPANT_CONTACT_MANAGE,
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
            # Recruitment / screening: DMs reclassify ineligible cases and
            # read the funnel for data-management oversight. They don't
            # record at point-of-care (coordinator's job).
            Permission.SCREENING_UPDATE,
            Permission.SCREENING_READ,
            # Visit schedule + reminders: DM activates schedules; reads
            # reminder audit trail; triggers manual reminder runs.
            Permission.VISIT_SCHEDULE_AUTHOR,
            Permission.VISIT_SCHEDULE_READ,
            Permission.REMINDER_READ,
            Permission.REMINDER_SEND,
            # Data managers classify deviations + author CAPAs; they also
            # have sae.report so the IND-safety report can be produced
            # outside the PI's signing flow.
            Permission.DEVIATION_CLASSIFY,
            Permission.CAPA_AUTHOR,
            Permission.SAE_REPORT,
            # CDISC: DM runs the derivation pipeline + reads results +
            # exports the submission bundle (paired with PI for the final
            # sign-off; either can produce the download).
            Permission.CDISC_DERIVE,
            Permission.CDISC_READ,
            Permission.CDISC_EXPORT,
            # E7: DM locks the study database for analysis. PI does NOT
            # carry study.lock — separation-of-duties: PI signs off the
            # casebook, DM locks the database afterward.
            Permission.STUDY_LOCK,
            # E8 (IRT): DM generates the randomisation schedule at
            # study-start; allocation calls (at-enrolment) and the
            # code-break (PI emergency) are separate permissions held
            # by other roles.
            Permission.RANDOMIZATION_GENERATE,
            Permission.RANDOMIZATION_READ,
        }
    ),
    Role.MONITOR: frozenset(
        {
            Permission.STUDY_READ,
            Permission.DATA_READ,
            Permission.QUERY_RAISE,
            Permission.SDV_VERIFY,
            Permission.AUDIT_READ,
            # Monitors discover deviations during site visits — they log
            # but don't classify or close.
            Permission.DEVIATION_RECORD,
            # CDISC: monitors verify derived data against source — read
            # only.
            Permission.CDISC_READ,
            # E8: monitors verify allocation events vs source — read only.
            Permission.RANDOMIZATION_READ,
            # Recruitment / screening: monitors read for source-data
            # verification — they don't record or update.
            Permission.SCREENING_READ,
            # Visit schedule + reminders: read-only for SDV.
            Permission.VISIT_SCHEDULE_READ,
            Permission.REMINDER_READ,
        }
    ),
    # SR screening roles — always granted at sr_review scope, never global.
    # Reviewers can screen + read the project. Adjudicator is screen + the
    # tie-break permission. None of them carry `sr.manage` — that's the
    # project creator's (researcher) job.
    Role.REVIEWER_1: frozenset({Permission.SR_READ, Permission.SR_SCREEN, Permission.PRISMA_READ}),
    Role.REVIEWER_2: frozenset({Permission.SR_READ, Permission.SR_SCREEN, Permission.PRISMA_READ}),
    Role.ADJUDICATOR: frozenset(
        {
            Permission.SR_READ,
            Permission.SR_SCREEN,
            Permission.SR_ADJUDICATE,
            Permission.PRISMA_READ,
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
    sr_review_id: str | None = None,
) -> bool:
    """True iff an assignment with the given scope satisfies a check at the
    requested resource scope.

    Three scope hierarchies share this function:

    | assignment scope | global check | study check | site check | sr_review check |
    |------------------|--------------|-------------|------------|-----------------|
    | global           | ✓            | ✓           | ✓          | ✓               |
    | study=S          | –            | ✓ iff S==Sₑ | (parent)   | –               |
    | site=T           | –            | –           | ✓ iff T==Tₑ| –               |
    | sr_review=R      | –            | –           | –          | ✓ iff R==Rₑ     |

    The eCRF and SR hierarchies are independent — a `study` grant does NOT
    satisfy an `sr_review` check (and vice versa). Only `global` crosses.

    Like the eCRF arms, the SR arm requires the caller to provide the
    `sr_review_id` for sr-level resources; this module has no DB access to
    derive it.
    """
    if scope_type == ScopeType.GLOBAL:
        return True
    if scope_type == ScopeType.STUDY:
        return scope_id is not None and scope_id == study_id
    if scope_type == ScopeType.SITE:
        return scope_id is not None and scope_id == site_id
    if scope_type == ScopeType.SR_REVIEW:
        return scope_id is not None and scope_id == sr_review_id
    return False


def effective_permissions(
    assignments: Iterable[tuple[str, str, str | None]],
    *,
    study_id: str | None = None,
    site_id: str | None = None,
    sr_review_id: str | None = None,
) -> frozenset[Permission]:
    """Aggregate the permissions granted by a user's role assignments after
    filtering to those whose scope satisfies the requested resource.

    `assignments` is an iterable of `(role, scope_type, scope_id)` tuples —
    the repo layer projects `RoleAssignment` rows to this shape so this
    module stays DB-agnostic.
    """
    perms: set[Permission] = set()
    for role, scope_type, scope_id in assignments:
        if not assignment_applies(
            scope_type,
            scope_id,
            study_id=study_id,
            site_id=site_id,
            sr_review_id=sr_review_id,
        ):
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
    "sap_drafter": Permission.SKILL_SAP_DRAFTER,
    "manuscript_drafter": Permission.SKILL_MANUSCRIPT_DRAFTER,
    "registration_drafter": Permission.SKILL_REGISTRATION_DRAFTER,
    "irb_drafter": Permission.SKILL_IRB_DRAFTER,
    "csr_drafter": Permission.SKILL_CSR_DRAFTER,
    "grade_drafter": Permission.SKILL_GRADE_DRAFTER,
    "trial_stats": Permission.SKILL_TRIAL_STATS,
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
