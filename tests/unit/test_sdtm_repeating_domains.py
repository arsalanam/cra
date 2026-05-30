"""SDTM mapper — LB / EX / CM / MH derivation (CDISC2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from research_assistant.cdisc.sdtm_mapper import (
    BuiltinPythonMapper,
    ItemMappingConfig,
)


def _subject(id: str = "subj-1", code: str = "S-001") -> Any:
    return SimpleNamespace(
        id=id,
        deployment_id="dep-1",
        site_id="site-1",
        subject_code=code,
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def _fi(
    subject_id: str = "subj-1",
    created_at: datetime = datetime(2026, 1, 2, 9, 0, tzinfo=UTC),
) -> Any:
    return SimpleNamespace(
        id=f"fi-{id(object())}",
        subject_id=subject_id,
        deployed_form_id="df-1",
        created_at=created_at,
    )


def _item(item_id: str, value: str) -> Any:
    return SimpleNamespace(
        item_id=item_id,
        value=value,
        entered_at=datetime(2026, 1, 2, 9, 0, tzinfo=UTC),
        form_instance_id="fi-1",
    )


# ── LB ─────────────────────────────────────────────────────────────────


def test_lb_emits_one_row_per_form_instance() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (
            _fi(),
            [
                _item("test", "hgb"),
                _item("result", "14.5"),
                _item("unit", "g/dL"),
            ],
        ),
        (
            _fi(),
            [
                _item("test", "glucose"),
                _item("result", "92"),
                _item("unit", "mg/dL"),
            ],
        ),
    ]
    rows = mapper.derive_lb(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert len(rows) == 2
    codes = {r.LBTESTCD for r in rows}
    assert codes == {"HGB", "GLUC"}
    assert all(r.USUBJID == "RS-1-S-001" for r in rows)
    assert {r.LBSEQ for r in rows} == {1, 2}


def test_lb_normal_high_low_flags_derive_from_ref_range() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        # Normal hemoglobin
        (_fi(), [_item("test", "hgb"), _item("result", "14.0")]),
        # Low hemoglobin (below 12.0 default low)
        (_fi(), [_item("test", "hgb"), _item("result", "8.5")]),
        # High glucose (above 99.0 default high)
        (_fi(), [_item("test", "glucose"), _item("result", "180")]),
    ]
    rows = mapper.derive_lb(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    flags = [r.LBNRIND for r in rows]
    assert flags == ["NORMAL", "LOW", "HIGH"]


def test_lb_falls_back_to_form_created_at_for_lbdtc() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    fi_dt = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    instances = [
        (
            _fi(created_at=fi_dt),
            [_item("test", "hgb"), _item("result", "14")],
        ),
    ]
    [row] = mapper.derive_lb(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert row.LBDTC == "2026-03-15T10:00:00"


def test_lb_skips_form_instance_without_test_or_result() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (_fi(), [_item("result", "14")]),  # no test
        (_fi(), [_item("test", "hgb")]),    # no result
    ]
    rows = mapper.derive_lb(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert rows == []


def test_lb_explicit_ref_range_wins_over_default() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (
            _fi(),
            [
                _item("test", "hgb"),
                _item("result", "14.0"),
                # Tight reference range that flips the result to HIGH
                _item("ref_low", "10.0"),
                _item("ref_high", "13.0"),
            ],
        ),
    ]
    [row] = mapper.derive_lb(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert row.LBNRIND == "HIGH"
    assert row.LBSTNRLO == 10.0
    assert row.LBSTNRHI == 13.0


# ── EX ─────────────────────────────────────────────────────────────────


def test_ex_route_normalises_to_sdtm_term() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (
            _fi(),
            [
                _item("treatment", "Aspirin 81 mg"),
                _item("dose", "81"),
                _item("dose_unit", "mg"),
                _item("route", "oral"),
                _item("start_date", "2026-02-01"),
                _item("end_date", "2026-04-01"),
            ],
        ),
    ]
    [row] = mapper.derive_ex(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert row.EXTRT == "Aspirin 81 mg"
    assert row.EXDOSE == 81.0
    assert row.EXDOSU == "mg"
    assert row.EXROUTE == "ORAL"
    assert row.EXSTDTC == "2026-02-01T00:00:00"


def test_ex_skips_form_instance_without_treatment() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [(_fi(), [_item("dose", "81")])]
    rows = mapper.derive_ex(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert rows == []


def test_ex_unknown_route_passes_through_verbatim() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (
            _fi(),
            [_item("treatment", "X"), _item("route", "intratympanic")],
        ),
    ]
    [row] = mapper.derive_ex(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert row.EXROUTE == "intratympanic"


# ── CM ─────────────────────────────────────────────────────────────────


def test_cm_carries_indication_and_dose() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (
            _fi(),
            [
                _item("medication", "Lisinopril"),
                _item("indication", "Hypertension"),
                _item("dose", "10"),
                _item("dose_unit", "mg"),
                _item("start_date", "2025-08-15"),
            ],
        ),
    ]
    [row] = mapper.derive_cm(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert row.CMTRT == "Lisinopril"
    assert row.CMINDC == "Hypertension"
    assert row.CMDOSE == 10.0
    assert row.CMDOSU == "mg"
    assert row.CMSEQ == 1


def test_cm_per_subject_sequencing() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (_fi(), [_item("medication", "Lisinopril")]),
        (_fi(), [_item("medication", "Atorvastatin")]),
        (_fi(), [_item("medication", "Metformin")]),
    ]
    rows = mapper.derive_cm(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert sorted(r.CMSEQ for r in rows) == [1, 2, 3]


# ── MH ─────────────────────────────────────────────────────────────────


def test_mh_ongoing_derived_y_when_end_date_missing() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (
            _fi(),
            [
                _item("condition", "Type 2 Diabetes"),
                _item("category", "endocrine"),
                _item("onset_date", "2020-01-01"),
            ],
        ),
    ]
    [row] = mapper.derive_mh(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert row.MHTERM == "Type 2 Diabetes"
    assert row.MHCAT == "ENDOCRINE"  # canonicalised via mh_categories.json
    assert row.MHONGO == "Y"
    assert row.MHENDTC is None


def test_mh_ongoing_n_when_end_date_present() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (
            _fi(),
            [
                _item("condition", "Pneumonia"),
                _item("onset_date", "2025-12-01"),
                _item("resolved_date", "2025-12-21"),
            ],
        ),
    ]
    [row] = mapper.derive_mh(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert row.MHONGO == "N"
    assert row.MHENDTC == "2025-12-21T00:00:00"


def test_mh_unknown_category_passes_through() -> None:
    mapper = BuiltinPythonMapper()
    subj = _subject()
    instances = [
        (
            _fi(),
            [
                _item("condition", "Cluster headache"),
                _item("category", "rare-disorder"),
            ],
        ),
    ]
    [row] = mapper.derive_mh(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=ItemMappingConfig(),
    )
    assert row.MHCAT == "rare-disorder"


# ── Cross-domain: ItemMappingConfig overrides ──────────────────────────


def test_custom_item_map_overrides_defaults() -> None:
    """A deployment using non-default form item ids passes a custom map."""
    mapper = BuiltinPythonMapper()
    subj = _subject()
    cfg = ItemMappingConfig(
        lb_item_map={
            "__test": "lab_code",
            "__result": "lab_value",
            "__unit": "lab_unit",
            "__date": "collected_at",
            "__nrlo": "ref_low",
            "__nrhi": "ref_high",
        }
    )
    instances = [
        (
            _fi(),
            [
                _item("lab_code", "alt"),
                _item("lab_value", "75"),
                _item("lab_unit", "U/L"),
            ],
        )
    ]
    [row] = mapper.derive_lb(
        deployment_id="dep-1",
        study_id="RS-1",
        subjects_by_id={subj.id: subj},
        form_instances=instances,
        config=cfg,
    )
    assert row.LBTESTCD == "ALT"
    assert row.LBORRES == "75"
    assert row.LBNRIND == "HIGH"  # 75 > 56 (default high for ALT)
