"""Pydantic schemas for the meta-analysis specialist's turn-based workflow.

Each agent run returns one `MetaAnalysisTurn` — a discriminated union over
the workflow shapes the specialist can produce:

  clarification    — needs more info from the user (shared shape from common)
  pico             — structured PICO table draft for review/edit
  search_results   — PubMed studies matching a confirmed PICO
  data_extraction  — per-study × per-outcome data table for review/edit
  meta_analysis    — pooled effect estimates + forest plots

Note: `Answer` is intentionally NOT in this union. The dispatcher routes
non-clinical questions to the general_qa specialist; the meta-analysis
specialist never has to fall back to free text. PMIDs and effect-size
numbers MUST come from real tool calls (search_papers, sandbox_exec).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest


class PicoTable(BaseModel):
    """PICO + extras — the contract a researcher reviews before searching."""

    population: str = Field(
        description=(
            "Patient population. Be precise: condition, severity, age range, "
            "prior treatments. Example: 'Adults ≥18 y/o with ACS who underwent PCI'."
        )
    )
    intervention: str = Field(
        description="The intervention arm. Example: 'PPI + dual antiplatelet therapy (DAPT)'."
    )
    comparison: str = Field(
        description=(
            "The comparator arm. Use 'placebo', 'no PPI', or another active "
            "comparator. For single-arm studies, write 'none'."
        )
    )
    outcomes: list[str] = Field(
        default_factory=list,
        description="Each measurable outcome on its own line. e.g., 'Upper GI bleeding'.",
    )
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    study_types: list[str] = Field(
        default_factory=list,
        description=(
            "e.g., ['Randomized Controlled Trial']. Use the standard PubMed pub-type names."
        ),
    )
    age_range: str | None = Field(
        default=None,
        description="e.g., '≥18 years', 'pediatric (0-17)', 'all ages'.",
    )
    notes: str | None = Field(
        default=None,
        description="Free-text notes on scope, planned subgroups, or known prior work.",
    )


class StudyCandidate(BaseModel):
    """One study record, sourced from a real `search_papers` tool call."""

    source: str = Field(
        description=(
            "Which paper source returned this record: 'pubmed', 'europepmc', "
            "etc. Forward verbatim from the `search_papers` result."
        ),
    )
    source_id: str = Field(
        description=(
            "The source's canonical id — PMID for PubMed records, Europe PMC "
            "id (e.g. 'PMC1234567' or '12345678') for Europe PMC. Always "
            "populated; equals `pmid` when source is 'pubmed'."
        ),
    )
    pmid: str | None = Field(
        default=None,
        description=(
            "PubMed ID. Always present for PubMed records; present for "
            "Europe PMC records that are also indexed in Medline; None for "
            "Europe PMC records sourced from preprints / non-Medline corpora."
        ),
    )
    doi: str | None = Field(
        default=None,
        description=(
            "Digital Object Identifier — feeds the 'Open at publisher' link in "
            "the UI so the user can fetch the full PDF when the abstract is "
            "incomplete. Pass through verbatim from `search_papers` results."
        ),
    )
    title: str
    journal: str | None = None
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    publication_types: list[str] = Field(default_factory=list)
    mesh_headings: list[str] = Field(default_factory=list)
    relevance_note: str | None = Field(
        default=None,
        description="One-line explanation of how this study fits the confirmed PICO.",
    )


class SearchStrategy(BaseModel):
    """The PubMed query the agent constructed for the confirmed PICO."""

    pubmed_query: str = Field(description="Boolean PubMed query using MeSH terms and field tags.")
    rationale: str = Field(
        description="Short explanation of how the query was assembled from PICO concepts."
    )
    filters: list[str] = Field(
        default_factory=list,
        description="Applied filters, e.g., 'humans[mh]', 'English[la]', 'RCT[pt]'.",
    )


class PicoDraft(BaseModel):
    """Specialist has produced a PICO draft; user reviews / edits before searching."""

    kind: Literal["pico"] = "pico"
    pico: PicoTable
    rationale: str = Field(
        description="Explain the choices made (sources of inclusion/exclusion, study types, etc.)."
    )


class SearchResults(BaseModel):
    """Specialist ran the search and is returning candidate studies."""

    kind: Literal["search_results"] = "search_results"
    strategy: SearchStrategy
    studies: list[StudyCandidate]
    total_found: int = Field(
        description="Total studies PubMed reported for the query (may exceed `len(studies)`)."
    )
    notes: str | None = None


# ── Phase 2 — data extraction & meta-analysis ─────────────────────────────


EffectMeasure = Literal["OR", "RR", "MD", "SMD"]
ExtractionSource = Literal["abstract", "full_text", "user_provided"]


class OutcomeData(BaseModel):
    """Numbers extracted from one study for one outcome.

    For binary outcomes (OR/RR), set events_intervention/comparison and
    n_intervention/comparison.
    For continuous outcomes (MD/SMD), set mean/sd/n on both arms.
    Leave fields the abstract didn't report as None and explain in
    `extraction_notes`. The frontend lets the user paste missing values
    after fetching the full PDF themselves.
    """

    outcome: str = Field(description="Outcome name from the confirmed PICO.")
    effect_measure: EffectMeasure = Field(
        description=(
            "Inferred from the outcome wording — OR/RR for binary "
            "(events / counts); MD/SMD for continuous (means / SDs). "
            "User can override via the dropdown in the UI."
        )
    )

    # Binary fields
    events_intervention: int | None = None
    n_intervention: int | None = None
    events_comparison: int | None = None
    n_comparison: int | None = None

    # Continuous fields
    mean_intervention: float | None = None
    sd_intervention: float | None = None
    mean_comparison: float | None = None
    sd_comparison: float | None = None
    # n_intervention / n_comparison reused for continuous arm sizes

    extraction_source: ExtractionSource = "abstract"
    extraction_notes: str | None = Field(
        default=None,
        description=(
            "Use this to flag what's missing or how the numbers were derived "
            "(e.g. 'event counts not reported in abstract — full PDF needed')."
        ),
    )
    is_complete: bool = Field(
        description=(
            "True only when every required field for the chosen effect measure "
            "is populated. Set False if anything is missing — the UI will "
            "prompt the user to paste in values from the full PDF."
        )
    )


class StudyExtractedData(BaseModel):
    """All extractable data for one study."""

    source: str = Field(
        description="Which paper source returned this record (forwarded from `search_papers`).",
    )
    source_id: str = Field(
        description="Source's canonical id — equals `pmid` when source is 'pubmed'.",
    )
    pmid: str | None = Field(
        default=None,
        description=("PubMed ID when available. None for non-Medline Europe PMC records."),
    )
    doi: str | None = Field(
        default=None,
        description=(
            "Carry through the DOI from the original `search_papers` result. "
            "Drives the 'Open at publisher' link the user clicks to fetch the "
            "full PDF when manual extraction is needed."
        ),
    )
    title: str
    pmc_id: str | None = Field(
        default=None,
        description="PMC ID if `fetch_pmc_fulltext` succeeded (full text was open access).",
    )
    study_design: str | None = None
    follow_up: str | None = Field(
        default=None,
        description="Follow-up duration as reported, e.g., '12 weeks', '2 years'.",
    )
    outcomes: list[OutcomeData] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(
        default_factory=list,
        description="Cross-study issues, e.g., 'reports per-protocol not ITT'.",
    )


class DataExtraction(BaseModel):
    """Per-study extracted data, presented for the user to review/edit before meta-analysis."""

    kind: Literal["data_extraction"] = "data_extraction"
    studies: list[StudyExtractedData]
    outcome_names: list[str] = Field(
        description="Outcome names from the confirmed PICO (drives table columns)."
    )
    summary: str = Field(
        description=(
            "One-paragraph summary: total studies, how many had complete data per "
            "outcome, which need manual extraction."
        )
    )


class MetaAnalysisOutcomeResult(BaseModel):
    """Pooled result for one outcome, including the embedded forest plot."""

    outcome: str
    effect_measure: EffectMeasure
    pooled_effect: float = Field(description="Pooled point estimate (e.g., OR=0.72).")
    ci_lower: float
    ci_upper: float
    p_value: float | None = None
    i_squared: float | None = Field(
        default=None,
        description="Heterogeneity I² as a percentage (0–100).",
    )
    heterogeneity_p: float | None = None
    n_studies: int
    n_participants: int
    forest_plot_image: str | None = Field(
        default=None,
        description=(
            "Filename produced by sandbox_exec (e.g., 'forest_plot_outcome_1.png'). "
            "The dispatcher's post-processor substitutes this with the data-URI "
            "before returning to the frontend."
        ),
    )
    funnel_plot_image: str | None = Field(
        default=None,
        description=(
            "Optional filename produced by `run_visualisation(viz_kind='funnel', ...)` "
            "(e.g., 'funnel-all-cause-mortality.png'). Present only when the agent "
            "ran a funnel plot for publication-bias diagnosis on this outcome "
            "(≥3 studies)."
        ),
    )
    eggers_p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Egger's regression test intercept p-value. Low values (<0.10 by "
            "convention) signal small-study effects / funnel asymmetry. Set only "
            "when a funnel plot was produced."
        ),
    )
    funnel_interpretation: str | None = Field(
        default=None,
        description=(
            "One-line interpretation of the funnel + Egger's result "
            "(e.g. 'Egger's p=0.42 across 9 studies — funnel symmetric')."
        ),
    )
    interpretation: str = Field(
        description=(
            "One-sentence clinical takeaway (favours intervention / comparator / no difference)."
        )
    )


class MetaAnalysisResults(BaseModel):
    """Final meta-analysis output."""

    kind: Literal["meta_analysis"] = "meta_analysis"
    outcome_results: list[MetaAnalysisOutcomeResult]
    studies_included: list[str] = Field(description="PMIDs that contributed to the pool.")
    studies_excluded: list[dict[str, str]] = Field(
        default_factory=list,
        description="[{pmid, reason}] for studies dropped during analysis.",
    )
    summary: str = Field(description="Overall summary across outcomes.")
    caveats: list[str] = Field(
        default_factory=list,
        description="Limitations: heterogeneity, small studies, missing data, etc.",
    )


# Discriminated union of every shape the meta-analysis specialist may emit.
# `Answer` is deliberately NOT included — non-clinical / meta questions are
# handled by the general_qa specialist via the dispatcher.
MetaAnalysisTurn = Annotated[
    ClarificationRequest | PicoDraft | SearchResults | DataExtraction | MetaAnalysisResults,
    Field(discriminator="kind"),
]
