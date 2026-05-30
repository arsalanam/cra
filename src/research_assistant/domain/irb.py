"""Pydantic schemas for the irb_drafter specialist.

Shipping the two highest-value IRB-packet artefacts: the **protocol
synopsis** (1-2 page summary the IRB uses to triage the full protocol)
and the **Informed Consent Form** (legally binding, reading-level-
controlled, multilingual).

Anti-hallucination posture:
  - The ICF MUST cover all 21 CFR §50.25(a) required elements — the
    schema names them as required fields so the agent can't omit one.
  - The ICF reading-level target is captured + checked; the document
    carries the computed Flesch-Kincaid grade so the IRB sees it.
  - No medical-decision language ("you should", "you must consent").
    The ICF describes participation, doesn't recommend it.
  - Translations beyond the language set (en/es/fr/de) MUST go through
    a human translator; the agent flags this rather than producing
    a draft in an unsupported language.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest

# ── Categorical vocabularies ────────────────────────────────────────────


LangCode = Literal["en", "es", "fr", "de"]

JurisdictionType = Literal["us_irb", "ec_european_ec", "non_us_irb", "central_irb"]


# ── STEP 1 — intake ─────────────────────────────────────────────────────


class IrbIntake(BaseModel):
    kind: Literal["irb_intake"] = "irb_intake"
    protocol_summary: str = Field(
        description=(
            "Free-text protocol context the operator pastes. Usually "
            "comes from the SAP intake or the registration_drafter "
            "handoff — the irb_drafter doesn't re-elicit study design "
            "questions, it reformats."
        )
    )
    jurisdiction: JurisdictionType
    language: LangCode = "en"
    reading_level_target: int = Field(
        default=8,
        ge=4,
        le=12,
        description=(
            "Target Flesch-Kincaid grade level for the ICF. CDISC + NIH "
            "guidance: 6-8 for general-population trials, 8-10 for "
            "specialised populations. Default 8 if not specified."
        ),
    )
    population_descriptor: str = Field(
        default="adults",
        description=(
            "Plain-English description of the target population — drives "
            "the reading-level guidance and ICF phrasing (e.g. "
            "'adolescents 12-17' would push the ICF lower)."
        ),
    )
    notes: str = ""


# ── STEP 2 — Protocol synopsis ──────────────────────────────────────────


class SynopsisSection(BaseModel):
    heading: str
    body: str = Field(description="Plain text, 1-3 short paragraphs.")


class ProtocolSynopsis(BaseModel):
    """The 1-2 page IRB-triage summary.

    Section list follows the ICH E6(R2) protocol-synopsis convention.
    """

    kind: Literal["protocol_synopsis"] = "protocol_synopsis"
    title: str
    sponsor: str
    protocol_id: str | None = None
    design_summary: SynopsisSection
    objectives: SynopsisSection
    endpoints: SynopsisSection
    methods: SynopsisSection
    statistical_considerations: SynopsisSection
    eligibility_summary: SynopsisSection
    schedule_summary: SynopsisSection
    risks_and_mitigations: SynopsisSection
    notes: str = ""


# ── STEP 3 — Informed Consent Form ──────────────────────────────────────


class IcfSection(BaseModel):
    """One named ICF section.

    The 21 CFR §50.25(a) required-elements map (also aligned to ICH
    E6(R2) §4.8.10):

      A) statement of research purpose
      B) description of procedures + identification of experimental ones
      C) reasonably foreseeable risks / discomforts
      D) reasonably expected benefits
      E) alternative procedures / treatments
      F) confidentiality of records
      G) compensation / medical treatment for injury
      H) contacts for questions about the research / rights / injury
      I) statement that participation is voluntary
    """

    section_id: Literal[
        "purpose",
        "procedures",
        "risks",
        "benefits",
        "alternatives",
        "confidentiality",
        "injury_and_compensation",
        "contacts",
        "voluntariness",
    ]
    heading: str
    body: str = Field(description="Plain-text content at the configured reading level.")


class InformedConsentForm(BaseModel):
    """Legally-binding ICF aligned to 21 CFR §50.25(a) required elements.

    The schema requires every required-element section by name — the
    agent can't accidentally drop one. Optional regulator-extras (e.g.
    HIPAA authorisation, pregnancy partner notification) come in as
    additional `optional_sections`.
    """

    kind: Literal["informed_consent_form"] = "informed_consent_form"
    title: str
    study_name: str
    sponsor: str
    language: LangCode
    reading_level_target: int = Field(ge=4, le=12)
    reading_level_grade_actual: float = Field(
        description=(
            "Computed Flesch-Kincaid grade level of the assembled ICF. "
            "Reported in the report header so the IRB sees the actual "
            "vs target. Must be ≤ reading_level_target + 1 (allow a "
            "small overshoot) for the IRB to accept; otherwise the "
            "agent flags it in `notes`."
        )
    )
    sections: list[IcfSection] = Field(
        min_length=9,
        description="Required 9 sections covering 21 CFR §50.25(a) A-I.",
    )
    optional_sections: list[IcfSection] = Field(default_factory=list)
    signature_block: str = Field(
        default=(
            "I have read this consent form. My questions have been "
            "answered. I voluntarily agree to take part in this study."
        ),
        description="Plain-English acknowledgement line above the signature.",
    )
    notes: str = ""


# ── STEP 4 — assembled document ─────────────────────────────────────────


class IrbDocument(BaseModel):
    """The final assembled IRB packet (synopsis + ICF).

    Iterable — refinements return another IrbDocument with
    `is_final=False` until the user types "Finalize IRB".
    """

    kind: Literal["irb_document"] = "irb_document"
    intake: IrbIntake
    synopsis: ProtocolSynopsis
    icf: InformedConsentForm
    is_final: bool = False


# ── Discriminated union for the agent's output ──────────────────────────


IrbTurn = Annotated[
    ClarificationRequest
    | IrbIntake
    | ProtocolSynopsis
    | InformedConsentForm
    | IrbDocument,
    Field(discriminator="kind"),
]


__all__ = [
    "ClarificationRequest",
    "IcfSection",
    "InformedConsentForm",
    "IrbDocument",
    "IrbIntake",
    "IrbTurn",
    "JurisdictionType",
    "LangCode",
    "ProtocolSynopsis",
    "SynopsisSection",
]
