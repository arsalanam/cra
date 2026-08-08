"""CDISC submission export — per-dataset CSV + submission-bundle ZIP.

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
    sdtm/lb.csv
    sdtm/ex.csv
    sdtm/cm.csv
    sdtm/mh.csv
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

from ..persistence.clinical.models import (
    AdamAdsl,
    AdamAdtte,
    SdtmAe,
    SdtmCm,
    SdtmDa,
    SdtmDm,
    SdtmEx,
    SdtmLb,
    SdtmMh,
    SdtmVs,
    TlfArtefact,
)
from ._metadata import DOMAIN_METADATA
from .define_xml import build_define_xml
from .xpt_writer import write_xpt

# SDTM variable order per the standard implementation guide. The ORM
# columns are intentionally named identically so we can dump them by
# attribute lookup; the EXPORT_COLUMNS lists below pin the column order
# for the regulator.
_DM_COLUMNS = [
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "SUBJID",
    "SITEID",
    "AGE",
    "AGEU",
    "SEX",
    "RACE",
    "ETHNIC",
    "RFSTDTC",
    "RFENDTC",
    "ARM",
    "ARMCD",
    "COUNTRY",
]
_AE_COLUMNS = [
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "AESEQ",
    "AETERM",
    "AEDECOD",
    "AEBODSYS",
    "AESTDTC",
    "AEENDTC",
    "AESEV",
    "AESER",
    "AEREL",
    "AEOUT",
]
_VS_COLUMNS = [
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "VSSEQ",
    "VSTESTCD",
    "VSTEST",
    "VSORRES",
    "VSORRESU",
    "VSDTC",
]
_ADSL_COLUMNS = [
    "STUDYID",
    "USUBJID",
    "SUBJID",
    "SITEID",
    "AGE",
    "AGEU",
    "AGEGR1",
    "SEX",
    "RACE",
    "ETHNIC",
    "SAFFL",
    "ITTFL",
    "DTHFL",
    "RFSTDTC",
    "RFENDTC",
    "TRT01P",
    "TRT01A",
    "COUNTRY",
]
_LB_COLUMNS = [
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "LBSEQ",
    "LBTESTCD",
    "LBTEST",
    "LBORRES",
    "LBORRESU",
    "LBSTRESC",
    "LBSTRESN",
    "LBSTRESU",
    "LBORNRLO",
    "LBORNRHI",
    "LBSTNRLO",
    "LBSTNRHI",
    "LBNRIND",
    "LBDTC",
]
_EX_COLUMNS = [
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "EXSEQ",
    "EXTRT",
    "EXDOSE",
    "EXDOSU",
    "EXROUTE",
    "EXSTDTC",
    "EXENDTC",
]
_CM_COLUMNS = [
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "CMSEQ",
    "CMTRT",
    "CMDECOD",
    "CMINDC",
    "CMDOSE",
    "CMDOSU",
    "CMSTDTC",
    "CMENDTC",
]
_MH_COLUMNS = [
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "MHSEQ",
    "MHTERM",
    "MHDECOD",
    "MHCAT",
    "MHSTDTC",
    "MHENDTC",
    "MHONGO",
]
_DA_COLUMNS = [
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "DASEQ",
    "DAREFID",
    "DATESTCD",
    "DATEST",
    "DAORRES",
    "DAORRESU",
    "DASTRESN",
    "DASTRESU",
    "DADTC",
]
_ADTTE_COLUMNS = [
    "STUDYID",
    "USUBJID",
    "PARAMCD",
    "PARAM",
    "AVAL",
    "AVALU",
    "CNSR",
    "STARTDT",
    "ADT",
    "EVNTDESC",
    "SRCDOM",
    "SRCVAR",
    "TRT01P",
    "TRT01A",
]


def _records_to_csv(columns: list[str], rows: Iterable[Any]) -> bytes:
    """Serialise ORM rows to CSV bytes with the given column order."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if (v := getattr(row, c, None)) is None else str(v) for c in columns])
    return buf.getvalue().encode("utf-8")


def dm_to_csv(records: Iterable[SdtmDm]) -> bytes:
    return _records_to_csv(_DM_COLUMNS, records)


