"""Pydantic schemas for the ipd (individual patient data) meta-analysis
specialist.

IPD MA pools subject-level data across trials — methodologically richer
than aggregate-effect MA because it lets you:
  • test treatment-by-subgroup interactions (the main reason researchers
    do IPD vs aggregate MA),
  • run one-stage models that share information across trials,
  • check the two-stage estimate against the one-stage estimate as a
    misspecification diagnostic.

Five-stage workflow:

  Stage 1 — ipd_intake             → research question + endpoint
  Stage 2 — ipd_bundle              → per-trial CSV + column mapping
  Stage 3 — ipd_main_results        → one-stage + two-stage pooled
                                       effects + per-trial estimates +
                                       heterogeneity
  Stage 4 — ipd_subgroup_results    → treatment × subgroup interaction
                                       (subgroup variable + per-level
                                       pooled effects + interaction p)
  Stage 5 — ipd_document            → assembled artefact, iterable

Anti-hallucination posture (same as nma + meta_analysis):
  - Per-trial estimates + pooled effects + I² + τ² + interaction p
    come from `run_ipd_analysis` sandbox runs only — never authored
    inline.
  - Trial ids in `studies_included` MUST appear in the bundle.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .common import ClarificationRequest

# ── Categorical vocabularies ────────────────────────────────────────────


EffectMeasure = Literal["OR", "RR", "MD", "SMD", "HR"]


IpdStage = Literal["one_stage", "two_stage", "subgroup"]


CompositeDirection = Literal[
    "favours_intervention",
    "favours_comparator",
    "no_difference",
    "inconclusive",
]


# ── STEP 1 — intake ─────────────────────────────────────────────────────


class IpdIntake(BaseModel):
    kind: Literal["ipd_intake"] = "ipd_intake"
    research_question: str = Field(
        description=(
            "One sentence research question (e.g. 'IPD meta-analysis of "
            "statins vs placebo on LDL change in T2DM patients across 6 RCTs')."
        )
    )
    primary_endpoint: str = Field(description="The endpoint to pool. One per IPD pass.")
    effect_measure: EffectMeasure
    notes: str = ""


# ── STEP 2 — bundle ─────────────────────────────────────────────────────


class IpdTrialEntry(BaseModel):
    """One trial's per-subject CSV + column-mapping spec."""

    trial_id: str = Field(description="Sponsor / PMID / canonical trial identifier.")
    n_subjects: int = Field(ge=0)
    treatment_column: str = Field(
        description="Column name carrying the treatment assignment (e.g. 'arm', 'trt')."
    )
    treatment_active_value: str = Field(
        description=(
            "Value in `treatment_column` that marks the active arm "
            "(e.g. '1', 'active', 'Drug A'). All other values are "
            "treated as control / reference."
        )
    )
    outcome_column: str = Field(
        description=(
            "Column name for the outcome. For binary measures it carries "
            "0/1; for continuous it carries the numeric value; for HR "
            "the time-to-event."
        )
    )
    event_column: str | None = Field(
        default=None,
        description=(
            "For HR endpoints: the column with the event indicator "
            "(0=censored, 1=event). None for non-TTE measures."
        ),
    )
    covariate_columns: list[str] = Field(
        default_factory=list,
        description="Optional baseline covariates to adjust for + subgroup candidates.",
    )
    rows_csv: str = Field(
        description=(
            "The trial's per-subject rows as CSV (with header). Bounded "
            "in size — the bundle should fit within the conversation "
            "context. Operators with very large per-trial files should "
            "use the SourceDocument upload + bundle a row-id reference."
        ),
        min_length=10,
    )


class IpdBundleTurn(BaseModel):
    """STEP 2 turn — per-trial data + column-mapping spec."""

    kind: Literal["ipd_bundle"] = "ipd_bundle"
    trials: list[IpdTrialEntry] = Field(min_length=2)

    @model_validator(mode="after")
    def _consistent_columns(self) -> IpdBundleTurn:
        if len(self.trials) < 2:
            raise ValueError("IPD MA requires ≥2 trials.")
        # Column names need NOT be identical across trials — the
        # mapping spec lets each trial use its native column names.
        # But the same `effect_measure`-implied shape is required:
        # binary → outcome_column carries 0/1; TTE → event_column set.
        return self


# ── STEP 3 — main pooled results ────────────────────────────────────────


