"""Time-to-event summary TLF table — pure-Python K-M median."""

from __future__ import annotations

import json

from research_assistant.cdisc.tlf_generator import _km_median, generate_tlfs
from research_assistant.persistence.clinical.models import AdamAdtte


def _adtte(usubjid: str, paramcd: str, aval: float, cnsr: int) -> AdamAdtte:
    return AdamAdtte(
        deployment_id="dep-1",
        STUDYID="RS-1",
        USUBJID=usubjid,
        PARAMCD=paramcd,
        PARAM=f"Param {paramcd}",
        AVAL=aval,
        AVALU="DAYS",
        CNSR=cnsr,
    )


def test_km_median_all_events() -> None:
    # 4 events at 1,2,3,4 — at day 2 S drops from 0.75 to 0.5, median = 2.
    median = _km_median([1.0, 2.0, 3.0, 4.0], [1, 1, 1, 1])
    assert median == 2.0


def test_km_median_not_reached_when_no_event_drops_below_half() -> None:
    """All-censored subjects → K-M stays at 1.0 → median never reached."""
    median = _km_median([1.0, 2.0, 3.0], [0, 0, 0])
    assert median is None


def test_km_median_with_late_censoring_still_drops_to_zero() -> None:
    """Censoring at 1, 2 leaves 1 subject at risk at t=3; the single
    event there drives surv to 0 → median = 3."""
    median = _km_median([1.0, 2.0, 3.0], [0, 0, 1])
    assert median == 3.0


def test_km_median_handles_censoring_correctly() -> None:
    """3 events at 10, 20, 30; 2 censors at 5, 15 — median between event 1 and event 2."""
    # at 5: 1 censor, n=5 → at_risk goes to 4 after; no event so surv stays 1
    # at 10: 1 event, n=4 → surv = 0.75
    # at 15: 1 censor, n=3 → no event; surv stays 0.75
    # at 20: 1 event, n=2 → surv = 0.75 * (1 - 1/2) = 0.375 → drops below 0.5 at 20
    median = _km_median([5.0, 10.0, 15.0, 20.0, 30.0], [0, 1, 0, 1, 1])
    assert median == 20.0


def test_generate_tlfs_appends_tte_summary_when_adtte_provided() -> None:
    tlfs = generate_tlfs(
        deployment_id="dep-1",
        adsl=[],
        ae_records=[],
        adtte_records=[
            _adtte("RS-1-S-001", "TTAE", 5.0, 0),
            _adtte("RS-1-S-002", "TTAE", 15.0, 0),
        ],
    )
    tte_summary = [t for t in tlfs if t.tlf_id == "t-tte-summary"]
    assert len(tte_summary) == 1
    payload = json.loads(tte_summary[0].content_json)
    assert payload["columns"] == ["PARAMCD", "Parameter", "n", "Events", "Median (days)"]
    assert payload["rows"][0][0] == "TTAE"
    assert payload["rows"][0][2] == 2  # n


def test_generate_tlfs_omits_tte_summary_when_adtte_none() -> None:
    tlfs = generate_tlfs(deployment_id="dep-1", adsl=[], ae_records=[])
    assert all(t.tlf_id != "t-tte-summary" for t in tlfs)


def test_tte_summary_stub_when_no_rows() -> None:
    tlfs = generate_tlfs(
        deployment_id="dep-1", adsl=[], ae_records=[], adtte_records=[]
    )
    tte = next(t for t in tlfs if t.tlf_id == "t-tte-summary")
    payload = json.loads(tte.content_json)
    assert payload["rows"] == [["(no ADTTE rows)", "—", "—", "—", "—"]]


def test_tte_summary_reports_events_and_censored_separately() -> None:
    tlfs = generate_tlfs(
        deployment_id="dep-1",
        adsl=[],
        ae_records=[],
        adtte_records=[
            _adtte("S1", "TTAE", 10.0, 0),
            _adtte("S2", "TTAE", 20.0, 0),
            _adtte("S3", "TTAE", 30.0, 1),
        ],
    )
    tte = next(t for t in tlfs if t.tlf_id == "t-tte-summary")
    payload = json.loads(tte.content_json)
    row = payload["rows"][0]
    assert row[2] == 3  # n
    assert row[3] == "2 (1 censored)"
