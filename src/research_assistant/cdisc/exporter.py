"""CDISC submission export — per-dataset CSV + submission-bundle ZIP (top-6 #6).

CSV is what every modern submission preflight pipeline accepts (and what
the FDA's SDTM-Validator-2.0 reads upstream of XPT conversion). SAS XPT
format export is deferred to a follow-up — it requires either the
`xport` library (small, BSD-licensed) or hand-rolled fixed-width
records, and the regulatory teams we've targeted accept CSV from the
mapping pipeline + their own preflight tooling does the XPT translation
in their environment.

The submission bundle is a flat ZIP:

    define-overview.txt    — manifest (study id + record counts + run timestamp)
    sdtm/dm.csv
    sdtm/ae.csv
    sdtm/vs.csv
    adam/adsl.csv
    tlf/t-disposition.csv
    tlf/t-demographics.csv
    tlf/t-ae-summary.csv
    tlf/f-ae-frequency.svg
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from ..persistence.clinical.models import AdamAdsl, SdtmAe, SdtmDm, SdtmVs, TlfArtefact

# SDTM variable order per the standard implementation guide. The ORM
# columns are intentionally named identically so we can dump them by
# attribute lookup; the EXPORT_COLUMNS lists below pin the column order
# for the regulator.
_DM_COLUMNS = [
    "STUDYID", "DOMAIN", "USUBJID", "SUBJID", "SITEID",
    "AGE", "AGEU", "SEX", "RACE", "ETHNIC",
    "RFSTDTC", "RFENDTC", "ARM", "ARMCD", "COUNTRY",
]
_AE_COLUMNS = [
    "STUDYID", "DOMAIN", "USUBJID", "AESEQ",
    "AETERM", "AEDECOD", "AEBODSYS",
    "AESTDTC", "AEENDTC",
    "AESEV", "AESER", "AEREL", "AEOUT",
]
_VS_COLUMNS = [
    "STUDYID", "DOMAIN", "USUBJID", "VSSEQ",
    "VSTESTCD", "VSTEST", "VSORRES", "VSORRESU", "VSDTC",
]
_ADSL_COLUMNS = [
    "STUDYID", "USUBJID", "SUBJID", "SITEID",
    "AGE", "AGEU", "AGEGR1",
    "SEX", "RACE", "ETHNIC",
    "SAFFL", "ITTFL", "DTHFL",
    "RFSTDTC", "RFENDTC",
    "TRT01P", "TRT01A", "COUNTRY",
]


def _records_to_csv(columns: list[str], rows: Iterable[Any]) -> bytes:
    """Serialise ORM rows to CSV bytes with the given column order."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(
            [
                "" if (v := getattr(row, c, None)) is None else str(v)
                for c in columns
            ]
        )
    return buf.getvalue().encode("utf-8")


def dm_to_csv(records: Iterable[SdtmDm]) -> bytes:
    return _records_to_csv(_DM_COLUMNS, records)


def ae_to_csv(records: Iterable[SdtmAe]) -> bytes:
    return _records_to_csv(_AE_COLUMNS, records)


def vs_to_csv(records: Iterable[SdtmVs]) -> bytes:
    return _records_to_csv(_VS_COLUMNS, records)


def adsl_to_csv(records: Iterable[AdamAdsl]) -> bytes:
    return _records_to_csv(_ADSL_COLUMNS, records)


def tlf_table_to_csv(tlf: TlfArtefact) -> bytes:
    """Serialise a TlfArtefact (kind='table' or 'listing') as CSV."""
    if tlf.kind not in ("table", "listing"):
        raise ValueError(
            f"Only table/listing TLFs serialise as CSV — got kind={tlf.kind!r}."
        )
    payload = json.loads(tlf.content_json or '{"columns": [], "rows": []}')
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return buf.getvalue().encode("utf-8")


def build_submission_bundle(
    *,
    study_id: str,
    triggered_at: datetime | None,
    dm: list[SdtmDm],
    ae: list[SdtmAe],
    vs: list[SdtmVs],
    adsl: list[AdamAdsl],
    tlfs: list[TlfArtefact],
) -> bytes:
    """Build the deployment's submission bundle as a ZIP bytes payload."""
    ts = (triggered_at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_lines = [
        "CRA submission bundle",
        f"Study ID: {study_id}",
        f"Generated at: {ts}",
        "",
        "Record counts:",
        f"  SDTM DM:  {len(dm)}",
        f"  SDTM AE:  {len(ae)}",
        f"  SDTM VS:  {len(vs)}",
        f"  ADaM ADSL: {len(adsl)}",
        f"  TLF artefacts: {len(tlfs)}",
        "",
        "Notes:",
        "  - CSV format. SAS XPT conversion is left to the deploy-side preflight tooling.",
        "  - MedDRA Preferred Terms are captured as free text; deploy with a MedDRA",
        "    license to validate against the dictionary.",
        "  - TRT01P/TRT01A may show 'TBD' until the randomisation/IRT service ships.",
    ]
    manifest = "\n".join(manifest_lines).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("define-overview.txt", manifest)
        z.writestr("sdtm/dm.csv", dm_to_csv(dm))
        z.writestr("sdtm/ae.csv", ae_to_csv(ae))
        z.writestr("sdtm/vs.csv", vs_to_csv(vs))
        z.writestr("adam/adsl.csv", adsl_to_csv(adsl))
        for tlf in tlfs:
            ext = "svg" if tlf.kind == "figure" else "csv"
            content: bytes
            if tlf.kind == "figure":
                content = (tlf.svg_content or "").encode("utf-8")
            else:
                content = tlf_table_to_csv(tlf)
            z.writestr(f"tlf/{tlf.tlf_id}.{ext}", content)
    return buf.getvalue()


__all__ = [
    "adsl_to_csv",
    "ae_to_csv",
    "build_submission_bundle",
    "dm_to_csv",
    "tlf_table_to_csv",
    "vs_to_csv",
]
