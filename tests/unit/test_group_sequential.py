"""group_sequential — Lan-DeMets boundaries vs published tables.

Reference boundary values are the standard one-sided alpha=0.025
(two-sided 0.05) Lan-DeMets *spending-function* z-boundaries (as produced
by gsDesign / East). NOTE these differ from the classic constant-form
O'Brien-Fleming boundaries — the OBF spending function is slightly more
conservative early (e.g. K=3 look-1 is 3.710, not the classic 3.471).
"""

from __future__ import annotations

import pytest

from research_assistant.tools.data_science.group_sequential import (
    group_sequential_boundaries,
)


def _z(result: dict[str, object], look: int) -> float:
    looks = result["looks"]  # type: ignore[index]
    return float(looks[look - 1]["z_boundary"])  # type: ignore[index]


def test_single_look_reduces_to_fixed_design() -> None:
    """One look = a fixed design: the boundary is z_{1-alpha} and there's no
    inflation."""
    r = group_sequential_boundaries(n_looks=1, alpha=0.025)
    assert _z(r, 1) == pytest.approx(1.9600, abs=0.01)
    assert r["max_sample_size_inflation"] == pytest.approx(1.0, abs=0.01)


def test_obrien_fleming_two_looks_matches_table() -> None:
    """OBF spending, K=2, one-sided 0.025: boundaries ~2.963 then ~1.969."""
    r = group_sequential_boundaries(n_looks=2, alpha=0.025, spending="obrien_fleming")
    assert _z(r, 1) == pytest.approx(2.963, abs=0.02)
    assert _z(r, 2) == pytest.approx(1.969, abs=0.02)
    # Final cumulative spend equals alpha.
    assert r["looks"][-1]["cumulative_alpha_spent"] == pytest.approx(0.025, abs=1e-4)  # type: ignore[index]


def test_obrien_fleming_three_looks_matches_table() -> None:
    """OBF spending, K=3: ~3.710, 2.511, 1.993."""
    r = group_sequential_boundaries(n_looks=3, alpha=0.025, spending="obrien_fleming")
    assert _z(r, 1) == pytest.approx(3.710, abs=0.02)
    assert _z(r, 2) == pytest.approx(2.511, abs=0.02)
    assert _z(r, 3) == pytest.approx(1.993, abs=0.02)


def test_pocock_two_looks_matches_table() -> None:
    """Pocock, K=2, one-sided 0.025: a near-constant boundary ~2.178."""
    r = group_sequential_boundaries(n_looks=2, alpha=0.025, spending="pocock")
    assert _z(r, 1) == pytest.approx(2.178, abs=0.03)
    assert _z(r, 2) == pytest.approx(2.178, abs=0.05)


def test_pocock_spends_earlier_than_obf() -> None:
    """Pocock's first-look boundary is much less stringent than OBF's — it
    spends more alpha early."""
    obf = group_sequential_boundaries(n_looks=3, spending="obrien_fleming")
    poc = group_sequential_boundaries(n_looks=3, spending="pocock")
    assert _z(poc, 1) < _z(obf, 1)
    assert poc["looks"][0]["cumulative_alpha_spent"] > obf["looks"][0]["cumulative_alpha_spent"]  # type: ignore[index]


def test_final_cumulative_alpha_equals_alpha() -> None:
    for spending in ("obrien_fleming", "pocock"):
        r = group_sequential_boundaries(n_looks=4, alpha=0.025, spending=spending)
        assert r["looks"][-1]["cumulative_alpha_spent"] == pytest.approx(0.025, abs=1e-4)  # type: ignore[index]


def test_inflation_factor_is_small_for_obf() -> None:
    """OBF's inflation over a fixed design is modest (a few percent); Pocock's
    is larger because it spends alpha earlier."""
    obf = group_sequential_boundaries(n_looks=3, spending="obrien_fleming", power=0.90)
    poc = group_sequential_boundaries(n_looks=3, spending="pocock", power=0.90)
    assert 1.0 <= obf["max_sample_size_inflation"] < 1.10
    assert obf["max_sample_size_inflation"] < poc["max_sample_size_inflation"]


def test_custom_information_fractions() -> None:
    r = group_sequential_boundaries(
        n_looks=3, information_fractions=[0.4, 0.7, 1.0], spending="obrien_fleming"
    )
    fracs = [look["information_fraction"] for look in r["looks"]]  # type: ignore[index]
    assert fracs == [0.4, 0.7, 1.0]


def test_rejects_bad_information_fractions() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        group_sequential_boundaries(n_looks=3, information_fractions=[0.5, 0.5, 1.0])
    with pytest.raises(ValueError, match=r"end at 1\.0"):
        group_sequential_boundaries(n_looks=2, information_fractions=[0.5, 0.9])


def test_rejects_bad_spending_function() -> None:
    with pytest.raises(ValueError, match="Unknown spending function"):
        group_sequential_boundaries(n_looks=2, spending="haybittle_peto")
