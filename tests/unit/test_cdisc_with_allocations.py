"""ADSL TRT01P/A pulls from Allocation when present (IRT integration)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from research_assistant.cdisc.adam_deriver import derive_adsl


def _dm(usubjid: str = "RS-1-S-001", arm: str | None = None) -> Any:
    return SimpleNamespace(
        USUBJID=usubjid,
        SUBJID=usubjid.split("-")[-1],
        SITEID="site-1",
        AGE=42,
        AGEU="YEARS",
        SEX="F",
        RACE="WHITE",
        ETHNIC=None,
        RFSTDTC="2026-01-01T00:00:00",
        RFENDTC=None,
        ARM=arm,
        COUNTRY=None,
    )


def _allocation(arm: str) -> Any:
    return SimpleNamespace(arm=arm)


def test_adsl_pulls_trt_from_allocation_when_present() -> None:
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm()],
        ae_records=[],
        allocations_by_usubjid={"RS-1-S-001": _allocation("Drug A")},
    )
    assert adsl.TRT01P == "Drug A"
    assert adsl.TRT01A == "Drug A"


def test_adsl_falls_back_to_tbd_when_no_allocation_and_no_arm() -> None:
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm()],
        ae_records=[],
    )
    assert adsl.TRT01P == "TBD"
    assert adsl.TRT01A == "TBD"


def test_adsl_uses_dm_arm_when_allocation_missing_but_arm_present() -> None:
    """Backwards compatibility: if a DM row carried an item-captured
    ARM but the subject wasn't routed through IRT, we honour that."""
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm(arm="Standard of Care")],
        ae_records=[],
    )
    assert adsl.TRT01P == "Standard of Care"
    assert adsl.TRT01A == "Standard of Care"


def test_adsl_allocation_takes_precedence_over_dm_arm() -> None:
    """When both an Allocation and a DM.ARM exist (e.g. the form had a
    stale arm value pre-randomisation), the IRT allocation wins."""
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm(arm="Stale Pre-Rand Arm")],
        ae_records=[],
        allocations_by_usubjid={"RS-1-S-001": _allocation("Drug B")},
    )
    assert adsl.TRT01P == "Drug B"


def test_adsl_subjects_without_allocation_still_get_tbd() -> None:
    """Mixed deployment: one subject randomised, one not."""
    rows = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm(usubjid="RS-1-S-001"), _dm(usubjid="RS-1-S-002")],
        ae_records=[],
        allocations_by_usubjid={"RS-1-S-001": _allocation("Drug A")},
    )
    by_usubjid = {r.USUBJID: r for r in rows}
    assert by_usubjid["RS-1-S-001"].TRT01P == "Drug A"
    assert by_usubjid["RS-1-S-002"].TRT01P == "TBD"
