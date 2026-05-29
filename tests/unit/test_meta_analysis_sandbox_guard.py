"""Unit tests for the meta-analysis specialist's `_require_sandbox_for_results`
output validator.

The validator exists because the meta-analysis specialist's STEP 5 system-prompt
trigger requires the user message to start with "Confirmed extracted data" and
contain a JSON payload — and a user typing a free-form confirmation like
"approved" can sneak past that, letting the model emit a MetaAnalysisResults
with `forest_plot_image=None` on every outcome (no sandbox_exec was called).

These tests pin the validator's contract:
1. MetaAnalysisResults + empty artifacts -> ModelRetry raised.
2. MetaAnalysisResults + non-empty artifacts -> output passes through unchanged.
3. Any other output type -> passes through unchanged regardless of artifacts.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import ModelRetry

from research_assistant.agent.deps import AgentDeps
from research_assistant.agent.specialists.meta_analysis import (
    _require_sandbox_for_results,
)
from research_assistant.domain.meta_analysis import (
    MetaAnalysisOutcomeResult,
    MetaAnalysisResults,
    PicoDraft,
    PicoTable,
)


def _fake_ctx(artifacts: dict[str, str]) -> Any:
    """A duck-typed stand-in for RunContext — the validator only reads ctx.deps."""
    return SimpleNamespace(deps=AgentDeps(artifacts=dict(artifacts)))


def _results_with_outcomes(n: int = 1, forest_plot_image: str | None = None) -> MetaAnalysisResults:
    return MetaAnalysisResults(
        outcome_results=[
            MetaAnalysisOutcomeResult(
                outcome=f"outcome_{i}",
                effect_measure="RR",
                pooled_effect=0.44,
                ci_lower=0.33,
                ci_upper=0.58,
                i_squared=0.0,
                heterogeneity_p=0.99,
                n_studies=8,
                n_participants=17_596,
                forest_plot_image=forest_plot_image,
                interpretation="favours intervention",
            )
            for i in range(n)
        ],
        studies_included=[f"PMID_{i}" for i in range(8)],
        summary="Pooled RR=0.44 across 8 RCTs.",
        caveats=[],
    )


def test_meta_analysis_results_without_artifacts_raises_model_retry() -> None:
    """The headline failure mode: model computed numbers inline, no sandbox_exec."""
    ctx = _fake_ctx(artifacts={})
    output = _results_with_outcomes()

    with pytest.raises(ModelRetry) as excinfo:
        _require_sandbox_for_results(ctx, output)

    msg = str(excinfo.value)
    # The retry message must steer the model toward the right corrective action
    # — synthesise the JSON from history and call sandbox_exec.
    assert "sandbox_exec" in msg
    assert "input_data" in msg
    assert "STEP 5" in msg


def test_meta_analysis_results_with_artifacts_passes_through() -> None:
    """sandbox_exec did run and produced files -> validator must not retry."""
    ctx = _fake_ctx(artifacts={"forest_plot_outcome_0.png": "/images/abc_forest.png"})
    output = _results_with_outcomes(forest_plot_image="forest_plot_outcome_0.png")

    returned = _require_sandbox_for_results(ctx, output)

    assert returned is output


def test_non_meta_analysis_output_passes_through_regardless_of_artifacts() -> None:
    """Earlier-stage outputs (PicoDraft, SearchResults, DataExtraction) bypass the check."""
    pico = PicoDraft(
        pico=PicoTable(
            population="Adults post-PCI for ACS on DAPT",
            intervention="PPI + DAPT",
            comparison="DAPT alone",
            outcomes=["Upper GI bleeding"],
        ),
        rationale="Standard PICO for the canonical demo question.",
    )
    ctx_empty = _fake_ctx(artifacts={})
    ctx_full = _fake_ctx(artifacts={"x.png": "/images/x.png"})

    # Both should pass through with no retry. sandbox_exec is irrelevant for
    # non-STEP-5 outputs.
    assert _require_sandbox_for_results(ctx_empty, pico) is pico
    assert _require_sandbox_for_results(ctx_full, pico) is pico


def test_meta_analysis_results_with_zero_outcomes_still_requires_sandbox() -> None:
    """Edge case: empty outcome list should also fail — a real STEP 5 always
    produces at least one outcome via sandbox_exec, so an empty list with no
    artifacts is suspicious."""
    ctx = _fake_ctx(artifacts={})
    output = MetaAnalysisResults(
        outcome_results=[],
        studies_included=[],
        summary="No analysable outcomes.",
    )

    with pytest.raises(ModelRetry):
        _require_sandbox_for_results(ctx, output)
