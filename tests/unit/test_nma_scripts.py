"""NMA sandbox-script loader smoke tests."""

from __future__ import annotations

import pytest

from research_assistant.nma import AVAILABLE_NMA_SCRIPTS, load_script


def test_three_canonical_scripts_available() -> None:
    assert set(AVAILABLE_NMA_SCRIPTS) == {"frequentist", "bayesian", "geometry"}


@pytest.mark.parametrize("kind", list(AVAILABLE_NMA_SCRIPTS))
def test_each_script_loads_and_compiles(kind: str) -> None:
    src = load_script(kind)  # type: ignore[arg-type]
    assert src.strip(), f"{kind} script is empty"
    compile(src, f"<{kind}>", "exec")


@pytest.mark.parametrize("kind", list(AVAILABLE_NMA_SCRIPTS))
def test_each_script_uses_canonical_sandbox_paths(kind: str) -> None:
    src = load_script(kind)  # type: ignore[arg-type]
    assert "/home/sandbox/input/data.json" in src
    assert "/home/sandbox/output" in src


def test_frequentist_uses_scipy_multivariate_normal_for_sucra() -> None:
    """SUCRA via MVN posterior draws is the load-bearing decision —
    test pins the import so a future refactor flags it."""
    src = load_script("frequentist")
    assert "multivariate_normal" in src
    assert "sucra" in src.lower()


def test_bayesian_gracefully_skips_when_pymc_missing() -> None:
    """The Bayesian script MUST write a structured skip JSON when PyMC
    is missing — otherwise the host loses the fallback signal."""
    src = load_script("bayesian")
    assert "import pymc" in src
    assert "skip_reason" in src
    assert "rebuild" in src.lower()


def test_geometry_uses_matplotlib() -> None:
    src = load_script("geometry")
    assert "matplotlib" in src
    assert "nma-geometry.png" in src


def test_unknown_kind_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_script("ghost")  # type: ignore[arg-type]
