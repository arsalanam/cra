"""ADaM ADSL — subject-level analysis dataset (top-6 #6).

Built from the SDTM DM + AE rows that `sdtm_mapper` just produced.
Carries the population flags (SAFFL / ITTFL / DTHFL) every regulator-
grade analysis expects.

Treatment fields (TRT01P / TRT01A) are placeholder until the
randomisation/IRT service lands — captured as TBD rather than fabricated.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..persistence.clinical.models import AdamAdsl, Allocation, ItemData, SdtmAe, SdtmDm


def _age_group(age: int | None) -> str | None:
    """ICH E1-aligned age bins for AGEGR1 stratification.

    Standard bins are <18 / 18-64 / 65-74 / >=75 — adjust per study
    protocol if needed (the field is documented as a typical ICH E1
    convention in the ORM docstring, not a hard rule).
    """
    if age is None:
        return None
    if age < 18:
        return "<18"
    if age < 65:
        return "18-64"
    if age < 75:
        return "65-74"
    return ">=75"


def derive_adsl(
    *,
    deployment_id: str,
    study_id: str,
    dm_records: Iterable[SdtmDm],
    ae_records: Iterable[SdtmAe],
    subject_item_data: dict[str, list[ItemData]] | None = None,
    allocations_by_usubjid: dict[str, Allocation] | None = None,
) -> list[AdamAdsl]:
    """Build ADSL rows from the derived SDTM DM + AE rows.

    `subject_item_data` is the per-subject ItemData list keyed by the
    subject's clinical-store id; we use its presence/absence as a
    heuristic for SAFFL ("Y" iff any post-baseline data captured). When
    not provided, SAFFL defaults to "N" — caller must opt in by passing
    the captures map.

    `allocations_by_usubjid` carries each subject's `Allocation.arm`
    keyed by USUBJID. When present, TRT01P / TRT01A are taken from the
    allocation; otherwise we fall back to DM.ARM (item-captured) or
    'TBD' (no source available — pre-randomisation runs). This is the
    eCRF-E8 (IRT) integration: post-randomisation derivation produces
    real treatment fields without re-fabricating them from items.
    """
    subject_item_data = subject_item_data or {}
    allocations_by_usubjid = allocations_by_usubjid or {}
    # Index AE rows by USUBJID for fast death-flag lookup.
    death_subjects: set[str] = set()
    for ae in ae_records:
        if ae.AEOUT == "FATAL":
            death_subjects.add(ae.USUBJID)

    out: list[AdamAdsl] = []
    for dm in dm_records:
        items = subject_item_data.get(dm.SUBJID, [])
        has_data = any(it.value not in (None, "") for it in items)
        saffl = "Y" if has_data else "N"

        allocation = allocations_by_usubjid.get(dm.USUBJID)
        if allocation is not None:
            trt01p = allocation.arm
            trt01a = allocation.arm
        else:
            trt01p = dm.ARM or "TBD"
            trt01a = dm.ARM or "TBD"

        out.append(
            AdamAdsl(
                deployment_id=deployment_id,
                STUDYID=study_id,
                USUBJID=dm.USUBJID,
                SUBJID=dm.SUBJID,
                SITEID=dm.SITEID,
                AGE=dm.AGE,
                AGEU=dm.AGEU,
                AGEGR1=_age_group(dm.AGE),
                SEX=dm.SEX,
                RACE=dm.RACE,
                ETHNIC=dm.ETHNIC,
                SAFFL=saffl,
                ITTFL="Y",
                DTHFL="Y" if dm.USUBJID in death_subjects else "N",
                RFSTDTC=dm.RFSTDTC,
                RFENDTC=dm.RFENDTC,
                TRT01P=trt01p,
                TRT01A=trt01a,
                COUNTRY=dm.COUNTRY,
            )
        )
    return out


__all__ = ["derive_adsl"]
