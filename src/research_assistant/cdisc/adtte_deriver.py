"""ADaM ADTTE — Time-to-Event analysis dataset (CDISC follow-up).

Three parameters in the MVP slice — extend by adding to `_PARAMS`:

  • TTAE   — Time to first AE       (event = earliest AE.AESTDTC)
  • TTSAE  — Time to first SAE      (event = earliest AE.AESTDTC where AESER=Y)
  • DEATH  — Overall Survival       (event = earliest FATAL AE.AESTDTC)

Each row encodes one subject × one parameter. AVAL is elapsed time in
days from STARTDT (= DM.RFSTDTC) to ADT. CNSR follows ADaM convention:
0 = event observed, 1 = censored. Subjects with no event get CNSR=1
and ADT set to the latest known follow-up date (max of AE end dates,
falling back to RFSTDTC if none).

Dates are parsed by the same `_parse_iso8601` helper used elsewhere
in the CDISC package; missing or unparseable START dates skip the row
for that subject + parameter rather than fabricating a placeholder.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..persistence.clinical.models import AdamAdsl, AdamAdtte, SdtmAe


def _parse_iso8601(value: Any) -> datetime | None:
    """Permissive parser — accepts the strings emitted by `_to_iso8601`
    in `sdtm_mapper`, plus the date-only forms commonly captured."""
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


@dataclass(frozen=True)
class _Event:
    when: datetime
    description: str
    src_var: str = "AESTDTC"


def _first_event(
    ae_records: list[SdtmAe], *, only_serious: bool = False, only_fatal: bool = False
) -> _Event | None:
    """Earliest AE meeting the criteria — None if no qualifying record."""
    candidates: list[_Event] = []
    for ae in ae_records:
        if only_serious and ae.AESER != "Y":
            continue
        if only_fatal and ae.AEOUT != "FATAL":
            continue
        when = _parse_iso8601(ae.AESTDTC)
        if when is None:
            continue
        label = ae.AEDECOD or ae.AETERM or "Adverse event"
        candidates.append(_Event(when=when, description=label))
    if not candidates:
        return None
    return min(candidates, key=lambda e: e.when)


def _last_followup(ae_records: list[SdtmAe], fallback: datetime) -> datetime:
    """Latest known follow-up date — used as the censor date when no event."""
    dates: list[datetime] = []
    for ae in ae_records:
        for v in (ae.AEENDTC, ae.AESTDTC):
            parsed = _parse_iso8601(v)
            if parsed is not None:
                dates.append(parsed)
    return max(dates) if dates else fallback


def _days_between(start: datetime, end: datetime) -> float:
    """Whole days between two datetimes (positive). Returns 0 on degenerate
    interval rather than negative; the deriver shouldn't generate those
    but defensively clamp."""
    delta = end - start
    return max(delta.total_seconds() / 86400.0, 0.0)


def derive_adtte(
    *,
    deployment_id: str,
    study_id: str,
    adsl: Iterable[AdamAdsl],
    ae_records: Iterable[SdtmAe],
) -> list[AdamAdtte]:
    """Build ADTTE rows from the derived ADSL + SDTM AE rows.

    Pure Python — no sandbox / no analysis. The downstream K-M / Cox
    survival analyses (rendered in the sandbox) consume the rows this
    function emits.
    """
    ae_list = list(ae_records)
    ae_by_subject: dict[str, list[SdtmAe]] = {}
    for ae in ae_list:
        ae_by_subject.setdefault(ae.USUBJID, []).append(ae)

    out: list[AdamAdtte] = []
    for subj in adsl:
        start = _parse_iso8601(subj.RFSTDTC)
        if start is None:
            continue
        aes = ae_by_subject.get(subj.USUBJID, [])
        followup = _last_followup(aes, fallback=start)

        finders: list[tuple[str, str, Callable[[list[SdtmAe]], _Event | None]]] = [
            ("TTAE", "Time to First AE", lambda a: _first_event(a)),
            (
                "TTSAE",
                "Time to First Serious AE",
                lambda a: _first_event(a, only_serious=True),
            ),
            (
                "DEATH",
                "Overall Survival (Time to Death)",
                lambda a: _first_event(a, only_fatal=True),
            ),
        ]
        for paramcd, param_label, finder in finders:
            event = finder(aes)
            if event is not None:
                aval = _days_between(start, event.when)
                cnsr = 0
                adt = event.when
                evntdesc = event.description
                src_var = event.src_var
            else:
                aval = _days_between(start, followup)
                cnsr = 1
                adt = followup
                evntdesc = "Censored at last follow-up"
                src_var = "AEENDTC" if followup is not start else "RFSTDTC"

            out.append(
                AdamAdtte(
                    deployment_id=deployment_id,
                    STUDYID=study_id,
                    USUBJID=subj.USUBJID,
                    PARAMCD=paramcd,
                    PARAM=param_label,
                    AVAL=round(aval, 2),
                    AVALU="DAYS",
                    CNSR=cnsr,
                    STARTDT=start.strftime("%Y-%m-%dT%H:%M:%S"),
                    ADT=adt.strftime("%Y-%m-%dT%H:%M:%S"),
                    EVNTDESC=evntdesc,
                    SRCDOM="AE" if cnsr == 0 else "AE" if aes else "DM",
                    SRCVAR=src_var,
                    TRT01P=subj.TRT01P,
                    TRT01A=subj.TRT01A,
                )
            )
    return out


__all__ = ["derive_adtte"]
