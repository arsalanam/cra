"""CDISC export — LB / EX / CM / MH CSV + bundle (CDISC2)."""

from __future__ import annotations

import csv
import io
import zipfile

from research_assistant.cdisc.exporter import (
    build_submission_bundle,
    cm_to_csv,
    ex_to_csv,
    lb_to_csv,
    mh_to_csv,
)
from research_assistant.persistence.clinical.models import (
    SdtmCm,
    SdtmEx,
    SdtmLb,
    SdtmMh,
)


def _lb() -> SdtmLb:
    return SdtmLb(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="LB",
        USUBJID="RS-1-S-001",
        LBSEQ=1,
        LBTESTCD="HGB",
        LBTEST="Hemoglobin",
        LBORRES="14.5",
        LBORRESU="g/dL",
        LBSTRESC="14.5",
        LBSTRESN=14.5,
        LBSTRESU="g/dL",
        LBSTNRLO=12.0,
        LBSTNRHI=17.5,
        LBNRIND="NORMAL",
        LBDTC="2026-01-02T09:00:00",
    )


def _ex() -> SdtmEx:
    return SdtmEx(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="EX",
        USUBJID="RS-1-S-001",
        EXSEQ=1,
        EXTRT="Aspirin",
        EXDOSE=81.0,
        EXDOSU="mg",
        EXROUTE="ORAL",
        EXSTDTC="2026-02-01T00:00:00",
    )


def _cm() -> SdtmCm:
    return SdtmCm(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="CM",
        USUBJID="RS-1-S-001",
        CMSEQ=1,
        CMTRT="Lisinopril",
        CMINDC="Hypertension",
        CMDOSE=10.0,
        CMDOSU="mg",
    )


def _mh() -> SdtmMh:
    return SdtmMh(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="MH",
        USUBJID="RS-1-S-001",
        MHSEQ=1,
        MHTERM="Type 2 Diabetes",
        MHCAT="ENDOCRINE",
        MHSTDTC="2020-01-01T00:00:00",
        MHONGO="Y",
    )


# ── Per-domain CSV column ordering ──────────────────────────────────────


def test_lb_csv_headers_in_sdtm_order() -> None:
    csv_bytes = lb_to_csv([_lb()])
    headers = csv_bytes.decode("utf-8").splitlines()[0].split(",")
    assert headers[0] == "STUDYID"
    assert headers[1] == "DOMAIN"
    assert headers[2] == "USUBJID"
    assert headers[3] == "LBSEQ"
    assert "LBSTRESN" in headers
    assert "LBNRIND" in headers


def test_ex_csv_headers_have_route_dose_dates() -> None:
    csv_bytes = ex_to_csv([_ex()])
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert rows[0]["EXTRT"] == "Aspirin"
    assert rows[0]["EXROUTE"] == "ORAL"
    assert rows[0]["EXDOSE"] == "81.0"


def test_cm_csv_emits_indication() -> None:
    csv_bytes = cm_to_csv([_cm()])
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert rows[0]["CMTRT"] == "Lisinopril"
    assert rows[0]["CMINDC"] == "Hypertension"


def test_mh_csv_emits_ongoing_y() -> None:
    csv_bytes = mh_to_csv([_mh()])
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert rows[0]["MHONGO"] == "Y"
    assert rows[0]["MHCAT"] == "ENDOCRINE"


# ── Bundle now ships 7 SDTM domains ────────────────────────────────────


def test_bundle_contains_new_sdtm_csvs() -> None:
    payload = build_submission_bundle(
        study_id="RS-1",
        triggered_at=None,
        dm=[],
        ae=[],
        vs=[],
        lb=[_lb()],
        ex=[_ex()],
        cm=[_cm()],
        mh=[_mh()],
        adsl=[],
        tlfs=[],
    )
    z = zipfile.ZipFile(io.BytesIO(payload))
    names = set(z.namelist())
    assert "sdtm/lb.csv" in names
    assert "sdtm/ex.csv" in names
    assert "sdtm/cm.csv" in names
    assert "sdtm/mh.csv" in names

    manifest = z.read("define-overview.txt").decode("utf-8")
    assert "SDTM LB:  1" in manifest
    assert "SDTM EX:  1" in manifest
    assert "SDTM CM:  1" in manifest
    assert "SDTM MH:  1" in manifest


def test_bundle_empty_new_domains_still_emits_headers() -> None:
    """Calling build_submission_bundle without LB/EX/CM/MH must still
    produce header-only CSVs for the new domains (backward compatible)."""
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
    names = set(z.namelist())
    assert "sdtm/lb.csv" in names
    assert "sdtm/ex.csv" in names
    # Header-only — the .splitlines()[0] is the header row, no data.
    lb_csv = z.read("sdtm/lb.csv").decode("utf-8")
    assert lb_csv.splitlines()[0].startswith("STUDYID,DOMAIN,USUBJID,LBSEQ")
    assert len(lb_csv.splitlines()) == 1
