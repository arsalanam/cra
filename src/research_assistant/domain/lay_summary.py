"""Pydantic schemas for the lay_summary specialist (P2 #2).

Shipping plain-language summaries for three intake sources:

  • Recruitment lay summary — from a protocol synopsis / IRB intake; for
    posters, study landing pages, consent supplements. Closest sibling
    of the irb_drafter ICF surface.
  • Evidence lay summary — from a completed meta_analysis turn; for
    shared-decision-making aids and patient-facing "what does the
    evidence say" write-ups.
  • Trial-results lay summary — from a completed CSR / trial_stats turn;
    for return-of-results letters and EMA Reg (EU) No 536/2014 lay-
    summary obligations.

Anti-hallucination + compliance posture:

  • Every claim in an evidence / results variant must cite the source
    artefact id (PMID for evidence, derived_from for results). The
    schema makes the citation list a required field with min_length=1.
  • No medical-decision language. The lay summary describes what was
    found and what it might mean, never "you should" / "you must".
  • Reading-level is COMPUTED host-side (services.readability) and
    written back into the LaySummaryDocument before persistence —
    the model can't fabricate a low grade by claiming it.
  • Multilingual: en/es/fr/de plus a free-text region field (e.g.
    "Mexico" vs "Spain" for Spanish). Any other language → the agent
    emits a clarification asking for a human translator.

CISCRP / NIH plain-language guidance shapes the section order:
purpose → what we did / will do → what we found / hope to find → what
this means for you → next steps.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field

from .common import ClarificationRequest

# ── Categorical vocabularies ────────────────────────────────────────────


LangCode = Literal["en", "es", "fr", "de"]


# Three intake sources. The `source_kind` field on the LaySummaryDocument
# discriminates which one was used so the report renderer can pick the
# right cover page + provenance footer.
SourceKind = Literal[
    "recruitment_protocol",
    "evidence_meta_analysis",
    "results_trial",
]


# ── Audience profile ────────────────────────────────────────────────────


class AudienceProfile(BaseModel):
    """Who the lay summary is written for.

    `target_grade` drives the readability iteration loop (the specialist
    refuses to ship a document whose computed grade is more than
    `target_grade + 1`). `language` + `region` together let the same
    source generate Spanish-Mexico and Spanish-Spain versions distinct
    from each other.
    """

    target_grade: int = Field(
        default=6,
        ge=4,
        le=12,
        description=(
            "Target Flesch-Kincaid grade level. CISCRP guidance for "
            "patient-facing materials: 6 (general adult patient), 8 "
            "(patients with college education), 10 (healthcare "
            "professionals). Default 6 for safety."
        ),
    )
    language: LangCode = "en"
    region: str = Field(
        default="",
        max_length=80,
        description=(
            "Free-text region / cultural-note (e.g. 'Mexico', 'Spain', "
            "'India English'). Empty is fine for a generic English "
            "version. Drives idiom choice and metric / imperial units."
        ),
    )
    population_descriptor: str = Field(
        default="adults",
        max_length=120,
        description=(
            "Plain-English audience descriptor — e.g. 'adolescents "
            "12-17', 'parents of children with asthma', 'post-MI "
            "patients'. Shapes voice, examples, and reading level."
        ),
    )


# ── STEP 1 — intake variants ────────────────────────────────────────────


class RecruitmentIntake(BaseModel):
    """Source = a protocol synopsis / IRB intake.

    For recruitment posters + landing pages + consent supplements.
    The operator pastes the synopsis text (often from irb_drafter or
    registration_drafter); the lay summary specialist reformats — it
    does NOT re-elicit study design.
    """

    kind: Literal["recruitment_intake"] = "recruitment_intake"
    source_kind: Literal["recruitment_protocol"] = "recruitment_protocol"
    audience: AudienceProfile
    protocol_summary: str = Field(
        min_length=20,
        description=(
            "Free-text protocol synopsis the operator pastes. Usually "
            "comes from the irb_drafter handoff or the "
            "registration_drafter Core Fields."
        ),
    )
    study_title: str = Field(min_length=4)
    sponsor: str = Field(min_length=2)
    notes: str = ""


class EvidenceIntake(BaseModel):
    """Source = a completed meta-analysis turn.

    For shared-decision-making aids and patient-facing "what does the
    evidence say" write-ups. The lay summary cites the underlying
    studies (PMIDs) — never fabricates them.
    """

    kind: Literal["evidence_intake"] = "evidence_intake"
    source_kind: Literal["evidence_meta_analysis"] = "evidence_meta_analysis"
    audience: AudienceProfile
    pico_question: str = Field(min_length=10)
    pooled_effect_summary: str = Field(
        min_length=10,
        description=(
            "Plain-text 1-2 sentence summary of the pooled effect the "
            "meta-analysis found — e.g. 'a small reduction in 28-day "
            "mortality, 95% CI 0.71 to 0.92'. Pasted by the operator "
            "from the meta_analysis terminal card; not regenerated."
        ),
    )
    pmid_sources: list[str] = Field(
        min_length=1,
        description=(
            "PMIDs of the included studies. The model can ONLY cite "
            "PMIDs from this list — same anti-hallucination posture "
            "as the rest of the platform."
        ),
    )
    notes: str = ""


class ResultsIntake(BaseModel):
    """Source = a completed CSR / trial_stats turn.

    For return-of-results letters and EMA Reg (EU) No 536/2014 lay-
    summary obligations. Every numeric claim is derived from an
    operator-pasted result block and must cite its `derived_from`
    artefact id.
    """

    kind: Literal["results_intake"] = "results_intake"
    source_kind: Literal["results_trial"] = "results_trial"
    audience: AudienceProfile
    trial_title: str = Field(min_length=4)
    sponsor: str = Field(min_length=2)
    nct_id: str | None = Field(
        default=None,
        pattern=r"^NCT\d{8}$",
        description="ClinicalTrials.gov registry id (NCTxxxxxxxx).",
    )
    primary_outcome_summary: str = Field(
        min_length=10,
        description=(
            "Plain-text 1-2 sentence summary of the primary-outcome "
            "result. Pasted from trial_stats or CSR; not regenerated."
        ),
    )
    safety_summary: str = Field(
        min_length=10,
        description=(
            "Plain-text 1-2 sentence safety summary (most common AE + "
            "any SAE signal). Pasted from CSR safety section."
        ),
    )
    derived_from_ids: list[str] = Field(
        min_length=1,
        description=(
            "Source-artefact ids (e.g. 'sandbox:cox_ph:OS', 'TLF-14') "
            "the lay summary's numbers are derived from. Cited in the "
            "provenance footer."
        ),
    )
    notes: str = ""


# The three intake variants. Used by callers building a lay_summary
# from one of the three handoff seeds; the agent emits a specific
# variant rather than a tagged union here.
LaySummaryIntake = RecruitmentIntake | EvidenceIntake | ResultsIntake


# ── STEP 2 — drafted plain-language sections ────────────────────────────


class LaySummarySection(BaseModel):
    """One named section of the plain-language draft.

    `body` is the plain-text content (no markdown headers — the report
    renderer adds them). The CISCRP / NIH plain-language section
    order: what the study is about → what we did / will do → what we
    found / hope to find → what this means for you → what's next.
    """

    section_id: Literal[
        "what_this_is_about",
        "what_we_did",
        "what_we_found",
        "what_this_means_for_you",
        "next_steps",
    ]
    heading: str = Field(min_length=2)
    body: str = Field(min_length=20)


class LaySummaryDraft(BaseModel):
    """A draft of the plain-language summary — one per refinement round.

    The host-side readability loop computes `grade_actual` after the
    model returns the draft; the model's own estimate is discarded.
    The specialist iterates (up to 3 attempts) until `grade_actual ≤
    target_grade + 1` or surfaces an explicit "could not reach target"
    note in the document.
    """

    kind: Literal["lay_summary_draft"] = "lay_summary_draft"
    title: str = Field(min_length=4)
    one_line_summary: str = Field(
        min_length=20,
        max_length=240,
        description=(
            "A single sentence (≤240 chars) describing what the "
            "summary is about. Sits above the section list as a "
            "poster-ready strapline."
        ),
    )
    sections: list[LaySummarySection] = Field(
        min_length=5,
        max_length=5,
        description=(
            "Exactly 5 sections in the CISCRP / NIH order. The "
            "section_id values are enforced by the LaySummarySection "
            "literal so the agent can't reorder or skip a section."
        ),
    )
    plain_language_glossary: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Optional glossary of clinical terms with their plain-"
            "language gloss. Each entry: {'term': str, "
            "'gloss': str}. Surfaced as a sidebar on the report."
        ),
    )
    notes: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def joined_body(self) -> str:
        """Concatenated text used by the host-side readability check.

        Joining the one-line + section bodies (NOT the headings or
        glossary) keeps the grade representative of what the patient
        actually reads. Headings are usually 1-3 words and would skew
        the metric.
        """
        parts = [self.one_line_summary]
        parts.extend(s.body for s in self.sections)
        return "\n\n".join(parts)


# ── STEP 3 — assembled document ─────────────────────────────────────────


class LaySummaryDocument(BaseModel):
    """The assembled lay-summary deliverable.

    Sequence: intake → draft → document. The document is iterable
    (`is_final=False`) until the user types "Finalize lay summary".

    Anti-hallucination + audit:
      • `grade_actual` is the host-computed Flesch-Kincaid grade. The
        specialist writes it back BEFORE persisting; the model's own
        estimate is overwritten if present.
      • Every variant carries its source provenance (PMIDs for
        evidence, derived_from ids for results, protocol_summary
        excerpt for recruitment).
      • Surfaces `readability_attempts` — how many rounds of host
        rewrite-loop iteration the draft took to land under target.
    """

    kind: Literal["lay_summary_document"] = "lay_summary_document"
    source_kind: SourceKind
    audience: AudienceProfile
    draft: LaySummaryDraft
    grade_actual: float = Field(
        ge=0,
        description=(
            "Host-computed Flesch-Kincaid grade of the joined body. "
            "Written by the specialist after the draft round, never "
            "trusted from the model."
        ),
    )
    readability_attempts: int = Field(
        default=1,
        ge=1,
        le=3,
        description=(
            "Number of model passes used to land under target. 1 = "
            "first pass; 3 = max retries reached (the document may "
            "include a note that the target wasn't met)."
        ),
    )
    citations: list[str] = Field(
        default_factory=list,
        description=(
            "Source provenance ids surfaced in the report's footer "
            "(PMIDs for evidence variant, derived_from ids for "
            "results variant, empty list for recruitment variant)."
        ),
    )
    is_final: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grade_within_target(self) -> bool:
        """True when the host-computed grade lands within the +1
        allowance over the target. The report renderer uses this to
        switch between an "OK" and a "warning" badge in the cover
        page."""
        return self.grade_actual <= self.audience.target_grade + 1.0


# ── Discriminated union for the agent's output ──────────────────────────


LaySummaryTurn = Annotated[
    ClarificationRequest
    | RecruitmentIntake
    | EvidenceIntake
    | ResultsIntake
    | LaySummaryDraft
    | LaySummaryDocument,
    Field(discriminator="kind"),
]


__all__ = [
    "AudienceProfile",
    "ClarificationRequest",
    "EvidenceIntake",
    "LangCode",
    "LaySummaryDocument",
    "LaySummaryDraft",
    "LaySummaryIntake",
    "LaySummarySection",
    "LaySummaryTurn",
    "RecruitmentIntake",
    "ResultsIntake",
    "SourceKind",
]
