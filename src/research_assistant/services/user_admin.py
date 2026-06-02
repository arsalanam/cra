"""Sprint U1 — user-administration regulatory matrices + grant validators.

Pure data + pure functions. No DB, no FastAPI. The router (web/user_admin.py)
composes these against the live RoleAssignment + UserProfile rows.

The matrices here ARE the source of truth referenced by the user-admin UX
(roles catalogue tooltips, conflict warnings, missing-fields nudges).
Keep them in sync with `rbac-design.md` when adding a role or permission.

Regulatory anchors used throughout:
  - 21 CFR Part 11 §11.10(d) — individual accountability + audit
  - 21 CFR Part 11 §11.200    — signature-component requirements
  - 21 CFR §54                — financial disclosure for FDA studies
  - 21 CFR §312.62            — investigator delegation log
  - ICH E6 (R2) §4.1          — investigator qualifications
  - ICH E6 (R2) §4.2.4        — training records
  - ICH E6 (R2) §5.18         — sponsor monitor independence
  - ICH E6 (R2) §5.19         — auditor independence
  - EU CTR (Reg 536/2014) Art. 49 — qualifications + training
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from ..auth.rbac import Role

# ── Profile fields ──────────────────────────────────────────────────────


class ProfileField(StrEnum):
    """Per-User profile fields that REQUIRED_FIELDS references. Mirrors
    the column names on `UserProfile` so the matcher just checks
    `getattr(profile, field.value) is not None`."""

    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    CREDENTIALS = "credentials"
    MEDICAL_LICENSE_NUMBER = "medical_license_number"
    MEDICAL_LICENSE_COUNTRY = "medical_license_country"
    GCP_TRAINING_COMPLETED_DATE = "gcp_training_completed_date"
    CV_URL = "cv_url"
    FINANCIAL_DISCLOSURE_SIGNED_DATE = "financial_disclosure_signed_date"


# ── Required-fields-per-role ────────────────────────────────────────────


# A user holding the given role MUST have these profile fields populated
# before onboarding can complete. Missing fields don't block invite or
# grant — they block the onboarding gate (enforced in U3).
REQUIRED_FIELDS: dict[Role, frozenset[ProfileField]] = {
    Role.PRINCIPAL_INVESTIGATOR: frozenset(
        {
            ProfileField.FIRST_NAME,
            ProfileField.LAST_NAME,
            ProfileField.CREDENTIALS,
            ProfileField.MEDICAL_LICENSE_NUMBER,
            ProfileField.MEDICAL_LICENSE_COUNTRY,
            ProfileField.GCP_TRAINING_COMPLETED_DATE,
            ProfileField.CV_URL,
            ProfileField.FINANCIAL_DISCLOSURE_SIGNED_DATE,
        }
    ),
    Role.COORDINATOR: frozenset(
        {
            ProfileField.FIRST_NAME,
            ProfileField.LAST_NAME,
            ProfileField.GCP_TRAINING_COMPLETED_DATE,
        }
    ),
    Role.DATA_MANAGER: frozenset(
        {
            ProfileField.FIRST_NAME,
            ProfileField.LAST_NAME,
            ProfileField.GCP_TRAINING_COMPLETED_DATE,
        }
    ),
    Role.MONITOR: frozenset(
        {
            ProfileField.FIRST_NAME,
            ProfileField.LAST_NAME,
            ProfileField.GCP_TRAINING_COMPLETED_DATE,
        }
    ),
    Role.AUDITOR: frozenset(
        {
            ProfileField.FIRST_NAME,
            ProfileField.LAST_NAME,
            # Independence attestation captured in U3 (free-text + checkbox).
        }
    ),
    Role.STUDY_DESIGNER: frozenset(
        {
            ProfileField.FIRST_NAME,
            ProfileField.LAST_NAME,
            ProfileField.GCP_TRAINING_COMPLETED_DATE,
        }
    ),
    Role.RESEARCHER: frozenset(
        {ProfileField.FIRST_NAME, ProfileField.LAST_NAME},
    ),
    Role.STUDENT: frozenset(
        {ProfileField.FIRST_NAME, ProfileField.LAST_NAME},
    ),
    Role.ADMIN: frozenset({ProfileField.FIRST_NAME, ProfileField.LAST_NAME}),
    # SR-screening roles — pure-research; no clinical-data exposure.
    Role.REVIEWER_1: frozenset({ProfileField.FIRST_NAME, ProfileField.LAST_NAME}),
    Role.REVIEWER_2: frozenset({ProfileField.FIRST_NAME, ProfileField.LAST_NAME}),
    Role.ADJUDICATOR: frozenset({ProfileField.FIRST_NAME, ProfileField.LAST_NAME}),
}


# ── Separation of duties ────────────────────────────────────────────────


# Pairs of roles that MUST NOT be held by the same person on the same
# study scope. Override is permitted with a rationale string (stored on
# the new RoleAssignment row for auditor inspection).
#
# The pair is an unordered frozenset so (A, B) and (B, A) collapse.
SAME_STUDY_CONFLICTS: dict[frozenset[Role], str] = {
    frozenset({Role.DATA_MANAGER, Role.PRINCIPAL_INVESTIGATOR}): (
        "21 CFR Part 11 §11.10(d) + ICH E6 §1.27 — the Data Manager "
        "locks the database and the Principal Investigator signs the "
        "casebook; they must be distinct individuals to preserve the "
        "audit + accountability separation."
    ),
    frozenset({Role.MONITOR, Role.COORDINATOR}): (
        "ICH E6 §5.18 — the sponsor monitor verifies what site staff "
        "did; a site coordinator (site-staff) cannot also monitor "
        "their own work."
    ),
    frozenset({Role.MONITOR, Role.PRINCIPAL_INVESTIGATOR}): (
        "ICH E6 §5.18 — sponsor monitor must be independent from the site investigator."
    ),
    frozenset({Role.AUDITOR, Role.PRINCIPAL_INVESTIGATOR}): (
        "ICH E6 §5.19.1 — the auditor must be independent from the "
        "clinical trial / system being audited."
    ),
    frozenset({Role.AUDITOR, Role.DATA_MANAGER}): (
        "ICH E6 §5.19.1 — auditor independence from the team being audited."
    ),
    frozenset({Role.AUDITOR, Role.MONITOR}): (
        "ICH E6 §5.19.1 — auditor independence from the team being audited."
    ),
    frozenset({Role.AUDITOR, Role.COORDINATOR}): (
        "ICH E6 §5.19.1 — auditor independence from the team being audited."
    ),
}


# ── Role catalogue (drives U2 tooltips + onboarding copy) ──────────────


@dataclass(frozen=True, slots=True)
class RoleDescriptor:
    role: Role
    label: str
    description: str
    regulatory_basis: str
    typical_scope_types: tuple[str, ...]
    required_fields: frozenset[ProfileField]


ROLE_CATALOGUE: tuple[RoleDescriptor, ...] = (
    RoleDescriptor(
        role=Role.PRINCIPAL_INVESTIGATOR,
        label="Principal Investigator (PI)",
        description=(
            "Medically qualified individual responsible for the conduct "
            "of the clinical trial at a site. Signs the casebook (Part "
            "11 §11.50), reviews + classifies adverse events, and "
            "delegates trial-related duties to qualified team members."
        ),
        regulatory_basis="ICH E6 §4.1; 21 CFR §312.60 + §312.62; EU CTR Art. 49",
        typical_scope_types=("study", "site"),
        required_fields=REQUIRED_FIELDS[Role.PRINCIPAL_INVESTIGATOR],
    ),
    RoleDescriptor(
        role=Role.COORDINATOR,
        label="Clinical Research Coordinator (CRC)",
        description=(
            "Site staff who performs day-to-day trial activities under "
            "the PI's delegation: enrolment, consent, data entry, "
            "scheduling, source-document maintenance."
        ),
        regulatory_basis="ICH E6 §4.1.5 (PI delegation); 21 CFR §312.62 (delegation log)",
        typical_scope_types=("study", "site"),
        required_fields=REQUIRED_FIELDS[Role.COORDINATOR],
    ),
    RoleDescriptor(
        role=Role.DATA_MANAGER,
        label="Data Manager (DM)",
        description=(
            "Sponsor / CRO staff responsible for database integrity, "
            "query resolution, edit-check authoring, and the final "
            "study-level database lock (E7)."
        ),
        regulatory_basis="ICH E6 §5.5; Part 11 §11.10",
        typical_scope_types=("study",),
        required_fields=REQUIRED_FIELDS[Role.DATA_MANAGER],
    ),
    RoleDescriptor(
        role=Role.MONITOR,
        label="Clinical Research Associate (Monitor)",
        description=(
            "Sponsor-side monitor — performs source-data verification "
            "(SDV), oversees site compliance, generates monitoring "
            "reports. Must be independent from site staff."
        ),
        regulatory_basis="ICH E6 §5.18; Part 11 audit trail review",
        typical_scope_types=("study", "site"),
        required_fields=REQUIRED_FIELDS[Role.MONITOR],
    ),
    RoleDescriptor(
        role=Role.AUDITOR,
        label="Auditor (independent QA)",
        description=(
            "Independent quality-assurance role — reviews monitor + "
            "investigator actions for compliance. Cannot hold any "
            "other role on the same study."
        ),
        regulatory_basis="ICH E6 §5.19",
        typical_scope_types=("study", "global"),
        required_fields=REQUIRED_FIELDS[Role.AUDITOR],
    ),
    RoleDescriptor(
        role=Role.STUDY_DESIGNER,
        label="Study Designer",
        description=(
            "Authors CRFs, edit checks, and visit schedules in the "
            "Form Builder (X1). Read-write on study definitions only — "
            "no patient-data access."
        ),
        regulatory_basis="CDASH 2.0; ICH E6 §6 (protocol design)",
        typical_scope_types=("study", "global"),
        required_fields=REQUIRED_FIELDS[Role.STUDY_DESIGNER],
    ),
    RoleDescriptor(
        role=Role.RESEARCHER,
        label="Researcher",
        description=(
            "Evidence-synthesis + analysis tier: meta-analysis, NMA, "
            "IPD, SAP drafting, CSR drafting, manuscript writing, "
            "trial registration drafting. No clinical-data write."
        ),
        regulatory_basis="N/A (research-tier; no Part 11 / E6 obligation)",
        typical_scope_types=("global",),
        required_fields=REQUIRED_FIELDS[Role.RESEARCHER],
    ),
    RoleDescriptor(
        role=Role.STUDENT,
        label="Student",
        description=(
            "Restricted research-tier — Q&A + library only; no "
            "drafting + no clinical access. Designed for teaching "
            "deployments."
        ),
        regulatory_basis="N/A (teaching tier)",
        typical_scope_types=("global",),
        required_fields=REQUIRED_FIELDS[Role.STUDENT],
    ),
    RoleDescriptor(
        role=Role.ADMIN,
        label="Platform Administrator",
        description=(
            "Org-wide configuration: user management, source-config, "
            "validation pack download, portfolio-org rollup. Should "
            "be a small number of individuals per ICH E6 §5.2."
        ),
        regulatory_basis="Part 11 §11.10(d) (individual accountability)",
        typical_scope_types=("global",),
        required_fields=REQUIRED_FIELDS[Role.ADMIN],
    ),
)


# ── Pure-fn validators ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GrantConflict:
    """A separation-of-duties conflict detected for a proposed grant."""

    existing_role: Role
    proposed_role: Role
    scope_type: str
    scope_id: str | None
    rationale: str


def _normalise(role_str: str) -> Role | None:
    """Map a free-text role string to the canonical Role enum. Returns
    None for unknown values so callers can 422 with a list."""
    try:
        return Role(role_str)
    except ValueError:
        return None


def detect_grant_conflicts(
    *,
    existing_assignments: Iterable[tuple[str, str, str | None]],
    proposed_role: str,
    proposed_scope_type: str,
    proposed_scope_id: str | None,
) -> list[GrantConflict]:
    """Find every existing assignment that conflicts with the proposed
    one under SAME_STUDY_CONFLICTS.

    `existing_assignments` is an iterable of `(role, scope_type, scope_id)`
    tuples — typically the projection of RoleAssignment rows for the
    target user.

    Conflicts are scoped: a `study` proposal collides with an existing
    `study` grant ONLY when scope_id matches. `site` collisions are not
    auto-detected (a site-scoped DM grant doesn't conflict with a study-
    scoped PI grant — different operational layer); the admin UI can
    surface a soft warning.

    Returns an empty list when the proposal is clean.
    """
    target = _normalise(proposed_role)
    if target is None:
        return []
    if proposed_scope_type != "study" or proposed_scope_id is None:
        return []
    conflicts: list[GrantConflict] = []
    for existing_role_str, existing_scope_type, existing_scope_id in existing_assignments:
        if existing_scope_type != "study" or existing_scope_id != proposed_scope_id:
            continue
        existing_role = _normalise(existing_role_str)
        if existing_role is None or existing_role == target:
            continue
        pair = frozenset({existing_role, target})
        rationale = SAME_STUDY_CONFLICTS.get(pair)
        if rationale is None:
            continue
        conflicts.append(
            GrantConflict(
                existing_role=existing_role,
                proposed_role=target,
                scope_type=proposed_scope_type,
                scope_id=proposed_scope_id,
                rationale=rationale,
            )
        )
    return conflicts


def missing_required_fields(
    *,
    role: str,
    profile: object | None,
) -> list[ProfileField]:
    """For a given role + profile (UserProfile row, or None), return the
    list of required fields the profile is missing. Empty list means
    onboarding can complete.

    `profile=None` collapses to "every required field is missing".
    """
    canonical = _normalise(role)
    if canonical is None:
        return []
    required = REQUIRED_FIELDS.get(canonical, frozenset())
    if profile is None:
        return sorted(required, key=lambda f: f.value)
    missing: list[ProfileField] = []
    for field in required:
        if getattr(profile, field.value, None) in (None, ""):
            missing.append(field)
    return sorted(missing, key=lambda f: f.value)


def list_known_roles() -> tuple[str, ...]:
    """Convenience for input validation in DTOs / endpoints."""
    return tuple(r.value for r in Role)
