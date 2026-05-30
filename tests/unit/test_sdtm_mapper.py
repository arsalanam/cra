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
