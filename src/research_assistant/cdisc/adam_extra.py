"""ADaM ADAE / ADCM / ADLB / ADVS derivers (the FDA-recommended dataset set).

All four build on the already-derived ADSL (for treatment + population) and
the corresponding SDTM domain:

  • ADAE (OCCDS) ← SDTM AE — plus TRTEMFL (treatment-emergent) + AOCCFL
    (first occurrence per subject).
  • ADCM (OCCDS) ← SDTM CM — con-meds merged with treatment/population.
  • ADLB (BDS)   ← SDTM LB — one record per lab result, with the BDS
    baseline/change value-add (ABLFL / BASE / CHG).
  • ADVS (BDS)   ← SDTM VS — vitals, same baseline/change derivation.

Pure Python (no sandbox). Baseline is the earliest dated record per
(subject, PARAMCD); CHG = AVAL − BASE for post-baseline records.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ..persistence.clinical.models import (
    AdamAdae,
    AdamAdcm,
    AdamAdlb,
    AdamAdsl,
    AdamAdvs,
    SdtmAe,
    SdtmCm,
    SdtmLb,
    SdtmVs,
)


def _parse_iso8601(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _adsl_index(adsl: Iterable[AdamAdsl]) -> dict[str, AdamAdsl]:
    return {a.USUBJID: a for a in adsl}


# ── OCCDS: ADAE / ADCM ───────────────────────────────────────────────────


def derive_adae(
    *,
    deployment_id: str,
    study_id: str,
    adsl: Iterable[AdamAdsl],
    ae_records: Iterable[SdtmAe],
) -> list[AdamAdae]:
    """One ADAE row per SDTM AE, with TRTEMFL + AOCCFL flags."""
    adsl_by = _adsl_index(adsl)
    events = sorted(ae_records, key=lambda a: (a.USUBJID, a.AESEQ))
    seen_terms: dict[str, set[str]] = {}
    out: list[AdamAdae] = []
    for ae in events:
        subj = adsl_by.get(ae.USUBJID)
        rfst = _parse_iso8601(subj.RFSTDTC) if subj is not None else None
        aest = _parse_iso8601(ae.AESTDTC)
        trtemfl = "Y" if (rfst is not None and aest is not None and aest >= rfst) else ""
        term = ae.AEDECOD or ae.AETERM or ""
        subj_seen = seen_terms.setdefault(ae.USUBJID, set())
        aoccfl = "Y" if term and term not in subj_seen else ""
        if term:
            subj_seen.add(term)
        out.append(
            AdamAdae(
                deployment_id=deployment_id,
                STUDYID=study_id,
                USUBJID=ae.USUBJID,
                ASEQ=ae.AESEQ,
                TRTA=subj.TRT01A if subj is not None else None,
                TRTP=subj.TRT01P if subj is not None else None,
                AGE=subj.AGE if subj is not None else None,
                SEX=subj.SEX if subj is not None else None,
                SAFFL=subj.SAFFL if subj is not None else "N",
                AEDECOD=ae.AEDECOD,
                AETERM=ae.AETERM,
                AEBODSYS=ae.AEBODSYS,
                AESEV=ae.AESEV,
                AESER=ae.AESER,
                AEREL=ae.AEREL,
                AEOUT=ae.AEOUT,
                ASTDT=ae.AESTDTC,
                AENDT=ae.AEENDTC,
                TRTEMFL=trtemfl,
                AOCCFL=aoccfl,
            )
        )
    return out


def derive_adcm(
    *,
    deployment_id: str,
    study_id: str,
    adsl: Iterable[AdamAdsl],
    cm_records: Iterable[SdtmCm],
) -> list[AdamAdcm]:
    """One ADCM row per SDTM CM, merged with ADSL treatment/population."""
    adsl_by = _adsl_index(adsl)
    out: list[AdamAdcm] = []
    for cm in sorted(cm_records, key=lambda c: (c.USUBJID, c.CMSEQ)):
        subj = adsl_by.get(cm.USUBJID)
        out.append(
            AdamAdcm(
                deployment_id=deployment_id,
                STUDYID=study_id,
                USUBJID=cm.USUBJID,
                ASEQ=cm.CMSEQ,
                TRTA=subj.TRT01A if subj is not None else None,
                TRTP=subj.TRT01P if subj is not None else None,
                SAFFL=subj.SAFFL if subj is not None else "N",
                CMDECOD=cm.CMDECOD,
                CMTRT=cm.CMTRT,
                CMINDC=cm.CMINDC,
                CMDOSE=_to_float(cm.CMDOSE),
                CMDOSU=cm.CMDOSU,
                ASTDT=cm.CMSTDTC,
                AENDT=cm.CMENDTC,
            )
        )
    return out


# ── BDS: ADLB / ADVS ─────────────────────────────────────────────────────


def _assign_baseline_change(rows: list[Any]) -> None:
    """In-place: set ABLFL/BASE/CHG on BDS rows.

    Baseline = the earliest-dated record per (USUBJID, PARAMCD) — that row
    gets ABLFL='Y'. BASE is that record's AVAL, propagated to every record
    in the group; CHG = AVAL − BASE (None when either is missing). Rows
    with no parseable ADT sort last so a dated record wins the baseline.
    """
    groups: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        groups.setdefault((r.USUBJID, r.PARAMCD), []).append(r)
    _far = datetime.max
    for group in groups.values():
        group.sort(key=lambda r: _parse_iso8601(r.ADT) or _far)
        baseline = group[0]
        base_val = baseline.AVAL
        for r in group:
            # Set ABLFL explicitly (the model default only applies at DB
            # flush, so in-memory rows would otherwise be None).
            r.ABLFL = "Y" if r is baseline else ""
            r.BASE = base_val
            r.CHG = (
                round(r.AVAL - base_val, 6)
                if (r.AVAL is not None and base_val is not None)
                else None
            )


def derive_adlb(
    *,
    deployment_id: str,
    study_id: str,
    adsl: Iterable[AdamAdsl],
    lb_records: Iterable[SdtmLb],
) -> list[AdamAdlb]:
    adsl_by = _adsl_index(adsl)
    per_subject_seq: dict[str, int] = {}
    out: list[AdamAdlb] = []
    for lb in sorted(lb_records, key=lambda r: (r.USUBJID, r.LBSEQ)):
        subj = adsl_by.get(lb.USUBJID)
        per_subject_seq[lb.USUBJID] = per_subject_seq.get(lb.USUBJID, 0) + 1
        out.append(
            AdamAdlb(
                deployment_id=deployment_id,
                STUDYID=study_id,
                USUBJID=lb.USUBJID,
                ASEQ=per_subject_seq[lb.USUBJID],
                TRTA=subj.TRT01A if subj is not None else None,
                TRTP=subj.TRT01P if subj is not None else None,
                PARAMCD=lb.LBTESTCD,
                PARAM=lb.LBTEST,
                AVAL=_to_float(lb.LBSTRESN),
                AVALU=lb.LBSTRESU,
                ADT=lb.LBDTC,
                ANRIND=lb.LBNRIND,
                A1LO=_to_float(lb.LBSTNRLO),
                A1HI=_to_float(lb.LBSTNRHI),
            )
        )
    _assign_baseline_change(out)
    return out


def derive_advs(
    *,
    deployment_id: str,
    study_id: str,
    adsl: Iterable[AdamAdsl],
    vs_records: Iterable[SdtmVs],
) -> list[AdamAdvs]:
    adsl_by = _adsl_index(adsl)
    per_subject_seq: dict[str, int] = {}
    out: list[AdamAdvs] = []
    for vs in sorted(vs_records, key=lambda r: (r.USUBJID, r.VSSEQ)):
        subj = adsl_by.get(vs.USUBJID)
        per_subject_seq[vs.USUBJID] = per_subject_seq.get(vs.USUBJID, 0) + 1
        out.append(
            AdamAdvs(
                deployment_id=deployment_id,
                STUDYID=study_id,
                USUBJID=vs.USUBJID,
                ASEQ=per_subject_seq[vs.USUBJID],
                TRTA=subj.TRT01A if subj is not None else None,
                TRTP=subj.TRT01P if subj is not None else None,
                PARAMCD=vs.VSTESTCD,
                PARAM=vs.VSTEST,
                AVAL=_to_float(vs.VSORRES),
                AVALU=vs.VSORRESU,
                ADT=vs.VSDTC,
            )
        )
    _assign_baseline_change(out)
    return out


__all__ = ["derive_adae", "derive_adcm", "derive_adlb", "derive_advs"]
