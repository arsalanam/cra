"""CDISC export — ADTTE CSV + bundle (CDISC follow-up)."""

from __future__ import annotations

import csv
import io
import zipfile

from research_assistant.cdisc.exporter import adtte_to_csv, build_submission_bundle
from research_assistant.persistence.clinical.models import AdamAdtte


def _adtte(
    usubjid: str = "RS-1-S-001",
    paramcd: str = "TTAE",
    aval: float = 14.0,
    cnsr: int = 0,
) -> AdamAdtte:
    return AdamAdtte(
        deployment_id="dep-1",
        STUDYID="RS-1",
        USUBJID=usubjid,
        PARAMCD=paramcd,
        PARAM=f"Param {paramcd}",
        AVAL=aval,
        AVALU="DAYS",
        CNSR=cnsr,
        STARTDT="2026-01-01T00:00:00",
        ADT="2026-01-15T00:00:00",
        EVNTDESC="headache",
        SRCDOM="AE",
        SRCVAR="AESTDTC",
        TRT01P="Drug A",
        TRT01A="Drug A",
    )


def test_adtte_csv_header_order_starts_with_studyid_usubjid_paramcd() -> None:
    payload = adtte_to_csv([_adtte()])
    headers = payload.decode("utf-8").splitlines()[0].split(",")
    assert headers[0] == "STUDYID"
    assert headers[1] == "USUBJID"
    assert headers[2] == "PARAMCD"
    assert "CNSR" in headers
    assert "SRCDOM" in headers
    assert "TRT01A" in headers


def test_adtte_csv_carries_aval_cnsr_and_audit_anchor() -> None:
    rows = list(
        csv.DictReader(io.StringIO(adtte_to_csv([_adtte()]).decode("utf-8")))
    )
    assert rows[0]["AVAL"] == "14.0"
    assert rows[0]["CNSR"] == "0"
    assert rows[0]["SRCDOM"] == "AE"
    assert rows[0]["SRCVAR"] == "AESTDTC"


def test_bundle_includes_adam_adtte_csv() -> None:
    payload = build_submission_bundle(
        study_id="RS-1",
        triggered_at=None,
        dm=[],
        ae=[],
        vs=[],
        adsl=[],
        adtte=[_adtte()],
        tlfs=[],
    )
    z = zipfile.ZipFile(io.BytesIO(payload))
    assert "adam/adtte.csv" in z.namelist()
    manifest = z.read("define-overview.txt").decode("utf-8")
    assert "ADaM ADTTE: 1" in manifest


def test_bundle_without_adtte_still_emits_header_only_csv() -> None:
    """Backward-compatible: omitting adtte= still produces a valid
    header-only adam/adtte.csv so empty trials work end-to-end."""
    payload = build_submission_bundle(
        study_id="RS-1",
        triggered_at=None,
        dm=[],
        ae=[],
        vs=[],
        adsl=[],
        tlfs=[],
    )
    z = zipfile.ZipFile(io.BytesIO(payload))
    assert "adam/adtte.csv" in z.namelist()
    adtte_csv = z.read("adam/adtte.csv").decode("utf-8")
    assert adtte_csv.splitlines()[0].startswith("STUDYID,USUBJID,PARAMCD")
    assert len(adtte_csv.splitlines()) == 1
