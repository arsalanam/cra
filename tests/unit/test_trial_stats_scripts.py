"""Sandbox-script loader smoke tests.

The actual K-M / MMRM / binary / subgroup analyses run inside the
Docker sandbox (statsmodels + matplotlib live there, not the host
venv). These tests only verify:

  1. Every canonical analysis kind is bundled as package data.
  2. Each script parses as valid Python (compile() doesn't raise).
  3. The script bodies declare the `_INPUT` / `_OUTPUT_DIR` contract
     the trial_analysis wrapper tool depends on.
"""

from __future__ import annotations

import pytest

from research_assistant.trial_stats import AVAILABLE_ANALYSES, load_script


def test_available_analyses_match_the_specialist_menu() -> None:
    assert set(AVAILABLE_ANALYSES) == {
        "kaplan_meier",
        "mmrm",
        "binary",
        "subgroup_forest",
    }


@pytest.mark.parametrize("kind", list(AVAILABLE_ANALYSES))
def test_load_script_returns_non_empty_python_source(kind: str) -> None:
    src = load_script(kind)  # type: ignore[arg-type]
    assert src.strip(), f"{kind} script is empty"
    # Compiles cleanly — no syntax errors.
    compile(src, f"<{kind}>", "exec")


@pytest.mark.parametrize("kind", list(AVAILABLE_ANALYSES))
def test_each_script_declares_sandbox_input_and_output_paths(kind: str) -> None:
    src = load_script(kind)  # type: ignore[arg-type]
    assert "/home/sandbox/input/data.json" in src, (
        f"{kind} must read from the canonical sandbox input path"
    )
    assert "/home/sandbox/output" in src, (
        f"{kind} must write to the canonical sandbox output dir"
    )


def test_load_unknown_kind_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_script("does_not_exist")  # type: ignore[arg-type]


def test_kaplan_meier_script_imports_statsmodels_phreg() -> None:
    """The K-M script reuses statsmodels.duration for Cox PH — same
    backend as the CDISC survival_analysis.py. Verifying the import
    statement is present catches accidental drift to lifelines or other
    backends not pinned in the sandbox image."""
    src = load_script("kaplan_meier")
    assert "statsmodels.duration.hazard_regression" in src
    assert "PHReg" in src


def test_mmrm_script_imports_mixed_linear_model() -> None:
    src = load_script("mmrm")
    assert "MixedLM" in src
    assert "statsmodels.regression.mixed_linear_model" in src


def test_binary_script_uses_fisher_and_falls_back_when_log_binomial_fails() -> None:
    src = load_script("binary")
    assert "fisher_exact" in src
    assert "log_binomial" in src.lower() or "Log()" in src


def test_subgroup_script_runs_cox_per_subgroup_and_an_interaction_test() -> None:
    src = load_script("subgroup_forest")
    assert "PHReg" in src
    assert "SGxTRT_" in src  # interaction term marker
