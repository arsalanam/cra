"""ADaM ADAE / ADCM / ADLB / ADVS derivers (the FDA-recommended set)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from research_assistant.cdisc.adam_extra import (
    derive_adae,
    derive_adcm,
    derive_adlb,
    derive_advs,
)


def _adsl(**overrides: object) -> Any:
    base = dict(
        USUBJID="RS-1-S-001",
        TRT01A="Drug X",
        TRT01P="Drug X",
        AGE=42,
        SEX="M",
        SAFFL="Y",
        RFSTDTC="2026-01-10T00:00:00",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _ae(**overrides: object) -> Any:
    base = dict(
        USUBJID="RS-1-S-001",
        AESEQ=1,
        AETERM="headache",
        AEDECOD="Headache",
        AEBODSYS=None,
        AESEV="MODERATE",
        AESER="N",
        AEREL="POSSIBLE",
        AEOUT="RECOVERED",
        AESTDTC="2026-01-15T00:00:00",
        AEENDTC="2026-01-16T00:00:00",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── ADAE ─────────────────────────────────────────────────────────────────


def test_adae_merges_adsl_and_flags_treatment_emergent() -> None:
    [row] = derive_adae(deployment_id="d", study_id="RS-1", adsl=[_adsl()], ae_records=[_ae()])
    assert row.TRTA == "Drug X"
    assert row.SAFFL == "Y"
    assert row.AEDECOD == "Headache"
    # AE started 2026-01-15, after RFSTDTC 2026-01-10 → treatment-emergent.
    assert row.TRTEMFL == "Y"
    assert row.AOCCFL == "Y"  # first occurrence of the term


def test_adae_pre_treatment_ae_is_not_emergent() -> None:
    ae = _ae(AESTDTC="2026-01-05T00:00:00")  # before RFSTDTC 2026-01-10
    [row] = derive_adae(deployment_id="d", study_id="RS-1", adsl=[_adsl()], ae_records=[ae])
    assert row.TRTEMFL == ""


def test_adae_aoccfl_only_on_first_occurrence_of_term() -> None:
    rows = derive_adae(
        deployment_id="d",
        study_id="RS-1",
        adsl=[_adsl()],
        ae_records=[
            _ae(AESEQ=1, AEDECOD="Headache"),
            _ae(AESEQ=2, AEDECOD="Headache"),
            _ae(AESEQ=3, AEDECOD="Nausea"),
        ],
    )
    flags = {r.ASEQ: r.AOCCFL for r in rows}
    assert flags == {1: "Y", 2: "", 3: "Y"}


def test_adae_without_matching_adsl_degrades_gracefully() -> None:
    [row] = derive_adae(deployment_id="d", study_id="RS-1", adsl=[], ae_records=[_ae()])
    assert row.TRTA is None
    assert row.SAFFL == "N"
    assert row.TRTEMFL == ""  # no RFSTDTC to compare against


# ── ADCM ─────────────────────────────────────────────────────────────────


def test_adcm_merges_treatment_and_copies_fields() -> None:
    cm = SimpleNamespace(
        USUBJID="RS-1-S-001",
        CMSEQ=1,
        CMTRT="Aspirin",
        CMDECOD="ASPIRIN",
        CMINDC="headache",
        CMDOSE="100",
        CMDOSU="mg",
        CMSTDTC="2026-01-12",
        CMENDTC="2026-01-14",
    )
    [row] = derive_adcm(deployment_id="d", study_id="RS-1", adsl=[_adsl()], cm_records=[cm])
    assert row.TRTA == "Drug X"
    assert row.CMDECOD == "ASPIRIN"
    assert row.CMDOSE == 100.0
    assert row.ASTDT == "2026-01-12"


# ── ADLB (BDS baseline / change) ─────────────────────────────────────────


def _lb(**overrides: object) -> Any:
    base = dict(
        USUBJID="RS-1-S-001",
        LBSEQ=1,
        LBTESTCD="GLUC",
        LBTEST="Glucose",
        LBSTRESN=5.0,
        LBSTRESU="mmol/L",
        LBDTC="2026-01-10",
        LBNRIND="NORMAL",
        LBSTNRLO=3.9,
        LBSTNRHI=5.5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_adlb_baseline_and_change() -> None:
    rows = derive_adlb(
        deployment_id="d",
        study_id="RS-1",
        adsl=[_adsl()],
        lb_records=[
            _lb(LBSEQ=1, LBSTRESN=5.0, LBDTC="2026-01-10"),  # baseline (earliest)
            _lb(LBSEQ=2, LBSTRESN=6.5, LBDTC="2026-02-10"),  # post-baseline
        ],
    )
    by_dt = {r.ADT: r for r in rows}
    base = by_dt["2026-01-10"]
    post = by_dt["2026-02-10"]
    assert base.ABLFL == "Y"
    assert base.BASE == 5.0
    assert base.CHG == 0.0
    assert post.ABLFL == ""
    assert post.BASE == 5.0
    assert post.CHG == 1.5
    assert post.PARAMCD == "GLUC"
    assert post.TRTA == "Drug X"


def test_adlb_baseline_is_per_parameter() -> None:
    rows = derive_adlb(
        deployment_id="d",
        study_id="RS-1",
        adsl=[_adsl()],
        lb_records=[
            _lb(LBSEQ=1, LBTESTCD="GLUC", LBSTRESN=5.0, LBDTC="2026-01-10"),
            _lb(LBSEQ=2, LBTESTCD="ALT", LBSTRESN=30.0, LBDTC="2026-01-10"),
        ],
    )
    # Each PARAMCD gets its own baseline row.
    assert sum(1 for r in rows if r.ABLFL == "Y") == 2


# ── ADVS ─────────────────────────────────────────────────────────────────


def test_advs_parses_character_vsorres_to_numeric_aval() -> None:
    vs = [
        SimpleNamespace(
            USUBJID="RS-1-S-001",
            VSSEQ=1,
            VSTESTCD="SYSBP",
            VSTEST="Systolic BP",
            VSORRES="120",
            VSORRESU="mmHg",
            VSDTC="2026-01-10",
        ),
        SimpleNamespace(
            USUBJID="RS-1-S-001",
            VSSEQ=2,
            VSTESTCD="SYSBP",
            VSTEST="Systolic BP",
            VSORRES="130",
            VSORRESU="mmHg",
            VSDTC="2026-02-10",
        ),
    ]
    rows = derive_advs(deployment_id="d", study_id="RS-1", adsl=[_adsl()], vs_records=vs)
    post = next(r for r in rows if r.ADT == "2026-02-10")
    assert post.AVAL == 130.0
    assert post.BASE == 120.0
    assert post.CHG == 10.0
