"""Turn shapes shared across multiple specialists.

`ClarificationRequest` is used by any specialist that needs the user to
disambiguate before proceeding. `Answer` is the primary output of the
general_qa specialist (and may eventually be reused by other specialists
for tool-meta questions, though dispatcher-level routing is the preferred
mechanism).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ClarificationRequest(BaseModel):
    """Specialist needs more information before it can proceed."""

    kind: Literal["clarification"] = "clarification"
    question: str
    options: list[str] | None = Field(
        default=None,
        description=(
            "When the question is multiple-choice, list the options the user "
            "can pick. The frontend renders these as clickable chips."
        ),
    )
    rationale: str | None = Field(
        default=None,
        description="Why this clarification is needed.",
    )


class Answer(BaseModel):
    """Plain-text response. The general_qa specialist's primary output."""

    kind: Literal["answer"] = "answer"
    text: str
    references: list[str] = Field(
        default_factory=list,
        description=(
            "Optional citations / URLs surfaced from web_search or wikipedia "
            "tool calls during this turn. Renders as a footer in the UI."
        ),
    )
