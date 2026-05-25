"""Pydantic schemas for the risk_of_bias specialist's turn-based workflow.

Each agent run returns one `RiskOfBiasTurn` — a discriminated union over:

  clarification     — needs more info (shared shape from common)
  rob_assessments   — per-study × per-domain judgments for review/edit
  rob_summary       — stacked-bar plot + narrative + sensitivity list

Anti-hallucination posture mirrors the other specialists:
  - Every PMID in `assessments` must come from a real `search_papers`
    call this turn (or be in the handoff payload from data_extraction).
  - Every `quote` must be verbatim from the abstract / full text — no
    paraphrasing dressed up as quotes.
  - When the abstract doesn't address a domain, judgment MUST be
    `no_information` — never inferred from training data.

The canonical domain names per tool live in `ROB_DOMAINS_BY_TOOL`. The
prompt references this so the agent can't drift into invented domain
names. Schemas leave `domain` as a free string so the user can tweak
phrasings during review.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest

RobTool = Literal["RoB 2.0", "ROBINS-I", "Newcastle-Ottawa", "QUADAS-2"]
RobJudgment = Literal["low", "some_concerns", "high", "no_information"]


# Canonical RoB domains per instrument. The agent receives these in its
# prompt and must use the canonical names when constructing per-study
# assessments. Short labels keep the assessments table compact in the UI.
ROB_DOMAINS_BY_TOOL: dict[RobTool, list[str]] = {
    "RoB 2.0": [
        "Randomization process",
        "Deviations from intended interventions",
        "Missing outcome data",
        "Measurement of the outcome",
        "Selection of the reported result",
    ],
    "ROBINS-I": [
        "Confounding",
        "Selection of participants",
        "Classification of interventions",
        "Deviations from intended interventions",
        "Missing data",
        "Measurement of outcomes",
        "Selection of the reported result",
    ],
    "Newcastle-Ottawa": [
        "Selection",
        "Comparability",
        "Outcome / Exposure",
    ],
    "QUADAS-2": [
        "Patient selection",
        "Index test",
        "Reference standard",
        "Flow and timing",
    ],
}


class RobDomain(BaseModel):
    """One domain × one study."""

    domain: str = Field(
        description=(
            "Canonical domain name from ROB_DOMAINS_BY_TOOL for the tool in use. "
            "Free-string in the schema so the user can tweak in review, but the "
            "prompt requires the agent to start with canonical names."
        ),
    )
    judgment: RobJudgment = Field(
        description=(
            "low / some_concerns / high / no_information. "
            "no_information is the correct answer when the abstract is silent — "
            "common for randomization-method, allocation-concealment, blinding details. "
            "NEVER infer from training data."
        ),
    )
    justification: str = Field(
        description=(
            "1–2 sentences grounded in the fetched abstract / full text. "
            "When judgment='no_information', state explicitly that the source "
            "doesn't describe this domain."
        ),
    )
    quote: str | None = Field(
        default=None,
        description="Optional verbatim snippet from the source. No paraphrasing.",
    )


class StudyRobAssessment(BaseModel):
    """All domain judgments for one study."""

    pmid: str
    title: str
    study_design: str = Field(
        description=(
            "e.g. 'Randomized Controlled Trial', 'Cohort study'. Drives RoB tool selection."
        ),
    )
    domains: list[RobDomain] = Field(
        description="One entry per domain in the chosen tool's rubric.",
    )
    overall_judgment: RobJudgment = Field(
        description=(
            "Synthesized per the tool's rules. RoB 2.0: any domain 'high' → "
            "overall 'high'; any 'some_concerns' (and no 'high') → 'some_concerns'; "
            "all 'low' → 'low'. Treat 'no_information' for a critical domain "
            "as 'some_concerns' at minimum."
        ),
    )
    overall_rationale: str = Field(
        description="One-sentence summary of the worst-case domain and why.",
    )


# kind="rob_assessments" — per-study × per-domain table, user reviews/edits
class RobAssessments(BaseModel):
    kind: Literal["rob_assessments"] = "rob_assessments"
    tool: RobTool
    assessments: list[StudyRobAssessment]
    summary: str = Field(
        description=(
            "One paragraph: distribution of overall judgments (e.g. '2 low, 5 some_concerns, "
            "1 high'); the most common 'no_information' domains across the set; whether the "
            "tool choice fits the included designs."
        ),
    )


class DomainDistribution(BaseModel):
    """Stacked-bar input for one domain."""

    domain: str
    low: int = 0
    some_concerns: int = 0
    high: int = 0
    no_information: int = 0


# kind="rob_summary" — final summary with plot + narrative
class RobSummary(BaseModel):
    kind: Literal["rob_summary"] = "rob_summary"
    tool: RobTool
    assessments: list[StudyRobAssessment] = Field(
        description="Carried through from the confirmed RobAssessments turn.",
    )
    domain_distribution: list[DomainDistribution] = Field(
        description="One entry per domain, counts across all studies. Drives the stacked bar.",
    )
    summary_plot_image: str | None = Field(
        default=None,
        description=(
            "Filename produced by sandbox_exec (e.g. 'rob_summary_rob2.png'). "
            "The dispatcher post-processor swaps it for a data URI before the "
            "frontend renders it."
        ),
    )
    narrative: str = Field(
        description=(
            "1–2 paragraphs interpreting the bar chart: which domains are the "
            "weak spots across the set, what the user should be cautious about "
            "when reading the pooled estimate."
        ),
    )
    sensitivity_recommendations: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete next moves, e.g. 'Re-pool excluding PMID 12345678 (overall high)' "
            "or 'Subgroup analysis by allocation-concealment status'. Names specific PMIDs."
        ),
    )
    high_rob_pmids: list[str] = Field(
        default_factory=list,
        description=(
            "PMIDs whose overall_judgment is 'high'. Used by the meta-analysis "
            "handoff to seed a sensitivity-analysis re-pool."
        ),
    )
    is_final: bool = Field(
        default=False,
        description="True after the user clicks Finalize. Enables the handoff CTA.",
    )


# Discriminated union of every shape the risk_of_bias specialist may emit.
RiskOfBiasTurn = Annotated[
    ClarificationRequest | RobAssessments | RobSummary,
    Field(discriminator="kind"),
]