class IpdPerTrialEffect(BaseModel):
    trial_id: str
    n_subjects: int = Field(ge=0)
    effect: float
    ci_lower: float
    ci_upper: float
    se: float = Field(
        description="Standard error on the effect-measure's natural-log "
        "scale for OR/RR/HR, or the raw scale for MD/SMD."
    )


class IpdPooledEffect(BaseModel):
    """One pooled estimate — produced once per stage (one-stage / two-stage)."""

    effect: float
    ci_lower: float
    ci_upper: float
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    n_trials: int = Field(ge=2)
    n_subjects: int = Field(ge=0)
    i_squared: float | None = Field(
        default=None, ge=0.0, le=100.0, description="Heterogeneity I² in percent (0–100)."
    )
    tau_squared: float | None = Field(
        default=None, ge=0.0, description="Between-trial variance estimate (DL)."
    )
    method: str = Field(
        description="Estimator description (e.g. 'MixedLM REML', 'DerSimonian-Laird')."
    )


class IpdMainResults(BaseModel):
    """STEP 3 turn — one-stage + two-stage side-by-side."""

    kind: Literal["ipd_main_results"] = "ipd_main_results"
    effect_measure: EffectMeasure
    one_stage: IpdPooledEffect
    two_stage: IpdPooledEffect
    per_trial: list[IpdPerTrialEffect] = Field(min_length=2)
    forest_plot_image: str | None = Field(
        default=None, description="Sandbox-rendered per-trial forest plot URL."
    )
    discrepancy_note: str = Field(
        description=(
            "1-2 sentence comparison of one-stage vs two-stage estimates. "
            "Large discrepancy (>0.2 on log-scale for binary) signals "
            "model misspecification — surface it honestly."
        )
    )


# ── STEP 4 — subgroup × treatment interaction ──────────────────────────


class IpdSubgroupLevel(BaseModel):
    """One level within the subgroup variable."""

    level_label: str = Field(description="e.g. 'Female', 'Age ≥65', 'EU region'.")
    n_trials: int = Field(ge=0)
    n_subjects: int = Field(ge=0)
    effect: float | None = None
    ci_lower: float | None = None
    ci_upper: float | None = None
    skip_reason: str | None = None


class IpdSubgroupResults(BaseModel):
    """STEP 4 turn — treatment × subgroup interaction."""

    kind: Literal["ipd_subgroup_results"] = "ipd_subgroup_results"
    subgroup_variable: str
    levels: list[IpdSubgroupLevel] = Field(min_length=2)
    interaction_p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "p for the treatment × subgroup interaction term in the "
            "one-stage model. Low (<0.10 by convention) signals "
            "effect-modification."
        ),
    )
    subgroup_forest_image: str | None = None
    notes: str = ""


# ── STEP 5 — assembled document ─────────────────────────────────────────


class IpdDocument(BaseModel):
    """The assembled IPD MA artefact."""

    kind: Literal["ipd_document"] = "ipd_document"
    intake: IpdIntake
    main_results: IpdMainResults
    subgroup_results: list[IpdSubgroupResults] = Field(default_factory=list)
    studies_included: list[str] = Field(description="trial_ids that contributed to the analysis.")
    studies_excluded: list[dict[str, str]] = Field(default_factory=list)
    interpretation: str = Field(
        description=(
            "Plain-language summary referencing the headline pooled "
            "effect, the one-stage vs two-stage agreement, and any "
            "subgroup interaction signals."
        )
    )
    direction: CompositeDirection = "inconclusive"
    caveats: list[str] = Field(default_factory=list)
    is_final: bool = False


# ── Discriminated union ─────────────────────────────────────────────────


IpdTurn = Annotated[
    ClarificationRequest
    | IpdIntake
    | IpdBundleTurn
    | IpdMainResults
    | IpdSubgroupResults
    | IpdDocument,
    Field(discriminator="kind"),
]


__all__ = [
    "ClarificationRequest",
    "CompositeDirection",
    "EffectMeasure",
    "IpdBundleTurn",
    "IpdDocument",
    "IpdIntake",
    "IpdMainResults",
    "IpdPerTrialEffect",
    "IpdPooledEffect",
    "IpdStage",
    "IpdSubgroupLevel",
    "IpdSubgroupResults",
    "IpdTrialEntry",
    "IpdTurn",
]
