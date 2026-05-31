"""Pydantic schemas for the nma (network meta-analysis) specialist.

NMA pools evidence across 3+ interventions using both direct (head-to-
head trials) and indirect (through a common comparator) evidence —
methodologically distinct from pairwise meta-analysis.

Five-stage workflow:

  Stage 1 — nma_intake          → multi-arm clinical question
  Stage 2 — nma_pico             → PicoNetwork (≥3 interventions)
  Stage 3 — nma_search_results   → studies retrieved + flagged direct
                                     pairs they contribute to
  Stage 4 — nma_data_extraction  → per-study × per-pair arm data
  Stage 5 — nma_results          → league table + SUCRA + network +
                                     interpretation

Anti-hallucination posture mirrors meta_analysis:
  - PMIDs come from `search_papers` tool calls only.
  - Pooled effects + CIs + SUCRA come from `run_nma_analysis` sandbox
    runs only — never authored inline.
  - Consistency / transitivity assumptions surfaced in the system
    prompt; violations are reported in `caveats`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .common import ClarificationRequest

# ── Categorical vocabularies ────────────────────────────────────────────


EffectMeasure = Literal["OR", "RR", "MD", "SMD", "HR"]


NmaBackend = Literal["frequentist", "bayesian"]


CompositeDirection = Literal[
    "favours_intervention",
    "favours_comparator",
    "no_difference",
    "inconclusive",
]


# ── STEP 1 — intake ─────────────────────────────────────────────────────


class NmaIntake(BaseModel):
    kind: Literal["nma_intake"] = "nma_intake"
    research_question: str = Field(
        description=(
            "One sentence; mention ≥3 interventions "
            "(e.g. 'Compare 5 DOACs for AF stroke prevention')."
        )
    )
    notes: str = ""


# ── STEP 2 — multi-arm PICO ─────────────────────────────────────────────


class PicoNetwork(BaseModel):
    """PICO with a list of interventions (≥3) instead of a single arm
    + comparator. The reference intervention is the first entry; the
    league table reports all pairwise effects vs reference + against
    each other.
    """

    population: str
    interventions: list[str] = Field(
        min_length=3,
        description=(
            "Ordered list of interventions (≥3). First entry is the "
            "reference comparator used in the league table."
        ),
    )
    outcome: str = Field(description="Primary outcome; one per NMA pass.")
    effect_measure: EffectMeasure
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    study_types: list[str] = Field(default_factory=list)
    notes: str = ""


class NmaPicoTurn(BaseModel):
    kind: Literal["nma_pico"] = "nma_pico"
    pico: PicoNetwork
    rationale: str = Field(
        description="Explain comparator selection + transitivity assumption."
    )


# ── STEP 3 — search results ─────────────────────────────────────────────


class NmaStudyCandidate(BaseModel):
    """One study that contributes to ≥1 direct comparison in the network."""

    source: str
    source_id: str
    pmid: str | None = None
    doi: str | None = None
    title: str
    year: int | None = None
    journal: str | None = None
    arms_evaluated: list[str] = Field(
        description=(
            "Subset of `PicoNetwork.interventions` this study contributes "
            "data on. Length ≥2 (single-arm studies don't enter NMA)."
        ),
    )
    relevance_note: str = ""

    @model_validator(mode="after")
    def _at_least_two_arms(self) -> NmaStudyCandidate:
        if len(self.arms_evaluated) < 2:
            raise ValueError(
                "NmaStudyCandidate.arms_evaluated must list ≥2 arms (single-arm "
                "studies don't enter NMA)."
            )
        return self


class NmaSearchResults(BaseModel):
    kind: Literal["nma_search_results"] = "nma_search_results"
    pubmed_query: str
    studies: list[NmaStudyCandidate]
    total_found: int
    notes: str = ""


# ── STEP 4 — per-study arm extraction ───────────────────────────────────


class NmaArmData(BaseModel):
    """One arm's data within a study."""

    intervention: str
    n: int = Field(ge=0)
    events: int | None = Field(
        default=None,
        description="Required for binary measures (OR/RR/HR); leave None for MD/SMD.",
    )
    mean: float | None = None
    sd: float | None = None


