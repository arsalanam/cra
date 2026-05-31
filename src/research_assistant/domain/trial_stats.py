"""Pydantic schemas for the trial_stats specialist.

Trial-specific statistical analysis — the post-lock workflow that turns
ADaM datasets into the regulator-readable analysis numbers that feed
the CSR Efficacy section.

Six-stage workflow:

  Stage 1 — trial_stats_intake          → study identity + endpoints
  Stage 2 — analysis_populations        → ITT / PP / Safety / mITT counts
  Stage 3 — time_to_event_results       → K-M medians + Cox HR + log-rank
  Stage 4 — continuous_results          → MMRM longitudinal LSMean diffs
  Stage 5 — binary_results              → response-rate diffs + Fisher/log-binomial
  Stage 6 — subgroup_results            → per-subgroup HRs + interaction p
  Stage 7 — trial_stats_document        → assembled artefact

Anti-hallucination posture (every numerical result MUST cite a sandbox
run id):
  - Every HR / median / p-value / LSMean carries a `derived_from`
    string of the form ``sandbox:<analysis-kind>:<paramcd>``.
  - The assembled document exposes ``csr_artefact_ids`` so the CSR
    drafter can cite this workflow directly (e.g.
    ``"TrialStats t-km-OS"`` becomes a valid `derived_from` source for
    EfficacyResults).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .common import ClarificationRequest

# ── Categorical vocabularies ────────────────────────────────────────────


PopulationKind = Literal["ITT", "mITT", "PP", "Safety", "custom"]


TtEParamcd = Literal[
    "OS",
    "PFS",
    "DFS",
    "EFS",
    "TTAE",
    "TTSAE",
    "DEATH",
    "OTHER",
]


ContinuousModel = Literal["MMRM", "ANCOVA", "paired_t", "Wilcoxon"]


BinaryMethod = Literal["fisher_exact", "log_binomial", "chi_square", "log_rank"]


CompositeSummary = Literal[
    "favours_intervention",
    "favours_comparator",
    "no_difference",
    "inconclusive",
]


# ── STEP 1 — intake ─────────────────────────────────────────────────────


class TrialStatsIntake(BaseModel):
    """Operator-supplied identity + endpoint roster."""

    kind: Literal["trial_stats_intake"] = "trial_stats_intake"
    study_id: str = Field(description="Sponsor's study id (e.g. 'ABC-2026-001').")
    study_name: str
    primary_endpoint: str = Field(
        description="One sentence naming the primary endpoint (e.g. 'Overall survival').",
    )
    secondary_endpoints: list[str] = Field(default_factory=list)
    arms: list[str] = Field(
        default_factory=list,
        description=(
            "Treatment-arm labels matching ADSL.TRT01A (case-sensitive). "
            "Reference arm is the first entry; the rest are compared to it."
        ),
    )
    input_shape: Literal["adam", "raw_edc"] = Field(
        default="adam",
        description=(
            "ADaM-preferred. Raw EDC requires an explicit mapping turn at "
            "intake (which columns map to USUBJID / TRT01A / AVAL / CNSR / …)."
        ),
    )
    notes: str = ""


# ── STEP 2 — analysis populations ───────────────────────────────────────


class AnalysisPopulation(BaseModel):
    """One row of the populations summary.

    `derived_from` cites the ADSL filter flag used (e.g. ``"ADSL.SAFFL='Y'"``).
    The specialist never invents counts — they come from operator pastes
    or from the sandbox population script.
    """

    kind_name: PopulationKind
    n_total: int = Field(ge=0)
    n_per_arm: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Per-arm sample size. Keys MUST match `TrialStatsIntake.arms`. "
            "Missing arms imply n=0 for that arm in this population."
        ),
    )
    derived_from: str = Field(
        description=(
            "ADSL filter flag or population definition (e.g. "
            "'ADSL.ITTFL=Y', 'ADSL.SAFFL=Y', 'PROTDEV.CRITICAL=N')."
        ),
    )
    rationale: str = Field(
        description="One-line operator-supplied justification for this population definition.",
    )


class AnalysisPopulationsTurn(BaseModel):
    """STEP 2 turn — the population roster."""

    kind: Literal["analysis_populations"] = "analysis_populations"
    populations: list[AnalysisPopulation] = Field(min_length=1)
    notes: str = ""


# ── STEP 3 — time-to-event results ──────────────────────────────────────


class TimeToEventResult(BaseModel):
    """One PARAMCD × population time-to-event analysis.

    Source numbers come from the sandbox K-M / Cox PH script (the same
    one the CDISC submission pipeline drives for ADTTE). The specialist
    NEVER writes HR / CI / log-rank p inline.
    """

    paramcd: TtEParamcd | str = Field(
        description=(
            "ADTTE-style PARAMCD (OS / PFS / DFS / EFS / TTAE / TTSAE / "
            "DEATH) or a free-text label for custom endpoints."
        ),
    )
    param_label: str = Field(
        description="Human-readable description (e.g. 'Overall survival').",
    )
    population: PopulationKind = "ITT"
    n_subjects: int = Field(ge=0)
    n_events: int = Field(ge=0)
    median_event_time: str = Field(
        description=(
            "Median time-to-event as a string with units (e.g. '14.2 months', "
            "'Not reached'). Always cite the unit explicitly."
        ),
    )
    hazard_ratio: float | None = Field(
        default=None,
        description="Cox PH HR vs reference arm. None when Cox PH could not fit.",
    )
    hr_ci_lower: float | None = None
    hr_ci_upper: float | None = None
    logrank_p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    comparison_label: str = Field(
        default="",
        description="e.g. 'Drug A vs Placebo'. Empty when single-arm.",
    )
    km_image_url: str | None = Field(
        default=None,
        description=(
            "URL to the K-M curve PNG written by the sandbox (e.g. "
            "'/images/<run-id>_km-os.png'). Inlined in the report."
        ),
    )
    derived_from: str = Field(
        description=(
            "Sandbox run id of the analysis (e.g. 'sandbox:km:OS' or "
            "'sandbox:cox:PFS'). The CSR drafter cites this as "
            "'TrialStats t-km-<paramcd>'."
        ),
    )
    skip_reason: str | None = Field(
        default=None,
        description=(
            "When the analysis could not fit (single-arm, <5 events, "
            "convergence failure), the sandbox writes the reason here. "
            "Mutually exclusive with hazard_ratio/CI."
        ),
    )

    @model_validator(mode="after")
    def _hr_xor_skip(self) -> TimeToEventResult:
        if self.hazard_ratio is not None and self.skip_reason is not None:
            raise ValueError(
                "TimeToEventResult cannot carry both a hazard_ratio and a skip_reason."
            )
        if self.hazard_ratio is not None:
            if self.hr_ci_lower is None or self.hr_ci_upper is None:
                raise ValueError(
                    "A hazard_ratio must be accompanied by 95% CI bounds."
                )
            if self.hr_ci_lower > self.hr_ci_upper:
                raise ValueError(
                    "hr_ci_lower must be ≤ hr_ci_upper."
                )
        return self


class TimeToEventResultsTurn(BaseModel):
    kind: Literal["time_to_event_results"] = "time_to_event_results"
    results: list[TimeToEventResult] = Field(default_factory=list)
    notes: str = ""


# ── STEP 4 — continuous (MMRM) results ──────────────────────────────────


class ContinuousResult(BaseModel):
    """One PARAMCD × visit MMRM (or fallback ANCOVA / paired-t) result."""

    paramcd: str = Field(description="Endpoint code (e.g. 'CHGFBL-WK24').")
    param_label: str
    population: PopulationKind = "ITT"
    visit: str = Field(description="Analysis visit (e.g. 'Week 24').")
    model: ContinuousModel = "MMRM"
    n_observed: int = Field(ge=0)
    lsmean_treatment: float | None = None
    lsmean_comparator: float | None = None
    lsmean_difference: float | None = None
    diff_ci_lower: float | None = None
    diff_ci_upper: float | None = None
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    comparison_label: str = ""
    derived_from: str = Field(
        description="Sandbox run id (e.g. 'sandbox:mmrm:CHGFBL-WK24').",
    )
    skip_reason: str | None = None

    @model_validator(mode="after")
    def _diff_xor_skip(self) -> ContinuousResult:
        if self.lsmean_difference is not None and self.skip_reason is not None:
            raise ValueError(
                "ContinuousResult cannot carry both an LSMean difference and a skip_reason."
            )
        if self.lsmean_difference is not None and (
            self.diff_ci_lower is None or self.diff_ci_upper is None
        ):
            raise ValueError(
                "An LSMean difference must carry 95% CI bounds."
            )
        return self


class ContinuousResultsTurn(BaseModel):
    kind: Literal["continuous_results"] = "continuous_results"
    results: list[ContinuousResult] = Field(default_factory=list)
    notes: str = ""


# ── STEP 5 — binary endpoint results ────────────────────────────────────


class BinaryResult(BaseModel):
    """One binary endpoint analysis (response, mortality, recurrence)."""

    paramcd: str
    param_label: str
    population: PopulationKind = "ITT"
    method: BinaryMethod
    n_treatment: int = Field(ge=0)
    events_treatment: int = Field(ge=0)
    n_comparator: int = Field(ge=0)
    events_comparator: int = Field(ge=0)
    risk_difference: float | None = None
    rd_ci_lower: float | None = None
    rd_ci_upper: float | None = None
    risk_ratio: float | None = None
    rr_ci_lower: float | None = None
    rr_ci_upper: float | None = None
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    comparison_label: str = ""
    derived_from: str = Field(
        description="Sandbox run id (e.g. 'sandbox:fisher:ORR').",
    )
    skip_reason: str | None = None

    @model_validator(mode="after")
    def _event_bounds(self) -> BinaryResult:
        if self.events_treatment > self.n_treatment:
            raise ValueError("events_treatment cannot exceed n_treatment.")
        if self.events_comparator > self.n_comparator:
            raise ValueError("events_comparator cannot exceed n_comparator.")
        return self


class BinaryResultsTurn(BaseModel):
    kind: Literal["binary_results"] = "binary_results"
    results: list[BinaryResult] = Field(default_factory=list)
    notes: str = ""


# ── STEP 6 — subgroup analyses ──────────────────────────────────────────


class SubgroupRow(BaseModel):
    """One subgroup × parent-PARAMCD HR row."""

    subgroup_label: str = Field(description="e.g. 'Female', 'Age ≥65'.")
    n: int = Field(ge=0)
    n_events: int = Field(ge=0)
    effect: float | None = Field(
        default=None,
        description="Subgroup HR (time-to-event) or RR (binary).",
    )
    ci_lower: float | None = None
    ci_upper: float | None = None
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)


class SubgroupAnalysis(BaseModel):
    """One parent endpoint's subgroup analysis (forest plot)."""

    parent_paramcd: str
    parent_param_label: str
    subgroup_variable: str = Field(
        description="The pre-specified subgroup factor (e.g. 'Sex', 'Age group').",
    )
    rows: list[SubgroupRow] = Field(min_length=2)
    interaction_p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Treatment-by-subgroup interaction p from the Cox / GLM model "
            "with the interaction term. Low p (<0.10 by convention) signals "
            "effect-modification; absence does not prove no interaction."
        ),
    )
    derived_from: str
    notes: str = ""


