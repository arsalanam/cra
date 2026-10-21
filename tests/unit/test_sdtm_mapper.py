"""SDTM mapper — DM / AE / VS derivation + controlled terminology."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from research_assistant.cdisc.sdtm_mapper import (
    BuiltinPythonMapper,
    ItemMappingConfig,
    _to_iso8601,
    _usubjid,
)


def _subject(**overrides: object) -> Any:
    base = dict(
        id="subj-1",
        deployment_id="dep-1",
        site_id="site-1",
        subject_code="S-001",
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _item(**overrides: object) -> Any:
    base = dict(
        item_id="age",
        value="42",
        entered_at=datetime(2026, 1, 2, 9, 0, tzinfo=UTC),
        form_instance_id="fi-1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _ae(**overrides: object) -> Any:
    base = dict(
        subject_id="subj-1",
        subject=_subject(),
        term_text="headache",
        meddra_pt=None,
        severity_grade=3,
        outcome="recovering",
        relationship_to_intervention="possible",
        start_date=datetime(2026, 1, 5, tzinfo=UTC),
        end_date=None,
        reported_at=datetime(2026, 1, 5, 8, 0, tzinfo=UTC),
        is_serious=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Helpers ─────────────────────────────────────────────────────────────


def test_usubjid_combines_study_and_subject_codes() -> None:
    assert _usubjid("RS-1", "S-001") == "RS-1-S-001"


def test_to_iso8601_handles_datetime() -> None:
    dt = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    assert _to_iso8601(dt) == "2026-05-29T12:00:00"


def test_to_iso8601_passes_through_unparseable_string() -> None:
    assert _to_iso8601("not-a-date") == "not-a-date"


# ── DM derivation ───────────────────────────────────────────────────────


def test_derive_dm_maps_age_sex_race_from_item_data() -> None:
    mapper = BuiltinPythonMapper()
    cfg = ItemMappingConfig()
    subj = _subject(subject_code="S-001")
    items = [
        _item(item_id="age", value="42"),
        _item(item_id="sex", value="Female"),
        _item(item_id="race", value="Asian"),
    ]
    [dm] = mapper.derive_dm(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects=[subj],
        item_data_by_subject={subj.id: items},
        config=cfg,
    )
    assert dm.USUBJID == "RS-1-S-001"
    assert dm.AGE == 42
    # Controlled terminology lookup
    assert dm.SEX == "F"
    assert dm.RACE == "ASIAN"
    assert dm.DOMAIN == "DM"


def test_derive_dm_passes_through_unmapped_values() -> None:
    """If a value isn't in the controlled-terminology table, the raw
    string passes through verbatim so the deploy can catch + correct."""
    mapper = BuiltinPythonMapper()
    [dm] = mapper.derive_dm(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects=[_subject()],
        item_data_by_subject={"subj-1": [_item(item_id="sex", value="Nonbinary")]},
        config=ItemMappingConfig(),
    )
    assert dm.SEX == "Nonbinary"  # unmapped, passes through


def test_derive_dm_honours_custom_item_mapping() -> None:
    """A deployment that uses non-default item ids passes a custom map."""
    mapper = BuiltinPythonMapper()
    cfg = ItemMappingConfig(dm_item_map={"patient_age": "AGE"})
    items = [_item(item_id="patient_age", value="55")]
    [dm] = mapper.derive_dm(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects=[_subject()],
        item_data_by_subject={"subj-1": items},
        config=cfg,
    )
    assert dm.AGE == 55


# ── AE derivation ───────────────────────────────────────────────────────


def test_derive_ae_translates_ctcae_grade_to_sdtm_aesev() -> None:
    mapper = BuiltinPythonMapper()
    aes = [_ae(severity_grade=g, subject=_subject(subject_code=f"S-{g}")) for g in (1, 3, 5)]
    rows = mapper.derive_ae(deployment_id="dep-1", study_id="RS-1", adverse_events=aes)
    sev = sorted(r.AESEV for r in rows)
    assert sev == ["FATAL", "MILD", "SEVERE"]


def test_derive_ae_assigns_per_subject_stable_aeseq() -> None:
    """Two AEs for the same subject get AESEQ=1 and AESEQ=2."""
    mapper = BuiltinPythonMapper()
    s = _subject(id="subj-1", subject_code="S-001")
    aes = [
        _ae(subject=s, reported_at=datetime(2026, 1, 1, tzinfo=UTC)),
        _ae(subject=s, reported_at=datetime(2026, 1, 5, tzinfo=UTC)),
    ]
    rows = mapper.derive_ae(deployment_id="dep-1", study_id="RS-1", adverse_events=aes)
    aeseqs = sorted(r.AESEQ for r in rows)
    assert aeseqs == [1, 2]


def test_derive_ae_serious_flag_to_yn() -> None:
    mapper = BuiltinPythonMapper()
    serious, non_serious = mapper.derive_ae(
        deployment_id="dep-1",
        study_id="RS-1",
        adverse_events=[
            _ae(is_serious=True, subject=_subject(subject_code="S-1")),
            _ae(is_serious=False, subject=_subject(id="subj-2", subject_code="S-2")),
        ],
    )
    assert {r.AESER for r in [serious, non_serious]} == {"Y", "N"}


def test_derive_ae_outcome_maps_to_sdtm_controlled_term() -> None:
    mapper = BuiltinPythonMapper()
    [r] = mapper.derive_ae(
        deployment_id="dep-1",
        study_id="RS-1",
        adverse_events=[_ae(outcome="death")],
    )
    assert r.AEOUT == "FATAL"


# ── VS derivation ───────────────────────────────────────────────────────


def test_derive_vs_filters_non_vital_sign_items() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject(subject_code="S-001")
    items = [
        _item(item_id="sbp", value="120"),
        _item(item_id="dbp", value="80"),
        _item(item_id="age", value="42"),  # NOT a vital sign
        _item(item_id="favourite_colour", value="blue"),
    ]
    rows = mapper.derive_vs(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects=[subj],
        item_data_by_subject={subj.id: items},
        config=ItemMappingConfig(),
    )
    # Only sbp + dbp made it through
    test_codes = {r.VSTESTCD for r in rows}
    assert test_codes == {"SYSBP", "DIABP"}


def test_derive_vs_emits_default_units_and_iso_dates() -> None:
    mapper = BuiltinPythonMapper()
    [vs] = mapper.derive_vs(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects=[_subject(subject_code="S-1")],
        item_data_by_subject={
            "subj-1": [
                _item(
                    item_id="weight",
                    value="78.5",
                    entered_at=datetime(2026, 1, 2, 9, 0, tzinfo=UTC),
                )
            ]
        },
        config=ItemMappingConfig(),
    )
    assert vs.VSTESTCD == "WEIGHT"
    assert vs.VSORRESU == "kg"
    assert vs.VSDTC == "2026-01-02T09:00:00"


def test_derive_vs_skips_items_with_no_value() -> None:
    mapper = BuiltinPythonMapper()
    rows = mapper.derive_vs(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects=[_subject(subject_code="S-1")],
        item_data_by_subject={
            "subj-1": [_item(item_id="sbp", value=None)],
        },
        config=ItemMappingConfig(),
    )
    assert rows == []


# ── DA (Drug Accountability) ─────────────────────────────────────────────


def _disp(**overrides: object) -> Any:
    base = dict(
        id="disp-1",
        subject_id="subj-1",
        ip_id="ip-1",
        lot_number="LOT-A",
        kit_id="KIT-0001",
        quantity_dispensed=30,
        dispensed_at=datetime(2026, 2, 1, 9, 0, tzinfo=UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _ret(**overrides: object) -> Any:
    base = dict(
        subject_id="subj-1",
        dispensation_id="disp-1",
        kit_id="KIT-0001",
        quantity_returned=10,
        returned_at=datetime(2026, 2, 15, 9, 0, tzinfo=UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_derive_da_emits_dispensed_and_returned_rows() -> None:
    mapper = BuiltinPythonMapper()
    rows = mapper.derive_da(
        deployment_id="dep-1",
        study_id="RS-1",
        dispensations=[_disp()],
        returns=[_ret()],
        subjects_by_id={"subj-1": _subject()},
        units_by_ip_id={"ip-1": "tablet"},
    )
    assert len(rows) == 2
    by_test = {r.DATESTCD: r for r in rows}
    disp = by_test["DISPAMT"]
    assert disp.DATEST == "Dispensed Amount"
    assert disp.USUBJID == "RS-1-S-001"
    assert disp.DAORRES == "30"
    assert disp.DASTRESN == 30.0
    assert disp.DAORRESU == "tablet"
    assert disp.DASTRESU == "tablet"
    assert disp.DAREFID == "KIT-0001"
    ret = by_test["RETURNED"]
    assert ret.DATEST == "Returned Amount"
    assert ret.DAORRES == "10"
    assert ret.DAREFID == "KIT-0001"
    # Return borrows the units of its parent dispensation's IP.
    assert ret.DAORRESU == "tablet"


def test_derive_da_seq_is_per_subject_ordered_by_date() -> None:
    mapper = BuiltinPythonMapper()
    # Two dispensations for one subject; the later-dated one must get DASEQ 2.
    early = _disp(id="disp-1", kit_id="KIT-0001", dispensed_at=datetime(2026, 2, 1, tzinfo=UTC))
    late = _disp(id="disp-2", kit_id="KIT-0002", dispensed_at=datetime(2026, 3, 1, tzinfo=UTC))
    rows = mapper.derive_da(
        deployment_id="dep-1",
        study_id="RS-1",
        dispensations=[late, early],  # unsorted input
        returns=[],
        subjects_by_id={"subj-1": _subject()},
        units_by_ip_id={"ip-1": "tablet"},
    )
    seq_by_kit = {r.DAREFID: r.DASEQ for r in rows}
    assert seq_by_kit["KIT-0001"] == 1
    assert seq_by_kit["KIT-0002"] == 2


def test_derive_da_return_without_known_parent_has_no_units() -> None:
    """A return whose dispensation isn't in the batch still maps (units None)."""
    mapper = BuiltinPythonMapper()
    rows = mapper.derive_da(
        deployment_id="dep-1",
        study_id="RS-1",
        dispensations=[],
        returns=[_ret(dispensation_id="missing")],
        subjects_by_id={"subj-1": _subject()},
        units_by_ip_id={"ip-1": "tablet"},
    )
    assert len(rows) == 1
    assert rows[0].DATESTCD == "RETURNED"
    assert rows[0].DAORRESU is None


