"""Pydantic schemas for the registration_drafter specialist.

A trial-registration draft for ClinicalTrials.gov (PRS) and EU CTR
(Clinical Trials Information System). Distinct from the `sr_protocol`
specialist's PROSPERO map: prospective trials register before
enrollment under different IGs and different field surfaces.

Anti-hallucination posture:
  - NCT IDs / EudraCT numbers are assigned BY the registries on
    submission — never invent them. The draft is paste-able into the
    registry portal; the operator gets the ID once the portal accepts
    the record.
  - Site contact details and IRB approval letters are outside the
    platform's data surface — leave as `[SPONSOR INPUT]` placeholders
    rather than fabricating.
  - References for the background blurb come from `web_search` /
    `wikipedia` (no PMID claims).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest

# ── Categorical vocabularies (mapped to CT.gov + EU CTR codelists) ──────


StudyType = Literal[
    "interventional",
    "observational",
    "expanded_access",
]

PrimaryPurpose = Literal[
    "treatment",
    "prevention",
    "diagnostic",
    "supportive_care",
    "screening",
    "health_services_research",
    "basic_science",
    "device_feasibility",
    "other",
]

TrialPhase = Literal[
    "early_phase_1",
    "phase_1",
    "phase_1_2",
    "phase_2",
    "phase_2_3",
    "phase_3",
    "phase_4",
    "not_applicable",
]

AllocationModel = Literal["randomized", "non_randomized", "not_applicable"]

InterventionModel = Literal[
    "single_group",
    "parallel",
    "crossover",
    "factorial",
    "sequential",
]

MaskingModel = Literal[
    "none_open_label",
    "single_participant",
    "single_investigator",
    "double",
    "triple",
    "quadruple",
]

InterventionType = Literal[
    "drug",
    "biological",
    "device",
    "procedure",
    "radiation",
    "behavioral",
    "genetic",
    "dietary_supplement",
    "diagnostic_test",
    "combination_product",
    "other",
]

OutcomeRole = Literal["primary", "secondary", "other_pre_specified"]


# ── Building blocks ─────────────────────────────────────────────────────


class Sponsor(BaseModel):
    name: str
    sponsor_type: Literal["industry", "academic", "government", "individual", "other"] = "academic"
    contact_email: str | None = Field(
        default=None,
        description="Operator-supplied; leave None if the sponsor hasn't provided one.",
    )


class Condition(BaseModel):
    name: str = Field(description="Free-text condition / disease name.")
    mesh_term: str | None = Field(
        default=None,
        description=(
            "MeSH term for the condition. Only populate if the operator "
            "knows it; never guess — the registry can map free-text."
        ),
    )


class Intervention(BaseModel):
    type: InterventionType
    name: str = Field(description="Generic / brand name as the protocol uses it.")
    description: str
    arm_labels: list[str] = Field(
        default_factory=list,
        description="Names of the arms this intervention applies to.",
    )


ArmRole = Literal[
    "experimental",
    "active_comparator",
    "placebo_comparator",
    "sham",
    "no_intervention",
    "other",
]


class Arm(BaseModel):
    label: str
    role: ArmRole
    description: str
    intervention_names: list[str] = Field(default_factory=list)


class Outcome(BaseModel):
    role: OutcomeRole
    measure: str
    description: str
    time_frame: str = Field(
        description="When the outcome is measured (e.g. '12 weeks post-baseline')."
    )


class Eligibility(BaseModel):
    minimum_age: str = Field(description="e.g. '18 Years'. CT.gov accepts free-form age strings.")
    maximum_age: str = Field(default="N/A")
    sexes: Literal["all", "female", "male"] = "all"
    accepts_healthy_volunteers: bool = False
    inclusion_criteria: list[str]
    exclusion_criteria: list[str]


class Location(BaseModel):
    facility_name: str
    city: str
    country: str
    state_or_province: str | None = None
    recruitment_status: Literal[
        "not_yet_recruiting", "recruiting", "active_not_recruiting", "completed", "withdrawn"
    ] = "not_yet_recruiting"


# ── STEP 1 — intake ─────────────────────────────────────────────────────


class RegistrationIntake(BaseModel):
    kind: Literal["registration_intake"] = "registration_intake"
    brief_title: str = Field(description="Short title (≤300 chars per CT.gov).")
    official_title: str = Field(description="Formal title — usually starts with 'A Phase X …'.")
    study_type: StudyType
    primary_purpose: PrimaryPurpose
    phase: TrialPhase
    lead_sponsor: Sponsor
    conditions: list[Condition] = Field(min_length=1)
    brief_summary: str = Field(description="2-4 sentence lay summary for the public listing.")
    notes: str = ""


# ── STEP 2 — core fields ────────────────────────────────────────────────


class CoreFields(BaseModel):
    kind: Literal["core_fields"] = "core_fields"
    allocation: AllocationModel
    intervention_model: InterventionModel
    masking: MaskingModel
    arms: list[Arm] = Field(min_length=1)
    interventions: list[Intervention] = Field(min_length=1)
    primary_outcomes: list[Outcome] = Field(min_length=1)
    secondary_outcomes: list[Outcome] = Field(default_factory=list)
    eligibility: Eligibility
    target_enrollment: int = Field(gt=0)
    enrollment_type: Literal["actual", "anticipated"] = "anticipated"
    locations: list[Location] = Field(default_factory=list)


# ── STEP 3 — registry-specific drafts ───────────────────────────────────


class CtGovDraft(BaseModel):
    """Fields aligned to ClinicalTrials.gov PRS submission shape.

    These are the ~30 most-requested fields the operator pastes into
    the PRS form. The full surface (~120 fields) is bigger but most
    are auto-derived from the ones below or sponsor-fixed.
    """

    kind: Literal["ctgov_draft"] = "ctgov_draft"
    # Identity
    brief_title: str
    official_title: str
    org_study_id: str | None = Field(
        default=None,
        description="Sponsor's internal study id (e.g. 'ABC-2026-001').",
    )
    secondary_ids: list[str] = Field(default_factory=list)

    # Study design
    study_type: StudyType
    primary_purpose: PrimaryPurpose
    phase: TrialPhase
    allocation: AllocationModel
    intervention_model: InterventionModel
    masking: MaskingModel

    # Cohort
    arms: list[Arm]
    interventions: list[Intervention]

    # Outcomes
    primary_outcomes: list[Outcome]
    secondary_outcomes: list[Outcome]

    # Eligibility + recruitment
    eligibility: Eligibility
    target_enrollment: int
    enrollment_type: Literal["actual", "anticipated"]

    # Operational
    locations: list[Location]
    lead_sponsor: Sponsor
    conditions: list[Condition]

    # Sponsor-fixed placeholders
    overall_official_name: str = "[SPONSOR INPUT]"
    overall_official_title: str = "[SPONSOR INPUT]"
    central_contact_email: str = "[SPONSOR INPUT]"

    notes: str = ""


class EuCtrDraft(BaseModel):
    """Fields aligned to the EU Clinical Trials Information System (CTIS).

    CTIS replaced EudraCT in 2022; the field set is similar to CT.gov
    but adds Part I (sponsor) / Part II (member-state) split and
    member-state recruitment per Annex II.
    """

    kind: Literal["euctr_draft"] = "euctr_draft"
    # Identity
    full_title: str
    public_title: str
    sponsor_protocol_code: str | None = None
    iso_basket_codes: list[str] = Field(
        default_factory=list,
        description="ISO 3166-1 alpha-2 codes of the EU member states where the trial runs.",
    )

    # Design — CTIS uses similar terms to CT.gov but with slightly
    # different vocab.
    trial_type: StudyType
    therapeutic_area: str = Field(description="MedDRA SOC if known, otherwise free text.")
    phase: TrialPhase
    allocation: AllocationModel
    intervention_model: InterventionModel
    masking: MaskingModel

    arms: list[Arm]
    interventions: list[Intervention]
    primary_endpoints: list[Outcome]
    secondary_endpoints: list[Outcome] = Field(default_factory=list)

    eligibility: Eligibility
    target_enrollment_eu: int
    target_enrollment_global: int

    sponsor: Sponsor
    conditions: list[Condition]

    # CTIS member-state list
    member_state_locations: list[Location] = Field(default_factory=list)

    # Sponsor-fixed placeholders
    sponsor_contact_email: str = "[SPONSOR INPUT]"
    clinical_trial_qp_email: str = "[SPONSOR INPUT]"

    notes: str = ""


# ── STEP 4 — assembled document ─────────────────────────────────────────


class RegistrationDocument(BaseModel):
    """The final assembled registration draft.

    Iterable — refinements return another RegistrationDocument with
    `is_final=False` until the user types "Finalize registration".
    """

    kind: Literal["registration_document"] = "registration_document"
    intake: RegistrationIntake
    core: CoreFields
    ctgov: CtGovDraft
    euctr: EuCtrDraft
    background_paragraph: str = Field(
        description=(
            "1-2 paragraph rationale citing the disease-area context. "
            "References come from web_search / wikipedia and carry an "
            "origin tag in the report; never inline-cite PMIDs here."
        )
    )
    references: list[str] = Field(default_factory=list)
    is_final: bool = False


# ── Discriminated union for the agent's output ──────────────────────────


RegistrationTurn = Annotated[
    ClarificationRequest
    | RegistrationIntake
    | CoreFields
    | CtGovDraft
    | EuCtrDraft
    | RegistrationDocument,
    Field(discriminator="kind"),
]


__all__ = [
    "AllocationModel",
    "Arm",
    "ClarificationRequest",
    "Condition",
    "CoreFields",
    "CtGovDraft",
    "Eligibility",
    "EuCtrDraft",
    "Intervention",
    "InterventionModel",
    "InterventionType",
    "Location",
    "MaskingModel",
    "Outcome",
    "OutcomeRole",
    "PrimaryPurpose",
    "RegistrationDocument",
    "RegistrationIntake",
    "RegistrationTurn",
    "Sponsor",
    "StudyType",
    "TrialPhase",
]
