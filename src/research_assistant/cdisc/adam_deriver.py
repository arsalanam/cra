"""ADaM ADSL — subject-level analysis dataset (top-6 #6).

Built from the SDTM DM + AE rows that `sdtm_mapper` just produced.
Carries the population flags (SAFFL / ITTFL / DTHFL) every regulator-
grade analysis expects.

Treatment fields (TRT01P / TRT01A) are placeholder until the
randomisation/IRT service lands — captured as TBD rather than fabricated.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..persistence.clinical.models import AdamAdsl, ItemData, SdtmAe, SdtmDm


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
) -> list[AdamAdsl]:
    """Build ADSL rows from the derived SDTM DM + AE rows.

    `subject_item_data` is the per-subject ItemData list keyed by the
    subject's clinical-store id; we use its presence/absence as a
    heuristic for SAFFL ("Y" iff any post-baseline data captured). When
    not provided, SAFFL defaults to "N" — caller must opt in by passing
    the captures map.
    """
    subject_item_data = subject_item_data or {}
    # Index AE rows by USUBJID for fast death-flag lookup.
    death_subjects: set[str] = set()
    for ae in ae_records:
        if ae.AEOUT == "FATAL":
            death_subjects.add(ae.USUBJID)

    out: list[AdamAdsl] = []
    for dm in dm_records:
        # SAFFL heuristic: any non-empty item data → subject had at
        # least one captured value, which corresponds to taking the
        # intervention in practice. A real Safety-population flag
        # requires the "first-dose" event from the eCRF; this is the
        # platform's best-available proxy until that event captures.
        items = subject_item_data.get(dm.SUBJID, [])
        has_data = any(it.value not in (None, "") for it in items)
        saffl = "Y" if has_data else "N"
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
                TRT01P=dm.ARM or "TBD",
                TRT01A=dm.ARM or "TBD",
                COUNTRY=dm.COUNTRY,
            )
        )
    return out


__all__ = ["derive_adsl"]