def test_derive_da_empty_when_no_accountability() -> None:
    mapper = BuiltinPythonMapper()
    assert (
        mapper.derive_da(
            deployment_id="dep-1",
            study_id="RS-1",
            dispensations=[],
            returns=[],
            subjects_by_id={"subj-1": _subject()},
        )
        == []
    )


# ── SV (Subject Visits) ──────────────────────────────────────────────────


def _sched(**overrides: object) -> Any:
    base = dict(id="sched-1", visit_name="Baseline", day_offset=0)
    base.update(overrides)
    return SimpleNamespace(**base)


def _pv(**overrides: object) -> Any:
    base = dict(
        subject_id="subj-1",
        scheduled_visit_id="sched-1",
        planned_date=datetime(2026, 1, 1, tzinfo=UTC),
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 4, tzinfo=UTC),
        status="completed",
        completed_at=datetime(2026, 1, 2, 10, 0, tzinfo=UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_derive_sv_emits_completed_visits_with_visitnum() -> None:
    mapper = BuiltinPythonMapper()
    scheds = {
        "sched-1": _sched(id="sched-1", visit_name="Baseline", day_offset=0),
        "sched-2": _sched(id="sched-2", visit_name="Week 4", day_offset=28),
    }
    rows = mapper.derive_sv(
        deployment_id="dep-1",
        study_id="RS-1",
        planned_visits=[
            _pv(scheduled_visit_id="sched-1"),
            _pv(scheduled_visit_id="sched-2", completed_at=datetime(2026, 1, 29, tzinfo=UTC)),
        ],
        subjects_by_id={"subj-1": _subject()},
        scheduled_visits_by_id=scheds,
    )
    assert len(rows) == 2
    by_visit = {r.VISIT: r for r in rows}
    assert by_visit["Baseline"].VISITNUM == 1.0
    assert by_visit["Week 4"].VISITNUM == 2.0
    assert by_visit["Baseline"].USUBJID == "RS-1-S-001"
    # SVSTDTC = the actual completion date.
    assert by_visit["Baseline"].SVSTDTC.startswith("2026-01-02")


def test_derive_sv_skips_non_completed_visits() -> None:
    mapper = BuiltinPythonMapper()
    rows = mapper.derive_sv(
        deployment_id="dep-1",
        study_id="RS-1",
        planned_visits=[
            _pv(status="pending"),
            _pv(status="missed"),
            _pv(status="cancelled"),
        ],
        subjects_by_id={"subj-1": _subject()},
        scheduled_visits_by_id={"sched-1": _sched()},
    )
    assert rows == []


# ── DS (Disposition) ─────────────────────────────────────────────────────


def test_derive_ds_one_event_per_subject_mapped_from_status() -> None:
    mapper = BuiltinPythonMapper()
    subjects = [
        _subject(id="s1", subject_code="S-001", status="enrolled"),
        _subject(id="s2", subject_code="S-002", status="locked"),
    ]
    rows = mapper.derive_ds(deployment_id="dep-1", study_id="RS-1", subjects=subjects)
    by_usubjid = {r.USUBJID: r for r in rows}
    assert by_usubjid["RS-1-S-001"].DSDECOD == "ONGOING"
    assert by_usubjid["RS-1-S-002"].DSDECOD == "COMPLETED"
    assert all(r.DSCAT == "DISPOSITION EVENT" for r in rows)
    assert all(r.DSSEQ == 1 for r in rows)


def test_derive_ds_unknown_status_falls_back_to_uppercase() -> None:
    mapper = BuiltinPythonMapper()
    [row] = mapper.derive_ds(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects=[_subject(status="something_new")],
    )
    assert row.DSDECOD == "SOMETHING_NEW"
    assert row.DSTERM == "something_new"


# ── EX from dispensations (additive cascade) ─────────────────────────────


def test_derive_ex_from_dispensations() -> None:
    mapper = BuiltinPythonMapper()
    rows = mapper.derive_ex_from_dispensations(
        deployment_id="dep-1",
        study_id="RS-1",
        dispensations=[_disp(quantity_dispensed=30)],
        subjects_by_id={"subj-1": _subject()},
        drug_by_ip_id={"ip-1": ("DrugX 10 mg", "tablet")},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.DOMAIN == "EX"
    assert row.USUBJID == "RS-1-S-001"
    assert row.EXTRT == "DrugX 10 mg"
    assert row.EXDOSE == 30.0
    assert row.EXDOSU == "tablet"
    assert row.EXSEQ == 1


def test_derive_ex_from_dispensations_continues_seq_past_form_rows() -> None:
    """EXSEQ picks up after the form-based EX max for that subject."""
    mapper = BuiltinPythonMapper()
    rows = mapper.derive_ex_from_dispensations(
        deployment_id="dep-1",
        study_id="RS-1",
        dispensations=[_disp(id="d1", dispensed_at=datetime(2026, 2, 1, tzinfo=UTC))],
        subjects_by_id={"subj-1": _subject()},
        drug_by_ip_id={"ip-1": ("DrugX 10 mg", "tablet")},
        starting_seq={"RS-1-S-001": 2},  # two form-based EX rows already
    )
    assert rows[0].EXSEQ == 3