class NmaStudyExtractedData(BaseModel):
    """Per-study arm data — all arms in one study row."""

    source: str
    source_id: str
    pmid: str | None = None
    arms: list[NmaArmData] = Field(min_length=2)
    extraction_notes: str = ""
    is_complete: bool = True


class NmaDataExtraction(BaseModel):
    kind: Literal["nma_data_extraction"] = "nma_data_extraction"
    studies: list[NmaStudyExtractedData] = Field(min_length=2)
    summary: str = Field(
        description="Cross-study completeness, network connectedness check."
    )


# ── STEP 5 — assembled NMA results ──────────────────────────────────────


class LeagueRow(BaseModel):
    """One cell of the league table — pairwise effect estimate.

    `row_intervention` vs `col_intervention` with `effect` on the
    effect_measure scale. `direct` is True when at least one trial
    directly compared this pair (head-to-head); False = indirect-only.
    """

    row_intervention: str
    col_intervention: str
    effect: float
    ci_lower: float
    ci_upper: float
    n_direct_trials: int = Field(ge=0)
    n_indirect_paths: int = Field(ge=0)


class LeagueTable(BaseModel):
    """All-vs-all pairwise effects. The diagonal (i,i) is omitted; the
    table is symmetric in the effect_measure's natural-log scale (i.e.
    flipping rows and columns inverts an OR/RR/HR or negates an MD/SMD).
    """

    effect_measure: EffectMeasure
    rows: list[LeagueRow]


class SucraRow(BaseModel):
    """SUCRA (Surface Under the Cumulative RAnking) — closer to 1 means
    higher-ranked. Treatment ranking: rank 1 = best."""

    intervention: str
    sucra: float = Field(ge=0.0, le=1.0)
    mean_rank: float
    rank: int = Field(ge=1)


class NetworkNode(BaseModel):
    intervention: str
    n_studies: int = Field(ge=0)
    n_participants: int = Field(ge=0)


class NetworkEdge(BaseModel):
    """One edge of the network plot — counts head-to-head trials."""

    source_intervention: str
    target_intervention: str
    n_trials: int = Field(ge=1)


class NetworkGraph(BaseModel):
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]
    image_url: str | None = Field(
        default=None,
        description="URL to the sandbox-rendered network-geometry PNG.",
    )


class NmaResults(BaseModel):
    """Final assembled NMA artefact."""

    kind: Literal["nma_results"] = "nma_results"
    pico: PicoNetwork
    backend: NmaBackend
    league_table: LeagueTable
    sucra: list[SucraRow] = Field(min_length=3)
    network: NetworkGraph
    studies_included: list[str] = Field(
        description="PMIDs (or source ids) contributing to the analysis."
    )
    studies_excluded: list[dict[str, str]] = Field(default_factory=list)
    interpretation: str = Field(
        description=(
            "Plain-language summary referencing the best-ranked "
            "intervention by SUCRA + the consistency / transitivity caveats."
        )
    )
    caveats: list[str] = Field(default_factory=list)


# ── Discriminated union for the agent's output ──────────────────────────


NmaTurn = Annotated[
    ClarificationRequest
    | NmaIntake
    | NmaPicoTurn
    | NmaSearchResults
    | NmaDataExtraction
    | NmaResults,
    Field(discriminator="kind"),
]


__all__ = [
    "ClarificationRequest",
    "CompositeDirection",
    "EffectMeasure",
    "LeagueRow",
    "LeagueTable",
    "NetworkEdge",
    "NetworkGraph",
    "NetworkNode",
    "NmaArmData",
    "NmaBackend",
    "NmaDataExtraction",
    "NmaIntake",
    "NmaPicoTurn",
    "NmaResults",
    "NmaSearchResults",
    "NmaStudyCandidate",
    "NmaStudyExtractedData",
    "NmaTurn",
    "PicoNetwork",
    "SucraRow",
]
