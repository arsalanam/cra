"""Sandbox-script loader smoke tests for the visualisations package.

Actual funnel / waterfall / swimmer runs happen inside the Docker
sandbox (matplotlib + numpy + statsmodels live there). These tests
verify the loader contract — every kind is bundled, each script
compiles, and each declares the sandbox I/O paths.
"""

from __future__ import annotations

import pytest

from research_assistant.visualizations import AVAILABLE_VISUALIZATIONS, load_script


def test_available_visualizations_matches_expected_set() -> None:
    assert set(AVAILABLE_VISUALIZATIONS) == {"funnel", "waterfall", "swimmer"}


@pytest.mark.parametrize("kind", list(AVAILABLE_VISUALIZATIONS))
def test_load_script_returns_non_empty_python_source(kind: str) -> None:
    src = load_script(kind)  # type: ignore[arg-type]
    assert src.strip(), f"{kind} script is empty"
    compile(src, f"<{kind}>", "exec")


@pytest.mark.parametrize("kind", list(AVAILABLE_VISUALIZATIONS))
def test_each_script_declares_sandbox_input_and_output_paths(kind: str) -> None:
    src = load_script(kind)  # type: ignore[arg-type]
    assert "/home/sandbox/input/data.json" in src
    assert "/home/sandbox/output" in src


def test_load_unknown_kind_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_script("nonexistent")  # type: ignore[arg-type]


def test_funnel_script_uses_statsmodels_ols_for_eggers() -> None:
    src = load_script("funnel")
    assert "statsmodels.api" in src
    assert "OLS" in src
    assert "Egger" in src or "egger" in src.lower()


def test_waterfall_script_uses_recist_thresholds() -> None:
    src = load_script("waterfall")
    assert "-30" in src or "_RECIST_PR" in src
    assert "20" in src or "_RECIST_PD" in src
    assert "RECIST" in src or "recist" in src.lower()


def test_swimmer_script_renders_event_markers() -> None:
    src = load_script("swimmer")
    assert "response_onset" in src
    assert "progression" in src
    assert "off_treatment" in src
