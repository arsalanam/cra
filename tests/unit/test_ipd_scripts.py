"""IPD sandbox-script loader smoke tests."""

from __future__ import annotations

import pytest

from research_assistant.ipd import AVAILABLE_IPD_SCRIPTS, load_script


def test_three_canonical_scripts_available() -> None:
    assert set(AVAILABLE_IPD_SCRIPTS) == {"one_stage", "two_stage", "subgroup"}


@pytest.mark.parametrize("kind", list(AVAILABLE_IPD_SCRIPTS))
def test_each_script_loads_and_compiles(kind: str) -> None:
    src = load_script(kind)  # type: ignore[arg-type]
    assert src.strip(), f"{kind} script is empty"
    compile(src, f"<{kind}>", "exec")


@pytest.mark.parametrize("kind", list(AVAILABLE_IPD_SCRIPTS))
def test_each_script_uses_canonical_sandbox_paths(kind: str) -> None:
    src = load_script(kind)  # type: ignore[arg-type]
    assert "/home/sandbox/input/data.json" in src
    assert "/home/sandbox/output" in src


def test_one_stage_uses_mixedlm_for_continuous_and_phreg_for_tte() -> None:
    """The one-stage script is the load-bearing IPD primitive — pin the
    statsmodels backends so future refactors flag drift."""
    src = load_script("one_stage")
    assert "MixedLM" in src
    assert "PHReg" in src
    assert "strata=" in src or "strata =" in src


def test_two_stage_implements_dersimonian_laird() -> None:
    src = load_script("two_stage")
    assert "DerSimonian" in src
    assert "tau" in src.lower()
    assert "i_squared" in src.lower() or "i²" in src.lower()


def test_subgroup_uses_interaction_term() -> None:
    """Subgroup script adds treatment × subgroup interaction — pin the
    SGxTRT_ naming convention."""
    src = load_script("subgroup")
    assert "SGxTRT_" in src or "interaction_p_value" in src.lower()


def test_unknown_kind_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_script("ghost")  # type: ignore[arg-type]
