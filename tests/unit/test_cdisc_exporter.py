"""CDISC export — CSV headers + submission-bundle ZIP shape."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime

from research_assistant.cdisc.exporter import (
    adsl_to_csv,
    ae_to_csv,
    build_submission_bundle,
    dm_to_csv,
    tlf_table_to_csv,
    vs_to_csv,
)
from research_assistant.persistence.clinical.models import (
    AdamAdsl,
    SdtmAe,
    SdtmDm,
    SdtmVs,
    TlfArtefact,
)


def _dm() -> SdtmDm:
    return SdtmDm(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="DM",
        USUBJID="RS-1-S-001",
        SUBJID="S-001",
        AGE=42,
        AGEU="YEARS",
        SEX="F",
        RACE="WHITE",
    )


def _ae() -> SdtmAe:
    return SdtmAe(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="AE",
        USUBJID="RS-1-S-001",
        AESEQ=1,
        AETERM="headache",
        AESEV="MILD",
        AESER="N",
        AEOUT="RECOVERING/RESOLVING",
    )


def _vs() -> SdtmVs:
    return SdtmVs(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="VS",
        USUBJID="RS-1-S-001",
        VSSEQ=1,
        VSTESTCD="SYSBP",
        VSTEST="Systolic Blood Pressure",
        VSORRES="120",
        VSORRESU="mmHg",
    )


def _adsl() -> AdamAdsl:
    return AdamAdsl(
        deployment_id="dep-1",
        STUDYID="RS-1",
        USUBJID="RS-1-S-001",
        SUBJID="S-001",
        AGE=42,
        SAFFL="Y",
        ITTFL="Y",
        DTHFL="N",
        TRT01P="TBD",
        TRT01A="TBD",
    )


def _tlf_table() -> TlfArtefact:
    return TlfArtefact(
        deployment_id="dep-1",
        kind="table",
        tlf_id="t-disposition",
        title="Subject Disposition",
        content_json=json.dumps({"columns": ["Status", "n"], "rows": [["ITT", 100], ["SAF", 95]]}),
    )


# ── Per-domain CSV ──────────────────────────────────────────────────────


def test_dm_csv_headers_in_sdtm_order() -> None:
    csv_bytes = dm_to_csv([_dm()])
    headers = csv_bytes.decode("utf-8").splitlines()[0].split(",")
    # Confirm the conventional SDTM order — STUDYID first, USUBJID third.
    assert headers[0] == "STUDYID"
    assert headers[2] == "USUBJID"
    assert "ARMCD" in headers


def test_ae_csv_propagates_aeseq_and_severity() -> None:
    csv_bytes = ae_to_csv([_ae()])
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert rows[0]["AESEQ"] == "1"
    assert rows[0]["AESEV"] == "MILD"


def test_vs_csv_emits_units() -> None:
    csv_bytes = vs_to_csv([_vs()])
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert rows[0]["VSTESTCD"] == "SYSBP"
    assert rows[0]["VSORRESU"] == "mmHg"


def test_adsl_csv_emits_population_flags() -> None:
    csv_bytes = adsl_to_csv([_adsl()])
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert rows[0]["SAFFL"] == "Y"
    assert rows[0]["DTHFL"] == "N"
    assert rows[0]["ITTFL"] == "Y"


def test_tlf_table_to_csv_preserves_columns() -> None:
    csv_bytes = tlf_table_to_csv(_tlf_table())
    text = csv_bytes.decode("utf-8")
    assert text.splitlines()[0] == "Status,n"
    assert "ITT,100" in text


# ── Submission bundle ──────────────────────────────────────────────────


def test_submission_bundle_signature_and_manifest() -> None:
    payload = build_submission_bundle(
        study_id="RS-1",
        triggered_at=datetime(2026, 5, 29, 12, 0, tzinfo=UTC),
        dm=[_dm()],
        ae=[_ae()],
        vs=[_vs()],
        adsl=[_adsl()],
        tlfs=[_tlf_table()],
    )
    assert payload[:4] == b"PK\x03\x04"  # ZIP signature
    z = zipfile.ZipFile(io.BytesIO(payload))
    names = set(z.namelist())
    assert "define-overview.txt" in names
    assert "sdtm/dm.csv" in names
    assert "sdtm/ae.csv" in names
    assert "sdtm/vs.csv" in names
    assert "adam/adsl.csv" in names
    assert "tlf/t-disposition.csv" in names
    # Manifest carries the counts + study id
    manifest = z.read("define-overview.txt").decode("utf-8")
    assert "Study ID: RS-1" in manifest
    assert "SDTM DM:  1" in manifest
    assert "TLF artefacts: 1" in manifest


def test_submission_bundle_with_no_records_still_emits_valid_zip() -> None:
    """An empty trial (no data captured yet) still gets a valid bundle —
    every CSV is just a header row. Helpful for testing the pipeline
    end-to-end before any real data lands."""
    payload = build_submission_bundle(
        study_id="RS-1",
        triggered_at=None,
        dm=[],
        ae=[],
        vs=[],
        adsl=[],
        tlfs=[],
    )
    assert payload[:4] == b"PK\x03\x04"
    z = zipfile.ZipFile(io.BytesIO(payload))
    assert "sdtm/dm.csv" in z.namelist()
