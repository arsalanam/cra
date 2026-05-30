"""Pydantic schemas for the csr_drafter specialist.

ICH E3 Clinical Study Report — first slice covers the synopsis +
the four data-driven sections (Subject Disposition / Demographics /
Efficacy Evaluation / Safety Evaluation). Narrative sections
(Introduction / Discussion / Overall Conclusions) ship as
`[Operator to complete]` placeholders in the assembled document;
the next slice will draft them.

Anti-hallucination posture:
  - Patient counts MUST come from the ADSL `Counts` block the
    operator pastes — never from the agent's recall.
  - Effect sizes / p-values MUST cite a specific TLF artefact id.
  - Sponsor PHI (investigator names, IRB approval numbers) stays
    `[SPONSOR INPUT]` rather than fabricated.
  - References for the synopsis background cite design / regulatory
    guidance only — no PMIDs.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest

# ── Categorical vocabularies ────────────────────────────────────────────


BlindingState = Literal[
    "open_label",
    "single_blind",
    "double_blind",
    "triple_blind",
]


PrimaryResultDirection = Literal[
    "favours_intervention",
    "favours_comparator",
    "no_difference",
    "inconclusive",
]


# ── STEP 1 — intake ─────────────────────────────────────────────────────


class CsrIntake(BaseModel):
    kind: Literal["csr_intake"] = "csr_intake"
    study_id: str = Field(description="Sponsor's study id (e.g. 'ABC-2026-001').")
    study_name: str = Field(description="Brief title — usually mirrors CT.gov.")
    sponsor: str
    blinding: BlindingState
    lock_date: str | None = Field(
        default=None,
        description="ISO date the database was locked (E7 study-lock event).",
    )
    target_jurisdictions: list[str] = Field(
        default_factory=list,
        description="Where the CSR will be submitted (FDA / EMA / PMDA / …).",
    )
    notes: str = ""


# ── STEP 2 — synopsis ───────────────────────────────────────────────────


class CsrSynopsis(BaseModel):
    """1-2 page CSR synopsis (ICH E3 §1).

    Required fields enforce a regulator-readable shape even before the
    sections section lands. The synopsis is independently downloadable —
    most CSR readers (regulators, agencies, journals) read this first.
    """

    kind: Literal["csr_synopsis"] = "csr_synopsis"
    title: str
    sponsor: str
    protocol_id: str | None = None
    objectives_text: str = Field(
        description="Primary + key secondary objectives, 2-4 sentences."
    )
    methodology_text: str = Field(
        description="Design (allocation, blinding, masking), 3-6 sentences."
    )
    number_planned: int = Field(gt=0)
    number_analysed_safety: int = Field(ge=0)
    number_analysed_efficacy: int = Field(ge=0)
    primary_endpoint: str
    primary_result_description: str = Field(
        description=(
            "Plain-language description of the primary endpoint result. "
            "MUST cite the TLF or ADTTE row it came from in `derived_from`."
        )
    )
    primary_result_direction: PrimaryResultDirection
    derived_from: str = Field(
        description=(
            "Reference back to the source artefact for the primary result "
            "(e.g. 'TLF t-tte-summary' or 'ADTTE PARAMCD=TTAE row')."
        )
    )
    safety_overview: str = Field(
        description="2-4 sentences summarising AE / SAE / death counts."
    )
    conclusions: str = Field(
        description="1-2 sentence headline; reuse `[Operator to complete]` if data is mid-clean."
    )
    notes: str = ""


# ── STEP 3 — data sections ──────────────────────────────────────────────


class DispositionRow(BaseModel):
    label: str
    n: int = Field(ge=0)
    pct: str = Field(description="Percentage as a formatted string (e.g. '42.5%').")


class DispositionTable(BaseModel):
    """ICH E3 §10.1 — subject disposition.

    Rows are operator-supplied counts from ADSL (or paste-able from the
    Disposition TLF). The agent never invents row counts.
    """

    rows: list[DispositionRow] = Field(min_length=1)
    derived_from: str = Field(
        description="Source artefact id, e.g. 'TLF t-disposition'."
    )


class DemographicsRow(BaseModel):
    characteristic: str
    value: str
    detail: str | None = None


class DemographicsTable(BaseModel):
    """ICH E3 §10.2 — demographics + baseline characteristics."""

    rows: list[DemographicsRow] = Field(min_length=1)
    derived_from: str = Field(
        description="Source artefact id, e.g. 'TLF t-demographics'."
    )


class EfficacyResults(BaseModel):
    """ICH E3 §11 — efficacy evaluation.

    For each ADTTE parameter (or other efficacy endpoint), capture the
    description + the TLF reference. Effect sizes / p-values MUST be
    cited via `derived_from` — the agent cannot inline-write them
    without a source.
    """

    primary_endpoint_text: str
    primary_endpoint_derived_from: str
    secondary_endpoints: list[str] = Field(default_factory=list)
    secondary_endpoints_derived_from: list[str] = Field(default_factory=list)
    populations_analysed: list[str] = Field(
        default_factory=list,
        description="e.g. ['ITT', 'Per-protocol', 'Safety'].",
    )
    notes: str = ""


class SafetyOverview(BaseModel):
    """ICH E3 §12 — safety evaluation summary.

    Counts come from the AE-summary TLF + SAE-overdue endpoint. The
    agent never invents totals.
    """

    total_ae_events: int = Field(ge=0)
    subjects_with_any_ae: int = Field(ge=0)
    total_saes: int = Field(ge=0)
    deaths: int = Field(ge=0)
    discontinuations_due_to_ae: int = Field(ge=0)
    top_aes_text: str = Field(
        description=(
            "Plain-language sentence naming the top-3 AEs by frequency, "
            "with their counts. MUST cite the AE-frequency figure."
        )
    )
    derived_from: str = Field(
        description="Source artefact id, e.g. 'TLF t-ae-summary'."
    )


class CsrDataSections(BaseModel):
    """STEP 3 turn shape — the four data-driven sections together."""

    kind: Literal["csr_data_sections"] = "csr_data_sections"
    disposition: DispositionTable
    demographics: DemographicsTable
    efficacy: EfficacyResults
    safety: SafetyOverview
    notes: str = ""


# ── STEP 4 — assembled document ─────────────────────────────────────────


class CsrDocument(BaseModel):
    """The assembled CSR.

    Narrative sections (introduction / discussion / conclusions) are
    `[Operator to complete]` placeholders in this slice — the next slice
    drafts them. Iterable: refinement requests return another
    CsrDocument with `is_final=False` until the user types "Finalize CSR".
    """

    kind: Literal["csr_document"] = "csr_document"
    intake: CsrIntake
    synopsis: CsrSynopsis
    data_sections: CsrDataSections
    background_text: str = Field(
        default="[Operator to complete — Introduction / Background]",
        description=(
            "ICH E3 §6 Introduction. Deferred to the narrative slice; "
            "until then this stays as a placeholder."
        ),
    )
    discussion_text: str = Field(
        default="[Operator to complete — Discussion]",
        description="ICH E3 §13 Discussion — deferred to the narrative slice.",
    )
    conclusions_text: str = Field(
        default="[Operator to complete — Overall Conclusions]",
        description="ICH E3 §14 — deferred to the narrative slice.",
    )
    is_final: bool = False


# ── Discriminated union for the agent's output ──────────────────────────


CsrTurn = Annotated[
    ClarificationRequest
    | CsrIntake
    | CsrSynopsis
    | CsrDataSections
    | CsrDocument,
    Field(discriminator="kind"),
]


__all__ = [
    "BlindingState",
    "ClarificationRequest",
    "CsrDataSections",
    "CsrDocument",
    "CsrIntake",
    "CsrSynopsis",
    "CsrTurn",
    "DemographicsRow",
    "DemographicsTable",
    "DispositionRow",
    "DispositionTable",
    "EfficacyResults",
    "PrimaryResultDirection",
    "SafetyOverview",
]