def ae_to_csv(records: Iterable[SdtmAe]) -> bytes:
    return _records_to_csv(_AE_COLUMNS, records)


def vs_to_csv(records: Iterable[SdtmVs]) -> bytes:
    return _records_to_csv(_VS_COLUMNS, records)


def adsl_to_csv(records: Iterable[AdamAdsl]) -> bytes:
    return _records_to_csv(_ADSL_COLUMNS, records)


def lb_to_csv(records: Iterable[SdtmLb]) -> bytes:
    return _records_to_csv(_LB_COLUMNS, records)


def ex_to_csv(records: Iterable[SdtmEx]) -> bytes:
    return _records_to_csv(_EX_COLUMNS, records)


def cm_to_csv(records: Iterable[SdtmCm]) -> bytes:
    return _records_to_csv(_CM_COLUMNS, records)


def mh_to_csv(records: Iterable[SdtmMh]) -> bytes:
    return _records_to_csv(_MH_COLUMNS, records)


def da_to_csv(records: Iterable[SdtmDa]) -> bytes:
    return _records_to_csv(_DA_COLUMNS, records)


def adtte_to_csv(records: Iterable[AdamAdtte]) -> bytes:
    return _records_to_csv(_ADTTE_COLUMNS, records)


# ── XPT writers (one per dataset; all delegate to write_xpt) ────────────


def dm_to_xpt(records: Iterable[SdtmDm]) -> bytes:
    return write_xpt(DOMAIN_METADATA["DM"], records)


def ae_to_xpt(records: Iterable[SdtmAe]) -> bytes:
    return write_xpt(DOMAIN_METADATA["AE"], records)


def vs_to_xpt(records: Iterable[SdtmVs]) -> bytes:
    return write_xpt(DOMAIN_METADATA["VS"], records)


def lb_to_xpt(records: Iterable[SdtmLb]) -> bytes:
    return write_xpt(DOMAIN_METADATA["LB"], records)


def ex_to_xpt(records: Iterable[SdtmEx]) -> bytes:
    return write_xpt(DOMAIN_METADATA["EX"], records)


def cm_to_xpt(records: Iterable[SdtmCm]) -> bytes:
    return write_xpt(DOMAIN_METADATA["CM"], records)


def mh_to_xpt(records: Iterable[SdtmMh]) -> bytes:
    return write_xpt(DOMAIN_METADATA["MH"], records)


def da_to_xpt(records: Iterable[SdtmDa]) -> bytes:
    return write_xpt(DOMAIN_METADATA["DA"], records)


def adsl_to_xpt(records: Iterable[AdamAdsl]) -> bytes:
    return write_xpt(DOMAIN_METADATA["ADSL"], records)


def adtte_to_xpt(records: Iterable[AdamAdtte]) -> bytes:
    return write_xpt(DOMAIN_METADATA["ADTTE"], records)


