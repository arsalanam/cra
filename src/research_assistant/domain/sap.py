"""Pydantic schemas for the sap_drafter specialist (top-6 #3).

Each agent run returns one `SapTurn` — a discriminated union over:

  clarification   — needs more info (shared shape from common)
  picot           — population/intervention/comparator/outcome/timeframe +
                    trial design + hypothesis type (superiority etc.)
  sample_size     — computed N from the sample_size tool with the formula
                    citation + the inputs the user can iterate on
  analysis_plan   — ICH E9 fields (populations, primary test, multiplicity,
                    missing data, interim analyses, sensitivity, subgroup)
  sap_document    — full Statistical Analysis Plan, iterable until Finalize

Anti-hallucination posture:
  - Effect-size numbers MUST come from the user or be derived by the
    `sample_size` tool. Never quote effect sizes from training data.
  - All references in the final document carry an `origin` like the
    sr_protocol citations (web_search / search_papers / wikipedia).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest

# ── Categorical vocabularies ─────────────────────────────────────────────


DesignType = Literal[
    "parallel_rct",
    "cluster_rct",
    "crossover",
    "factorial",
    "single_arm",
    "stepped_wedge",
    "platform",
]

HypothesisType = Literal["superiority", "non_inferiority", "equivalence"]

OutcomeType = Literal["binary", "continuous", "time_to_event", "paired"]

AnalysisPopulation = Literal["ITT", "mITT", "PP", "Safety"]

MultiplicityStrategy = Literal[
    "none",
    "bonferroni",
    "holm",
    "hochberg",
    "hierarchical",
    "gatekeeping",
    "alpha_spending_obrien_fleming",
    "alpha_spending_pocock",
]

MissingDataStrategy = Literal[
    "complete_case",
    "lvcf",
    "lvcf_baseline",
    "multiple_imputation_mar",
    "tipping_point_sensitivity",
    "mixed_model_for_repeated_measures",
]

CitationOrigin = Literal["search_papers", "web_search", "wikipedia"]


# ── PICOT intake ─────────────────────────────────────────────────────────


class PicotTable(BaseModel):
    """Prospective-trial intake — PICO + Timeframe + Design + Hypothesis.

    The shape parallels meta_analysis's PicoTable but is keyed to a
    prospective trial rather than a literature question. The downstream
    sample-size step pulls the design + hypothesis from here so the
    chosen formula matches the trial shape.
    """

    kind: Literal["picot"] = "picot"
    population: str = Field(description="The target enrolment population.")
    intervention: str = Field(description="The experimental arm.")
    comparator: str = Field(description="Active control, placebo, or standard-of-care.")
    primary_outcome: str = Field(
        description=(
            "The single primary endpoint the trial is powered to detect. "
            "Distinct from secondary outcomes which are listed separately."
        ),
    )
    secondary_outcomes: list[str] = Field(default_factory=list)
    timeframe: str = Field(
        description=(
            "Primary-endpoint assessment timepoint and the total follow-up "
            "duration (e.g. 'Primary at 12 weeks; total follow-up 52 weeks')."
        ),
    )
    design: DesignType = Field(
        description="Trial design shape; drives the sample-size formula family."
    )
    hypothesis_type: HypothesisType = Field(
        description=(
            "Superiority is the default. Non-inferiority + equivalence "
            "require a margin which the user specifies in the assumptions."
        ),
    )
    outcome_type: OutcomeType = Field(
        description="Drives which sample-size formula the tool calls."
    )
    notes: str | None = None


# ── Sample-size assumptions + result ─────────────────────────────────────


class EffectAssumptions(BaseModel):
    """Inputs that drive the sample-size calc. The fields used depend on
    `outcome_type` — keep all optional so the SAP can be drafted before
    every assumption is finalised, but at calc time the specialist
    surfaces a clarification if a needed field is missing.
    """

    # Two-proportions
    control_event_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Anticipated rate in the control arm (proportion 0–1).",
    )
    relative_risk_reduction: float | None = Field(
        default=None,
        description=(
            "Proportional reduction in the event rate, e.g. 0.30 = RRR 30%. "
            "Either this or `absolute_risk_difference` is required for "
            "binary outcomes."
        ),
    )
    absolute_risk_difference: float | None = Field(default=None)

    # Two-means (continuous)
    control_mean: float | None = None
    intervention_mean: float | None = None
    standard_deviation: float | None = Field(
        default=None, gt=0.0, description="Common SD for continuous outcomes."
    )

    # Time-to-event
    hazard_ratio: float | None = Field(default=None, gt=0.0)
    control_survival_at_horizon: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="S(t*) on the control arm at the primary-endpoint horizon.",
    )

    # Paired / within-subject
    mean_difference: float | None = None
    sd_of_difference: float | None = Field(default=None, gt=0.0)

    # Common knobs
    alpha: float = Field(default=0.05, gt=0.0, lt=0.5)
    power: float = Field(default=0.8, gt=0.5, lt=1.0)
    allocation_ratio: float = Field(
        default=1.0,
        gt=0.0,
        description="Intervention-to-control ratio; 1.0 = balanced.",
    )
    dropout_rate: float = Field(
        default=0.0,
        ge=0.0,
        lt=1.0,
        description="Anticipated attrition; final N is inflated to compensate.",
    )
    one_sided: bool = Field(
        default=False,
        description="Most trials are two-sided; switch only with strong rationale.",
    )
    non_inferiority_margin: float | None = Field(
        default=None,
        description=(
            "Required for non_inferiority / equivalence trials. Same units "
            "as the primary outcome (e.g. risk difference of 0.05)."
        ),
    )


class SampleSizeResult(BaseModel):
    """Stage 2 turn — sample-size table the user reviews + iterates on."""

    kind: Literal["sample_size"] = "sample_size"

    picot: PicotTable = Field(description="Carried forward so the result is self-explanatory.")
    assumptions: EffectAssumptions
    n_per_arm_intervention: int = Field(
        ge=1,
        description="Required participants in the intervention arm BEFORE dropout.",
    )
    n_per_arm_control: int = Field(ge=1)
    n_total: int = Field(
        ge=2,
        description=(
            "Total enrolment AFTER dropout inflation. The arm-level numbers "
            "above are the pre-dropout per-protocol counts."
        ),
    )
    events_required: int | None = Field(
        default=None,
        description=(
            "Time-to-event only — the Schoenfeld events count the trial "
            "must observe before the analysis can be triggered."
        ),
    )
    formula_name: str = Field(
        description="Human-readable formula label (e.g. 'Two-proportions Z-test')."
    )
    formula_reference: str = Field(
        description=(
            "Citation for the formula used — textbook + chapter where "
            "applicable, otherwise the library function name."
        ),
    )
    caveats: list[str] = Field(
        default_factory=list,
        description=(
            "Methodological caveats the user must be aware of (e.g. cluster "
            "trials need a design-effect inflation; the tool reports the "
            "individually-randomised number and warns)."
        ),
    )
    notes: str | None = None


# ── Analysis plan (ICH E9) ───────────────────────────────────────────────


class InterimAnalysis(BaseModel):
    """One pre-specified interim look."""

    at_fraction: float = Field(
        gt=0.0,
        lt=1.0,
        description="Information fraction — e.g. 0.5 = halfway.",
    )
    rule: str = Field(
        description=(
            "Stopping rule short-name (e.g. 'O'Brien-Fleming superiority "
            "boundary' or 'futility per Lan-DeMets')."
        ),
    )
    decision_options: list[str] = Field(
        default_factory=list,
        description="What the DMC may recommend (stop for benefit / harm / futility / continue).",
    )


class AnalysisPlan(BaseModel):
    """Stage 3 turn — the ICH-E9-shaped methodological core.

    The user reviews + edits these fields before the document is composed.
    Stays editable across iterations until the user clicks Finalize.
    """

    kind: Literal["analysis_plan"] = "analysis_plan"

    populations_used: list[AnalysisPopulation] = Field(
        default_factory=lambda: ["ITT", "Safety"],  # type: ignore[arg-type]
        description=(
            "Pre-specified analysis populations. ITT is mandatory for "
            "superiority trials; PP is recommended alongside for "
            "non-inferiority. Safety is always at least one of these."
        ),
    )
    primary_analysis_description: str = Field(
        description=(
            "Plain-English description of the primary endpoint test "
            "(e.g. 'Log-rank test comparing intervention vs control on "
            "time to first GI bleed, stratified by enrolment site')."
        ),
    )
    primary_test_name: str = Field(
        description=(
            "The actual statistical test (Cox PH, two-sample t-test, "
            "Cochran-Mantel-Haenszel, MMRM, etc.)."
        ),
    )
    multiplicity_strategy: MultiplicityStrategy = Field(
        default="none",
        description=(
            "How alpha is allocated across multiple primary endpoints or "
            "interim analyses. 'none' is acceptable only for a single "
            "primary endpoint with no interim looks."
        ),
    )
    missing_data_strategy: MissingDataStrategy = Field(
        default="multiple_imputation_mar",
        description=(
            "Primary handling of missing primary-endpoint values. "
            "Per ICH E9 (R1), MI under MAR is the default; tipping-point "
            "sensitivity should be added when missingness is meaningful."
        ),
    )
    interim_analyses: list[InterimAnalysis] = Field(default_factory=list)
    sensitivity_analyses: list[str] = Field(
        default_factory=list,
        description=(
            "Per-line analysis variations (e.g. 'Per-protocol exclusion of "
            "protocol violators', 'On-treatment censoring rule')."
        ),
    )
    subgroup_analyses: list[str] = Field(
        default_factory=list,
        description=(
            "Pre-specified subgroups for forest plot reporting "
            "(e.g. 'Age <65 vs ≥65', 'Diabetes status')."
        ),
    )
    safety_monitoring: str | None = Field(
        default=None,
        description=(
            "DMC + SAE-reporting + stopping-for-safety summary. "
            "References the AE/SAE workflow once that ships."
        ),
    )
    notes: str | None = None


# ── Final document ───────────────────────────────────────────────────────


class SapCitation(BaseModel):
    """One reference for the SAP document.

    Same shape as sr_protocol's Citation — every reference must originate
    from a real tool call this turn.
    """

    n: int = Field(description="Bracketed citation index (1-based).")
    title: str
    authors: str | None = None
    journal: str | None = None
    year: int | None = None
    pmid: str | None = None
    doi: str | None = None
    url: str | None = None
    origin: CitationOrigin = Field(description="Which tool surfaced this reference this turn.")


class SapDocument(BaseModel):
    """Stage 4 turn — the assembled ICH-E9-shaped SAP.

    Iterable: refinements return another SapDocument with `is_final=False`.
    Clicking Finalize returns `is_final=True` and the Download CTAs appear.
    """

    kind: Literal["sap_document"] = "sap_document"

    picot: PicotTable
    sample_size: SampleSizeResult
    analysis_plan: AnalysisPlan

    background: str = Field(
        description=(
            "1–2 paragraphs framing the clinical problem + why this trial "
            "design is appropriate. Every concrete claim must be backed "
            "by an entry in `references`."
        ),
    )
    references: list[SapCitation] = Field(default_factory=list)
    full_markdown: str = Field(
        description=(
            "Complete SAP in Markdown — title, background with inline "
            "[n] citations, trial design synopsis, sample-size derivation, "
            "analysis-plan sections (populations, primary, secondary, "
            "multiplicity, missing data, interim, sensitivity, subgroup, "
            "safety), references list. Copy-paste ready for the protocol "
            "document or regulatory submission."
        ),
    )
    is_final: bool = Field(
        default=False,
        description=(
            "True after the user clicks Finalize. Enables the PDF/DOCX download CTAs in the UI."
        ),
    )
    notes: str | None = None


# ── Discriminated union ──────────────────────────────────────────────────


SapTurn = Annotated[
    ClarificationRequest | PicotTable | SampleSizeResult | AnalysisPlan | SapDocument,
    Field(discriminator="kind"),
]


__all__ = [
    "AnalysisPlan",
    "AnalysisPopulation",
    "CitationOrigin",
    "DesignType",
    "EffectAssumptions",
    "HypothesisType",
    "InterimAnalysis",
    "MissingDataStrategy",
    "MultiplicityStrategy",
    "OutcomeType",
    "PicotTable",
    "SampleSizeResult",
    "SapCitation",
    "SapDocument",
    "SapTurn",
]