class SubgroupResultsTurn(BaseModel):
    kind: Literal["subgroup_results"] = "subgroup_results"
    analyses: list[SubgroupAnalysis] = Field(default_factory=list)
    notes: str = ""


# ── STEP 6.5 — per-subject visualisations (waterfall + swimmer) ─────────


WaterfallResponseCategory = Literal["CR", "PR", "SD", "PD"]


class WaterfallSubject(BaseModel):
    """One subject's best response, sorted into the plot by the sandbox."""

    usubjid: str
    best_change_pct: float = Field(
        description="Best % change from baseline (e.g. -42.1 = 42% reduction)."
    )
    treatment: str = ""


class WaterfallResult(BaseModel):
    """Per-outcome waterfall plot — sandbox-rendered.

    Response categories follow RECIST 1.1 thresholds:
      • CR (Complete Response): −100%
      • PR (Partial Response):  ≤ −30%
      • SD (Stable Disease):    between PR and PD
      • PD (Progressive Disease): ≥ +20%
    """

    outcome_label: str
    n_subjects: int = Field(ge=0)
    subjects: list[WaterfallSubject] = Field(default_factory=list)
    response_counts: dict[WaterfallResponseCategory, int] = Field(default_factory=dict)
    waterfall_image_url: str | None = None
    derived_from: str = Field(
        description=(
            "Sandbox run id (e.g. 'sandbox:waterfall:RECIST'). The CSR drafter "
            "cites this as 'TrialStats waterfall-<outcome>'."
        )
    )


