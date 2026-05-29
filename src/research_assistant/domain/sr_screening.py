"""Domain models for the SR-screening AI-assist.

`SrScreeningPrediction` is the structured output of the
`sr_screening_assist` agent: a per-abstract classification + reason +
confidence + short rationale that the screening UI surfaces next to the
paper for the reviewer to accept or override.

Persisted into `AiSuggestion` rows so prediction accuracy vs. human
judgement can be measured later, and so a stale prediction never
accidentally gets counted as a real decision.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReasonCode = Literal[
    "population_mismatch",
    "intervention_mismatch",
    "comparator_mismatch",
    "outcome_mismatch",
    "wrong_design",
    "preclinical",
    "duplicate_report",
    "wrong_setting",
    "language",
    "other_excluded",
    # The `include` side has no reason code; the inclusion criteria themselves
    # are the rationale. We keep this enum closed so the UI's reason filter
    # is finite.
]


class SrScreeningPrediction(BaseModel):
    """The AI-assist's prediction for one abstract at one phase.

    Used both as the specialist's structured output and as a request
    schema when reviewers submit a batch of decisions accepted-from-AI.
    """

    model_config = ConfigDict(extra="forbid")

    predicted_decision: Literal["include", "exclude", "maybe"] = Field(
        description=(
            "include → matches all inclusion criteria and no exclusion criteria; "
            "exclude → fails at least one criterion; "
            "maybe → ambiguous, needs human review."
        )
    )
    predicted_reason_code: ReasonCode | None = Field(
        default=None,
        description=(
            "When predicted_decision is 'exclude', the primary failing "
            "criterion. None for include/maybe."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "0.0 = uncertain, 1.0 = certain. Anchor at 0.5 by default and "
            "only push above 0.75 when the abstract gives explicit evidence."
        ),
    )
    rationale: str = Field(
        description=(
            "One-paragraph justification citing specific phrases from the "
            "abstract. Used by the reviewer to decide whether to accept or "
            "override."
        ),
        min_length=10,
    )
