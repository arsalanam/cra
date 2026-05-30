"""Pydantic schemas for the grade_drafter specialist.

GRADE (Grading of Recommendations Assessment, Development and Evaluation)
Summary of Findings tables + PRISMA 2020 reporting checklist — both
journal-mandated for SR/MA submissions.

GRADE rates the certainty of evidence per outcome on four levels
(High / Moderate / Low / Very Low). Start point: RCTs = High,
observational = Low. Then apply downgrade / upgrade reasons per the
GRADE Handbook (Schünemann et al. 2013).

Downgrade domains (apply to all designs):
  1. Risk of bias        — across-study RoB
  2. Inconsistency       — heterogeneity (I² > 50% suggests serious)
  3. Indirectness        — PICO mismatch
  4. Imprecision         — wide CI / OIS not met / CI crosses null
  5. Publication bias    — funnel asymmetry / Egger's test

Upgrade domains (observational only):
  1. Large effect        — RR > 2 or < 0.5
  2. Dose-response       — gradient observed
  3. Residual confounding — would reduce, not inflate, the observed
                              effect

Anti-hallucination posture:
  - Every downgrade rating MUST cite the source number (I² value, CI
    bounds, n_studies, etc) — drawn from the operator-supplied
    meta-analysis JSON paste, never from the agent's recall.
  - Certainty is COMPUTED from the downgrade/upgrade ratings via
    `OutcomeAssessment.compute_certainty()` — the agent cannot
    override it.
  - PRISMA item locations (page/line) come from the operator, not
    fabricated.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field

from .common import ClarificationRequest

# ── Categorical vocabularies ────────────────────────────────────────────


StudyDesign = Literal["rct", "observational", "mixed"]

DowngradeLevel = Literal["none", "serious", "very_serious"]

UpgradeLevel = Literal["none", "moderate", "large"]

CertaintyLevel = Literal["high", "moderate", "low", "very_low"]

ImportanceRating = Literal["critical", "important", "not_important"]

YesNoNA = Literal["yes", "no", "not_applicable"]


# ── STEP 1 — intake ─────────────────────────────────────────────────────


class OutcomeSpec(BaseModel):
    """Operator-provided outcome description from the SR/MA."""

    name: str
    importance: ImportanceRating = "critical"
    study_design: StudyDesign = "rct"


class GradeIntake(BaseModel):
    kind: Literal["grade_intake"] = "grade_intake"
    research_question: str
    outcomes_to_assess: list[OutcomeSpec] = Field(min_length=1)
    ma_reference: str | None = Field(
        default=None,
        description=(
            "Free-text pointer to the source meta-analysis (thread id, "
            "PMID list, or 'pasted in next turn'). The downgrade ratings "
            "pull their evidence from this source."
        ),
    )
    notes: str = ""


# ── STEP 2 — per-outcome assessment ─────────────────────────────────────


class DowngradeReason(BaseModel):
    """One of the 5 GRADE downgrade domains."""

    level: DowngradeLevel
    rationale: str = Field(
        description=(
            "MUST cite a source number from the meta-analysis (e.g. "
            "'I²=78% across 8 RCTs' or 'CI 0.45-2.13 crosses the null'). "
            "Empty/handwavy rationales are not acceptable."
        )
    )


class UpgradeReason(BaseModel):
    """One of the 3 GRADE upgrade domains (observational only)."""

    level: UpgradeLevel
    rationale: str = Field(
        description="MUST cite a source number (e.g. 'pooled RR=3.2' for large effect)."
    )


class OutcomeAssessment(BaseModel):
    """Per-outcome GRADE assessment.

    Certainty is computed from the downgrade/upgrade ratings — the
    agent cannot override it.
    """

    kind: Literal["outcome_assessment"] = "outcome_assessment"
    outcome_name: str
    study_design: StudyDesign
    n_studies: int = Field(ge=0)
    n_participants: int = Field(ge=0)
    effect_estimate: str = Field(
        description="Pooled effect (e.g. 'RR 0.72'). Comes from the meta-analysis paste."
    )
    confidence_interval: str = Field(
        description="95% CI as a string (e.g. '0.58 to 0.89')."
    )

    # 5 downgrade domains.
    risk_of_bias: DowngradeReason
    inconsistency: DowngradeReason
    indirectness: DowngradeReason
    imprecision: DowngradeReason
    publication_bias: DowngradeReason

    # 3 upgrade domains — observational only. Schema-enforced via the
    # validator below.
    large_effect: UpgradeReason | None = None
    dose_response: UpgradeReason | None = None
    residual_confounding: UpgradeReason | None = None

    importance: ImportanceRating = "critical"
    notes: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def certainty(self) -> CertaintyLevel:
        """Compute certainty from the downgrade/upgrade pattern.

        Start: RCT/mixed = high (=4), observational = low (=2).
        Downgrade: each 'serious' = -1, each 'very_serious' = -2.
        Upgrade (observational only): each 'moderate' = +1, 'large' = +2.
        Clamp to [1, 4] then map to certainty level.
        """
        score = 4 if self.study_design in ("rct", "mixed") else 2

        for d in (
            self.risk_of_bias,
            self.inconsistency,
            self.indirectness,
            self.imprecision,
            self.publication_bias,
        ):
            if d.level == "serious":
                score -= 1
            elif d.level == "very_serious":
                score -= 2

        if self.study_design == "observational":
            for u in (
                self.large_effect,
                self.dose_response,
                self.residual_confounding,
            ):
                if u is None:
                    continue
                if u.level == "moderate":
                    score += 1
                elif u.level == "large":
                    score += 2

        score = max(1, min(4, score))
        mapping: dict[int, CertaintyLevel] = {
            4: "high",
            3: "moderate",
            2: "low",
            1: "very_low",
        }
        return mapping[score]


# ── STEP 3 — Summary of Findings ────────────────────────────────────────


class SofRow(BaseModel):
    """One row in the GRADE Summary of Findings table.

    Built from an OutcomeAssessment; the report module assembles the
    table from a list of these.
    """

    outcome_name: str
    n_studies: int
    n_participants: int
    effect_estimate: str
    confidence_interval: str
    certainty: CertaintyLevel
    importance: ImportanceRating
    comments: str = ""


class SofTable(BaseModel):
    kind: Literal["sof_table"] = "sof_table"
    research_question: str
    rows: list[SofRow] = Field(min_length=1)
    notes: str = ""


# ── STEP 4 — PRISMA 2020 reporting checklist ────────────────────────────


# 27 items from the PRISMA 2020 statement (Page et al., BMJ 2021).
# Item ids match the published statement so the report renders the
# regulator-canonical numbering.
PRISMA_2020_ITEMS: list[tuple[str, str, str]] = [
    # (section, id, item text)
    ("Title", "1", "Identify the report as a systematic review."),
    ("Abstract", "2", "See PRISMA 2020 for Abstracts checklist."),
    ("Introduction", "3", "Rationale: describe the rationale for the review in the context of existing knowledge."),
    ("Introduction", "4", "Objectives: provide an explicit statement of the objective(s) or question(s) the review addresses."),
    ("Methods", "5", "Eligibility criteria: specify the inclusion and exclusion criteria for the review."),
    ("Methods", "6", "Information sources: specify all databases, registers, websites, organisations, reference lists and other sources searched."),
    ("Methods", "7", "Search strategy: present the full search strategies for all databases, registers and websites, including any filters and limits used."),
    ("Methods", "8", "Selection process: specify the methods used to decide whether a study met the inclusion criteria."),
    ("Methods", "9", "Data collection process: specify the methods used to collect data from reports."),
    ("Methods", "10a", "Data items: list and define all outcomes for which data were sought."),
    ("Methods", "10b", "Data items: list and define all other variables for which data were sought."),
    ("Methods", "11", "Study risk of bias assessment: specify the methods used to assess RoB in the included studies."),
    ("Methods", "12", "Effect measures: specify for each outcome the effect measure(s) used in the synthesis or presentation of results."),
    ("Methods", "13a", "Synthesis methods: describe the processes used to decide which studies were eligible for each synthesis."),
    ("Methods", "13b", "Synthesis methods: describe any methods required to prepare the data for presentation or synthesis."),
    ("Methods", "13c", "Synthesis methods: describe any methods used to tabulate or visually display results of individual studies and syntheses."),
    ("Methods", "13d", "Synthesis methods: describe any methods used to synthesize results and provide a rationale for the choice(s)."),
    ("Methods", "13e", "Synthesis methods: describe any methods used to explore possible causes of heterogeneity among study results."),
    ("Methods", "13f", "Synthesis methods: describe any sensitivity analyses conducted to assess robustness of the synthesized results."),
    ("Methods", "14", "Reporting bias assessment: describe any methods used to assess risk of bias due to missing results in a synthesis."),
    ("Methods", "15", "Certainty assessment: describe any methods used to assess certainty (or confidence) in the body of evidence for an outcome (GRADE)."),
    ("Results", "16a", "Study selection: describe the results of the search and selection process, from the number of records identified in the search to the number of studies included in the review."),
    ("Results", "16b", "Study selection: cite studies that might appear to meet the inclusion criteria, but which were excluded, and explain why they were excluded."),
    ("Results", "17", "Study characteristics: cite each included study and present its characteristics."),
    ("Results", "18", "Risk of bias in studies: present assessments of risk of bias for each included study."),
    ("Results", "19", "Results of individual studies: for all outcomes, present, for each study (a) summary statistics for each group and (b) an effect estimate and its precision."),
    ("Results", "20a", "Results of syntheses: for each synthesis, briefly summarise the characteristics and risk of bias among contributing studies."),
    ("Results", "20b", "Results of syntheses: present results of all statistical syntheses conducted."),
    ("Results", "20c", "Results of syntheses: present results of all investigations of possible causes of heterogeneity among study results."),
    ("Results", "20d", "Results of syntheses: present results of all sensitivity analyses conducted to assess robustness of the synthesized results."),
    ("Results", "21", "Reporting biases: present assessments of risk of bias due to missing results for each synthesis assessed."),
    ("Results", "22", "Certainty of evidence: present assessments of certainty (or confidence) in the body of evidence for each outcome assessed."),
    ("Discussion", "23a", "Discussion: provide a general interpretation of the results in the context of other evidence."),
    ("Discussion", "23b", "Discussion: discuss any limitations of the evidence included in the review."),
    ("Discussion", "23c", "Discussion: discuss any limitations of the review processes used."),
    ("Discussion", "23d", "Discussion: discuss implications of the results for practice, policy, and future research."),
    ("Other information", "24a", "Registration and protocol: provide registration information for the review, including the register name and registration number, or state that the review was not registered."),
    ("Other information", "24b", "Registration and protocol: indicate where the review protocol can be accessed, or state that a protocol was not prepared."),
    ("Other information", "24c", "Registration and protocol: describe and explain any amendments to information provided at registration or in the protocol."),
    ("Other information", "25", "Support: describe sources of financial or non-financial support for the review, and the role of the funders or sponsors in the review."),
    ("Other information", "26", "Competing interests: declare any competing interests of review authors."),
    ("Other information", "27", "Availability of data, code and other materials: report which of the following are publicly available and where they can be found."),
]


class PrismaItem(BaseModel):
    section: str
    item_id: str
    item_text: str
    reported: YesNoNA = "no"
    location: str = Field(
        default="",
        description="Page / paragraph / section reference in the manuscript.",
    )
    notes: str = ""


class PrismaChecklist(BaseModel):
    kind: Literal["prisma_checklist"] = "prisma_checklist"
    review_title: str
    items: list[PrismaItem] = Field(
        min_length=27,
        description=(
            "All 27 PRISMA 2020 items (some have sub-items: 10a/b, 13a-f, "
            "16a/b, 20a-d, 23a-d, 24a-c — total 42 sub-items). The schema "
            "accepts any list of at least 27 entries; the report renders "
            "missing items as 'no' / unreported."
        ),
    )
    notes: str = ""


# ── STEP 5 — assembled document ─────────────────────────────────────────


class GradeDocument(BaseModel):
    """The assembled GRADE + PRISMA package."""

    kind: Literal["grade_document"] = "grade_document"
    intake: GradeIntake
    assessments: list[OutcomeAssessment] = Field(min_length=1)
    sof_table: SofTable
    prisma_checklist: PrismaChecklist | None = Field(
        default=None,
        description="PRISMA optional this slice — emit it when the user asks.",
    )
    is_final: bool = False


# ── Discriminated union for the agent's output ──────────────────────────


GradeTurn = Annotated[
    ClarificationRequest
    | GradeIntake
    | OutcomeAssessment
    | SofTable
    | PrismaChecklist
    | GradeDocument,
    Field(discriminator="kind"),
]


__all__ = [
    "CertaintyLevel",
    "ClarificationRequest",
    "DowngradeLevel",
    "DowngradeReason",
    "GradeDocument",
    "GradeIntake",
    "GradeTurn",
    "ImportanceRating",
    "OutcomeAssessment",
    "OutcomeSpec",
    "PRISMA_2020_ITEMS",
    "PrismaChecklist",
    "PrismaItem",
    "SofRow",
    "SofTable",
    "StudyDesign",
    "UpgradeLevel",
    "UpgradeReason",
    "YesNoNA",
]