SwimmerEventKind = Literal[
    "response_onset",
    "pr",
    "cr",
    "progression",
    "death",
    "off_treatment",
]


class SwimmerEvent(BaseModel):
    day: int = Field(ge=0)
    kind: SwimmerEventKind


class SwimmerSubject(BaseModel):
    usubjid: str
    treatment: str = ""
    duration_days: int = Field(ge=0)
    ongoing: bool = False
    events: list[SwimmerEvent] = Field(default_factory=list)


class SwimmerResult(BaseModel):
    """Per-cohort swimmer plot — treatment timeline + event markers."""

    outcome_label: str
    n_subjects: int = Field(ge=0)
    subjects: list[SwimmerSubject] = Field(default_factory=list)
    swimmer_image_url: str | None = None
    derived_from: str = Field(
        description=(
            "Sandbox run id (e.g. 'sandbox:swimmer:cohort'). The CSR drafter "
            "cites this as 'TrialStats swimmer-<outcome>'."
        )
    )


class SubjectVisualizationsTurn(BaseModel):
    """STEP 6.5 turn — optional. Per-subject waterfall + swimmer plots.

    Entered between subgroup_results and the assembled document when the
    operator wants oncology-style per-subject visualisations. Both
    fields are optional — emit waterfall alone, swimmer alone, or both
    in one turn.
    """

    kind: Literal["subject_visualisations"] = "subject_visualisations"
    waterfall: list[WaterfallResult] = Field(default_factory=list)
    swimmer: list[SwimmerResult] = Field(default_factory=list)
    notes: str = ""


