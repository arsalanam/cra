"""SDTM LB unit standardisation (US-conventional → SI)."""

from __future__ import annotations

import pytest

from research_assistant.cdisc.lab_units import standardize_lab


def test_glucose_mgdl_to_mmol() -> None:
    stresn, stresu, _, _ = standardize_lab(
        testcd="GLUC", value=90.0, unit="mg/dL", nrlo=None, nrhi=None
    )
    assert stresu == "mmol/L"
    assert stresn == pytest.approx(90.0 * 0.0555, abs=1e-4)


def test_creatinine_mgdl_to_umol() -> None:
    stresn, stresu, _, _ = standardize_lab(
        testcd="CREAT", value=1.0, unit="mg/dL", nrlo=None, nrhi=None
    )
    assert stresu == "umol/L"
    assert stresn == pytest.approx(88.42, abs=1e-2)


def test_hemoglobin_gdl_to_gl() -> None:
    stresn, stresu, _, _ = standardize_lab(
        testcd="HGB", value=14.0, unit="g/dL", nrlo=None, nrhi=None
    )
    assert stresu == "g/L"
    assert stresn == pytest.approx(140.0)


def test_range_limits_convert_with_the_value() -> None:
    """LBSTNRLO/HI must use the same conversion so LBNRIND stays consistent."""
    _, _, stnrlo, stnrhi = standardize_lab(
        testcd="GLUC", value=100.0, unit="mg/dL", nrlo=70.0, nrhi=110.0
    )
    factor = 0.0555
    assert stnrlo == pytest.approx(70.0 * factor, abs=1e-4)
    assert stnrhi == pytest.approx(110.0 * factor, abs=1e-4)


def test_case_and_micro_sign_insensitive_source_unit() -> None:
    a = standardize_lab(testcd="GLUC", value=90.0, unit="MG/DL", nrlo=None, nrhi=None)
    b = standardize_lab(testcd="GLUC", value=90.0, unit="mg/dl", nrlo=None, nrhi=None)
    assert a[0] == b[0]
    assert a[1] == "mmol/L"


def test_already_standard_unit_passes_through() -> None:
    stresn, stresu, _, _ = standardize_lab(
        testcd="GLUC", value=5.0, unit="mmol/L", nrlo=None, nrhi=None
    )
    assert stresu == "mmol/L"
    assert stresn == 5.0  # unchanged — already SI


def test_electrolyte_has_no_conversion() -> None:
    stresn, stresu, _, _ = standardize_lab(
        testcd="K", value=4.2, unit="mmol/L", nrlo=None, nrhi=None
    )
    assert stresn == 4.2
    assert stresu == "mmol/L"


def test_unknown_analyte_passes_through() -> None:
    stresn, stresu, _, _ = standardize_lab(
        testcd="XYZ", value=1.0, unit="mg/dL", nrlo=None, nrhi=None
    )
    assert stresn == 1.0
    assert stresu == "mg/dL"


def test_none_value_is_safe() -> None:
    stresn, stresu, _, _ = standardize_lab(
        testcd="GLUC", value=None, unit="mg/dL", nrlo=None, nrhi=None
    )
    assert stresn is None
    assert stresu == "mg/dL"
