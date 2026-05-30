"""ADaM ADTTE deriver — TTAE / TTSAE / DEATH parameter derivation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from research_assistant.cdisc.adtte_deriver import derive_adtte


def _adsl(
    usubjid: str = "RS-1-S-001",
    rfstdtc: str = "2026-01-01T00:00:00",
    trt01a: str | None = "Drug A",
) -> Any:
    return SimpleNamespace(
        USUBJID=usubjid,
        RFSTDTC=rfstdtc,
        TRT01P=trt01a,
        TRT01A=trt01a,
    )


def _ae(
    usubjid: str,
    startdtc: str,
    *,
    serious: bool = False,
    fatal: bool = False,
    enddtc: str | None = None,
    term: str = "headache",
) -> Any:
    return SimpleNamespace(
        USUBJID=usubjid,
        AESTDTC=startdtc,
        AEENDTC=enddtc,
        AESER="Y" if serious else "N",
        AEOUT="FATAL" if fatal else "RECOVERING/RESOLVING",
        AEDECOD=term,
        AETERM=term,
    )


def test_three_params_emitted_per_subject_with_events() -> None:
    adsl = [_adsl()]
    aes = [
        _ae("RS-1-S-001", "2026-01-15T00:00:00", term="headache"),
        _ae("RS-1-S-001", "2026-02-01T00:00:00", serious=True, term="severe rash"),
        _ae("RS-1-S-001", "2026-03-01T00:00:00", fatal=True, term="acute MI"),
    ]
    rows = derive_adtte(deployment_id="dep-1", study_id="RS-1", adsl=adsl, ae_records=aes)
    by_param = {r.PARAMCD: r for r in rows}
    assert set(by_param) == {"TTAE", "TTSAE", "DEATH"}
    # AVAL is in days from 2026-01-01.
    assert by_param["TTAE"].AVAL == 14.0
    assert by_param["TTAE"].CNSR == 0
    assert by_param["TTSAE"].AVAL == 31.0
    assert by_param["TTSAE"].CNSR == 0
    assert by_param["DEATH"].AVAL == 59.0
    assert by_param["DEATH"].CNSR == 0


def test_no_qualifying_event_yields_censored_row() -> None:
    """Subject with no SAE and no death should be censored on TTSAE + DEATH."""
    adsl = [_adsl()]
    aes = [
        _ae("RS-1-S-001", "2026-01-10T00:00:00", term="nausea", enddtc="2026-01-12T00:00:00"),
    ]
    rows = derive_adtte(deployment_id="dep-1", study_id="RS-1", adsl=adsl, ae_records=aes)
    by_param = {r.PARAMCD: r for r in rows}
    # TTAE: event observed at day 9
    assert by_param["TTAE"].CNSR == 0
    assert by_param["TTAE"].AVAL == 9.0
    # TTSAE: no serious AE → censored at last follow-up (2026-01-12)
    assert by_param["TTSAE"].CNSR == 1
    assert by_param["TTSAE"].AVAL == 11.0
    # DEATH: censored at last follow-up
    assert by_param["DEATH"].CNSR == 1
    assert by_param["DEATH"].AVAL == 11.0
    assert by_param["DEATH"].EVNTDESC == "Censored at last follow-up"


def test_subject_with_no_aes_censored_at_start() -> None:
    """Subject with zero AE records → all three params censored at day 0."""
    adsl = [_adsl()]
    rows = derive_adtte(deployment_id="dep-1", study_id="RS-1", adsl=adsl, ae_records=[])
    by_param = {r.PARAMCD: r for r in rows}
    for paramcd in ("TTAE", "TTSAE", "DEATH"):
        assert by_param[paramcd].CNSR == 1
        assert by_param[paramcd].AVAL == 0.0


def test_missing_rfstdtc_skips_subject() -> None:
    """Without RFSTDTC we can't compute elapsed time — skip rather than fabricate."""
    adsl = [_adsl(rfstdtc=None)]
    aes = [_ae("RS-1-S-001", "2026-01-15T00:00:00")]
    rows = derive_adtte(deployment_id="dep-1", study_id="RS-1", adsl=adsl, ae_records=aes)
    assert rows == []


def test_trt01p_and_trt01a_propagated_from_adsl() -> None:
    adsl = [_adsl(trt01a="Drug B")]
    rows = derive_adtte(deployment_id="dep-1", study_id="RS-1", adsl=adsl, ae_records=[])
    assert all(r.TRT01P == "Drug B" for r in rows)
    assert all(r.TRT01A == "Drug B" for r in rows)


def test_first_event_picked_not_last() -> None:
    adsl = [_adsl()]
    aes = [
        _ae("RS-1-S-001", "2026-02-10T00:00:00", term="late AE"),
        _ae("RS-1-S-001", "2026-01-05T00:00:00", term="early AE"),
    ]
    rows = derive_adtte(deployment_id="dep-1", study_id="RS-1", adsl=adsl, ae_records=aes)
    by_param = {r.PARAMCD: r for r in rows}
    assert by_param["TTAE"].AVAL == 4.0
    assert by_param["TTAE"].EVNTDESC == "early AE"


def test_per_subject_independence() -> None:
    adsl = [_adsl(usubjid="RS-1-S-001"), _adsl(usubjid="RS-1-S-002")]
    aes = [
        _ae("RS-1-S-001", "2026-01-10T00:00:00", fatal=True),
        _ae("RS-1-S-002", "2026-02-20T00:00:00", term="rash"),
    ]
    rows = derive_adtte(deployment_id="dep-1", study_id="RS-1", adsl=adsl, ae_records=aes)
    s1 = {r.PARAMCD: r for r in rows if r.USUBJID == "RS-1-S-001"}
    s2 = {r.PARAMCD: r for r in rows if r.USUBJID == "RS-1-S-002"}
    assert s1["DEATH"].CNSR == 0
    assert s2["DEATH"].CNSR == 1


def test_unparseable_aestdtc_falls_through_to_censored() -> None:
    """If an AE has an unparseable AESTDTC we shouldn't crash — the
    row contributes no event date and the subject ends up censored."""
    adsl = [_adsl()]
    aes = [_ae("RS-1-S-001", "not-a-date")]
    rows = derive_adtte(deployment_id="dep-1", study_id="RS-1", adsl=adsl, ae_records=aes)
    by_param = {r.PARAMCD: r for r in rows}
    assert by_param["TTAE"].CNSR == 1