# ── STEP 7 — assembled document ─────────────────────────────────────────


class TrialStatsDocument(BaseModel):
    """The assembled trial-stats artefact.

    `csr_artefact_ids` lists the source-artefact ids the CSR drafter can
    cite via `EfficacyResults.primary_endpoint_derived_from`. They follow
    the convention ``TrialStats <kind>-<paramcd>`` so the CSR audit
    trail can locate this workflow at any later point.
    """

    kind: Literal["trial_stats_document"] = "trial_stats_document"
    intake: TrialStatsIntake
    populations: list[AnalysisPopulation] = Field(min_length=1)
    time_to_event: list[TimeToEventResult] = Field(default_factory=list)
    continuous: list[ContinuousResult] = Field(default_factory=list)
    binary: list[BinaryResult] = Field(default_factory=list)
    subgroup: list[SubgroupAnalysis] = Field(default_factory=list)
    waterfall: list[WaterfallResult] = Field(default_factory=list)
    swimmer: list[SwimmerResult] = Field(default_factory=list)
    primary_summary: CompositeSummary = Field(
        default="inconclusive",
        description=(
            "Operator-supplied headline interpretation of the primary "
            "endpoint result. Reuses the CSR convention so the handoff "
            "stays type-compatible."
        ),
    )
    primary_summary_paragraph: str = Field(
        default="[Operator to complete — primary-endpoint plain-language paragraph]",
        description=(
            "2-4 sentence plain-language summary of the primary-endpoint "
            "result, citing the relevant TimeToEventResult / ContinuousResult / "
            "BinaryResult by paramcd. NEVER writes the effect size inline — "
            "the schema rows carry the numbers."
        ),
    )
    is_final: bool = False

    @property
    def csr_artefact_ids(self) -> list[str]:
        """Source-artefact ids the CSR drafter can cite."""
        ids: list[str] = []
        ids.extend(f"TrialStats t-km-{r.paramcd}" for r in self.time_to_event)
        ids.extend(f"TrialStats mmrm-{r.paramcd}" for r in self.continuous)
        ids.extend(f"TrialStats binary-{r.paramcd}" for r in self.binary)
        ids.extend(
            f"TrialStats subgroup-{a.parent_paramcd}-by-{a.subgroup_variable}"
            for a in self.subgroup
        )
        ids.extend(f"TrialStats waterfall-{w.outcome_label}" for w in self.waterfall)
        ids.extend(f"TrialStats swimmer-{s.outcome_label}" for s in self.swimmer)
        return ids


# ── Discriminated union for the agent's output ──────────────────────────


TrialStatsTurn = Annotated[
    ClarificationRequest
    | TrialStatsIntake
    | AnalysisPopulationsTurn
    | TimeToEventResultsTurn
    | ContinuousResultsTurn
    | BinaryResultsTurn
    | SubgroupResultsTurn
    | SubjectVisualizationsTurn
    | TrialStatsDocument,
    Field(discriminator="kind"),
]


__all__ = [
    "AnalysisPopulation",
    "AnalysisPopulationsTurn",
    "BinaryMethod",
    "BinaryResult",
    "BinaryResultsTurn",
    "ClarificationRequest",
    "CompositeSummary",
    "ContinuousModel",
    "ContinuousResult",
    "ContinuousResultsTurn",
    "PopulationKind",
    "SubgroupAnalysis",
    "SubgroupResultsTurn",
    "SubgroupRow",
    "SubjectVisualizationsTurn",
    "SwimmerEvent",
    "SwimmerEventKind",
    "SwimmerResult",
    "SwimmerSubject",
    "TimeToEventResult",
    "TimeToEventResultsTurn",
    "TrialStatsDocument",
    "TrialStatsIntake",
    "TrialStatsTurn",
    "TtEParamcd",
    "WaterfallResponseCategory",
    "WaterfallResult",
    "WaterfallSubject",
]
