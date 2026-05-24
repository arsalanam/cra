"""Pydantic schemas for the sr_protocol specialist's turn-based workflow.

Each agent run returns one `SrProtocolTurn` — a discriminated union over:

  clarification     — needs more info (shared shape from common)
  protocol_methods  — methodological core for review/edit (PICO, eligibility,
                      RoB tool, effect-measure plan, synthesis plan)
  protocol_document — full PRISMA-P document with grounded background,
                      references, markdown export, PROSPERO field map.
                      Iterable until the user clicks Finalize.

`PicoTable` and `EffectMeasure` are reused from `domain/meta_analysis.py`
because the methodological core overlaps with what meta_analysis consumes.
The downstream hand-off carries the same PicoTable into meta_analysis with
no shape conversion.

Anti-hallucination posture mirrors the other specialists:
  - PICO MeSH terms must come from real `mesh_lookup` calls.
  - Every Citation must come from a real `search_papers` / `web_search` /
    `wikipedia` call this turn.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest
from .meta_analysis import EffectMeasure, PicoTable

ReviewType = Literal[
    "intervention",
    "diagnostic",
    "prognostic",
    "etiology",
    "prevalence",
]

RobTool = Literal[
    "RoB 2.0",
    "ROBINS-I",
    "Newcastle-Ottawa",
    "QUADAS-2",
    "ROBIS",
    "AMSTAR-2",
]

CitationOrigin = Literal["search_papers", "web_search", "wikipedia", "mesh_lookup"]


class EligibilityCriteria(BaseModel):
    """Inclusion / exclusion + the structural filters reviewers apply."""

    inclusion: list[str] = Field(
        default_factory=list,
        description="Bullet-style inclusion rules. Each item one sentence.",
    )
    exclusion: list[str] = Field(default_factory=list)
    study_designs: list[str] = Field(
        default_factory=list,
        description=(
            "Canonical PubMed publication-type names: 'Randomized Controlled "
            "Trial', 'Cohort study', 'Case-control study', 'Cross-sectional "
            "study', 'Diagnostic accuracy study'. Used by RoB auto-suggest."
        ),
    )
    language: list[str] = Field(default_factory=list, description='e.g. ["English"].')
    date_range: str | None = Field(
        default=None,
        description="e.g. '2010–present' or '2015-01 to 2025-12'.",
    )
    age_range: str | None = None
    setting: str | None = Field(
        default=None,
        description="Outpatient / inpatient / primary care / community / ICU.",
    )


class RobToolChoice(BaseModel):
    """The risk-of-bias instrument planned for the review.

    Auto-suggest rules (applied in the system prompt):
      - RCTs only                       → RoB 2.0
      - RCTs + non-randomized           → ROBINS-I
      - Cohort/case-control only        → Newcastle-Ottawa
      - Diagnostic accuracy             → QUADAS-2
      - Reviews of reviews              → AMSTAR-2 / ROBIS
    """

    tool: RobTool
    rationale: str = Field(
        description="Why this tool fits the included study designs.",
    )


class SynthesisPlan(BaseModel):
    """How the reviewer plans to combine evidence."""

    primary_method: Literal[
        "random_effects_meta",
        "fixed_effects_meta",
        "narrative_only",
    ] = Field(
        description=(
            "Default to random_effects_meta unless the reviewer has a strong "
            "reason for fixed-effects (very few studies, no expected "
            "between-study variation) or narrative-only (high heterogeneity, "
            "incompatible outcome measures)."
        ),
    )
    heterogeneity_assessment: list[str] = Field(
        default_factory=lambda: ["I²", "Tau²", "Cochran's Q"],
        description="Statistics planned for between-study variance.",
    )
    planned_subgroups: list[str] = Field(
        default_factory=list,
        description="A priori subgroup variables, e.g. 'age band', 'baseline severity'.",
    )
    planned_sensitivity_analyses: list[str] = Field(
        default_factory=list,
        description="e.g. 'exclude high-RoB studies', 'restrict to ITT analyses'.",
    )
    publication_bias_methods: list[str] = Field(
        default_factory=lambda: ["Funnel plot", "Egger's test"],
        description="Methods to assess small-study / publication bias.",
    )


class Citation(BaseModel):
    """One reference in the protocol's background section.

    Provenance is tracked: every Citation must have come from a real tool
    call this turn. The system prompt enforces this.
    """

    text: str = Field(
        description=(
            "Vancouver-style reference text the model wrote, e.g. "
            "'Smith J, et al. Title. Journal 2023;12:345.'"
        ),
    )
    pmid: str | None = Field(
        default=None,
        description="Required when origin='search_papers' and the record is from PubMed.",
    )
    doi: str | None = None
    url: str | None = Field(
        default=None,
        description="Used for web_search / wikipedia origins.",
    )
    origin: CitationOrigin = Field(
        description="Which tool surfaced this reference. Drives provenance auditing.",
    )


class ProsperoFieldMap(BaseModel):
    """One PROSPERO registration field, ready to paste."""

    field: str = Field(
        description=(
            "PROSPERO field name verbatim, e.g. 'Review title', 'Review "
            "question', 'Participants/population', 'Risk of bias (quality) "
            "assessment', 'Strategy for data synthesis'."
        ),
    )
    content: str = Field(
        description=(
            "The value to paste into that PROSPERO field. For user-specific "
            "fields the model cannot fill (Named contact, Funding sources, "
            "Country, IRB number), use '[USER INPUT NEEDED: <what to "
            "provide>]' rather than fabricating."
        ),
    )


class ProtocolMethods(BaseModel):
    """Stage 2 turn — methodological core for the user to review/edit."""

    kind: Literal["protocol_methods"] = "protocol_methods"
    title: str = Field(
        description=(
            "Working protocol title following PRISMA-P guidance: condition + "
            "intervention + comparator + study design. e.g. "
            "'Sodium-glucose co-transporter-2 inhibitors for prevention of "
            "heart-failure hospitalisation in adults with type 2 diabetes: a "
            "systematic review and meta-analysis of randomised trials'."
        ),
    )
    review_type: ReviewType
    pico: PicoTable
    eligibility: EligibilityCriteria
    information_sources: list[str] = Field(
        default_factory=lambda: ["PubMed", "Embase", "Cochrane CENTRAL"],
        description=(
            "Databases + grey-literature sources the search will cover. "
            "Defaults to the Cochrane-style triumvirate; add CINAHL for "
            "nursing-related questions, PsycINFO for mental health, "
            "ClinicalTrials.gov for trial-registry coverage."
        ),
    )
    rob_tool: RobToolChoice
    effect_measures_plan: dict[str, EffectMeasure] = Field(
        description=(
            "Map outcome name → planned effect measure. Binary outcomes use "
            "OR or RR; continuous use MD (same scale across studies) or SMD "
            "(different scales). Outcome names must come from PicoTable.outcomes."
        ),
    )
    synthesis_plan: SynthesisPlan
    use_grade: bool = Field(
        default=True,
        description="Whether GRADE certainty assessment is planned.",
    )
    notes: str | None = Field(
        default=None,
        description="Free-text caveats, scope limits, or stakeholder notes.",
    )


class ProtocolDocument(BaseModel):
    """Stage 3 turn — full PRISMA-P document.

    Iterable: the user may request refinements ('expand the background',
    'add a sensitivity analysis for high-RoB studies'). Each refinement
    returns another ProtocolDocument with `is_final=False`. Clicking
    Finalize returns one with `is_final=True` and the hand-off buttons
    appear in the UI.
    """

    kind: Literal["protocol_document"] = "protocol_document"
    methods: ProtocolMethods = Field(
        description=(
            "Carried forward from the protocol_methods turn, possibly with "
            "user edits. The downstream search_strategy / meta_analysis "
            "hand-off reads PICO + eligibility from here."
        ),
    )
    background: str = Field(
        description=(
            "2–3 paragraphs grounding the rationale. Every concrete claim "
            "(prevalence numbers, prior trial findings, guideline gaps) must "
            "be backed by an entry in `references`. Use bracketed numeric "
            "citations like [1], [2] mapping to references list order."
        ),
    )
    references: list[Citation] = Field(
        default_factory=list,
        description=(
            "Every Citation must come from a real tool call (search_papers, "
            "web_search, wikipedia) in this turn. No training-data citations."
        ),
    )
    full_markdown: str = Field(
        description=(
            "Complete PRISMA-P document in Markdown — title, background "
            "with inline citations, objectives (PICO), methods (eligibility, "
            "info sources, search-strategy placeholder, selection process, "
            "data extraction, RoB, effect measures, synthesis, GRADE), "
            "references list. Copy-paste ready into PROSPERO/journal/email."
        ),
    )
    prospero_field_map: list[ProsperoFieldMap] = Field(
        default_factory=list,
        description=(
            "PROSPERO registration fields populated from the protocol. "
            "Skip fields the model can't legitimately fill — use "
            "'[USER INPUT NEEDED: ...]' rather than fabricating."
        ),
    )
    is_final: bool = Field(
        default=False,
        description=(
            "True after the user clicks Finalize. Enables the search_strategy "
            "and meta_analysis hand-off CTAs in the UI."
        ),
    )
    notes: str | None = None


# Discriminated union of every shape the sr_protocol specialist may emit.
SrProtocolTurn = Annotated[
    ClarificationRequest | ProtocolMethods | ProtocolDocument,
    Field(discriminator="kind"),
]
