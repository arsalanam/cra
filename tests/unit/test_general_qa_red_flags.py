"""Regression tests for general_qa's clinical-synthesis red-flag patterns.

The guardrail must catch CONCRETE numeric claims from training data while
allowing definitional prose. Found live 2026-07-08: `[\\s:]*` in the CI
patterns matched newlines, so "…the 95% CI\\n\\n5. The diamond…" (a
paragraph break into a numbered list) tripped the validator and errored
the canonical "what does a forest plot show?" turn after retry exhaustion.
"""

from __future__ import annotations

import pytest

from research_assistant.agent.specialists.general_qa import _looks_like_clinical_synthesis

# ── Definitional prose that MUST pass ────────────────────────────────────

_ALLOWED = [
    # The live failure: CI phrase at end of a paragraph, numbered list next.
    "Each horizontal line represents the study's 95% CI\n\n5. The diamond "
    "shows the pooled estimate",
    "The width reflects the confidence interval\n\n5. Heterogeneity is "
    "shown at the bottom",
    # Bare concepts with no adjacent number.
    "A forest plot displays each study's effect estimate and its 95% CI.",
    "A confidence interval that crosses the line of no effect suggests the "
    "result is not statistically significant.",
    "The diamond at the bottom represents the pooled effect and its "
    "confidence interval.",
    # Percentages that are not CI claims.
    "Weights are often shown as percentages next to each study.",
]


@pytest.mark.parametrize("text", _ALLOWED)
def test_definitional_prose_is_allowed(text: str) -> None:
    assert _looks_like_clinical_synthesis(text) is None


# ── Concrete numeric claims that MUST still be blocked ───────────────────

_BLOCKED = [
    "The pooled estimate was OR = 0.75 favouring treatment.",
    "The trial reported a 95% CI 0.34-0.58.",
    "The effect was significant (95% CI: 0.4 to 0.9).",
    "with a confidence interval of 0.2 to 0.8",
    "a confidence interval [0.31, 0.78] around the estimate",
    "heterogeneity was low (I² = 20%)",
    "the difference was significant, p < 0.05",
    "as shown in PMID 12345678",
    "guidelines recommend statins for primary prevention",
]


@pytest.mark.parametrize("text", _BLOCKED)
def test_concrete_claims_are_still_blocked(text: str) -> None:
    assert _looks_like_clinical_synthesis(text) is not None