def tlf_table_to_csv(tlf: TlfArtefact) -> bytes:
    """Serialise a TlfArtefact (kind='table' or 'listing') as CSV."""
    if tlf.kind not in ("table", "listing"):
        raise ValueError(f"Only table/listing TLFs serialise as CSV — got kind={tlf.kind!r}.")
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
    lb: list[SdtmLb] | None = None,
    ex: list[SdtmEx] | None = None,
    cm: list[SdtmCm] | None = None,
    mh: list[SdtmMh] | None = None,
    da: list[SdtmDa] | None = None,
    adtte: list[AdamAdtte] | None = None,
) -> bytes:
    """Build the deployment's submission bundle as a ZIP bytes payload.

    LB / EX / CM / MH / ADTTE are optional so existing callers (and
    the empty-trial test path) keep working with just the original
    DM/AE/VS/ADSL/TLF arguments. The pipeline always passes lists
    today (possibly empty)."""
    lb_records = lb or []
    ex_records = ex or []
    cm_records = cm or []
    mh_records = mh or []
    da_records = da or []
    adtte_records = adtte or []
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
        f"  SDTM LB:  {len(lb_records)}",
        f"  SDTM EX:  {len(ex_records)}",
        f"  SDTM CM:  {len(cm_records)}",
        f"  SDTM MH:  {len(mh_records)}",
        f"  SDTM DA:  {len(da_records)}",
        f"  ADaM ADSL: {len(adsl)}",
        f"  ADaM ADTTE: {len(adtte_records)}",
        f"  TLF artefacts: {len(tlfs)}",
        "",
        "Files in this bundle:",
        "  - define.xml      — CDISC Define-XML v2.1 (regulator-grade metadata)",
        "  - sdtm/*.csv      — character-encoded SDTM datasets (preflight-friendly)",
        "  - sdtm/*.xpt      — SAS Transport v5 SDTM datasets (CDISC IG default)",
        "  - adam/{adsl,adtte}.{csv,xpt} — analysis datasets in both formats",
        "  - tlf/*.{csv,svg} — tables / figures (figures = embedded SVG or PNG data URI)",
        "",
        "Notes:",
        "  - XPT v5 is hand-rolled (ASCII-only CHAR + IBM-360 NUM). Column names",
        "    are capped at 8 chars per the v5 limit; CSV carries the same data",
        "    without the truncation risk.",
        "  - MedDRA Preferred Terms (AE.AEDECOD / MH.MHDECOD) and WHODrug names",
        "    (CM.CMDECOD) are captured as free text; deploy with the dictionary",
        "    licenses to validate.",
        "  - TRT01P/TRT01A may show 'TBD' until the randomisation/IRT service ships.",
    ]
    manifest = "\n".join(manifest_lines).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # Human-readable manifest + machine-readable Define-XML side by
        # side. The regulator's preflight reads define.xml; the
        # `.txt` is a quick sanity check for the deploy team.
        z.writestr("define-overview.txt", manifest)
        z.writestr("define.xml", build_define_xml(study_id=study_id))

        # CSV (preferred by FDA's transitional preflight tooling).
        z.writestr("sdtm/dm.csv", dm_to_csv(dm))
        z.writestr("sdtm/ae.csv", ae_to_csv(ae))
        z.writestr("sdtm/vs.csv", vs_to_csv(vs))
        z.writestr("sdtm/lb.csv", lb_to_csv(lb_records))
        z.writestr("sdtm/ex.csv", ex_to_csv(ex_records))
        z.writestr("sdtm/cm.csv", cm_to_csv(cm_records))
        z.writestr("sdtm/mh.csv", mh_to_csv(mh_records))
        z.writestr("sdtm/da.csv", da_to_csv(da_records))
        z.writestr("adam/adsl.csv", adsl_to_csv(adsl))
        z.writestr("adam/adtte.csv", adtte_to_csv(adtte_records))

        # SAS Transport (XPT v5) — the CDISC IG submission default.
        z.writestr("sdtm/dm.xpt", dm_to_xpt(dm))
        z.writestr("sdtm/ae.xpt", ae_to_xpt(ae))
        z.writestr("sdtm/vs.xpt", vs_to_xpt(vs))
        z.writestr("sdtm/lb.xpt", lb_to_xpt(lb_records))
        z.writestr("sdtm/ex.xpt", ex_to_xpt(ex_records))
        z.writestr("sdtm/cm.xpt", cm_to_xpt(cm_records))
        z.writestr("sdtm/mh.xpt", mh_to_xpt(mh_records))
        z.writestr("sdtm/da.xpt", da_to_xpt(da_records))
        z.writestr("adam/adsl.xpt", adsl_to_xpt(adsl))
        z.writestr("adam/adtte.xpt", adtte_to_xpt(adtte_records))

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
    "adsl_to_xpt",
    "adtte_to_csv",
    "adtte_to_xpt",
    "ae_to_csv",
    "ae_to_xpt",
    "build_submission_bundle",
    "cm_to_csv",
    "cm_to_xpt",
    "da_to_csv",
    "da_to_xpt",
    "dm_to_csv",
    "dm_to_xpt",
    "ex_to_csv",
    "ex_to_xpt",
    "lb_to_csv",
    "lb_to_xpt",
    "mh_to_csv",
    "mh_to_xpt",
    "tlf_table_to_csv",
    "vs_to_csv",
    "vs_to_xpt",
]
