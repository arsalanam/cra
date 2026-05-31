"""Parser correctness tests for HL7 v2, CDISC LAB, and FHIR (P2 #6).

Each parser produces `ParsedLabResult` rows from a known sample
payload. Tests pin the field extraction since downstream LB
derivation depends on these fields being populated correctly.
"""

from __future__ import annotations

import json

import pytest

from research_assistant.services.lab_ingest import (
    ParsedLabBatch,
    parse_lab_payload,
)

# ── HL7 v2 ORU^R01 ───────────────────────────────────────────────────────


_HL7V2_SAMPLE = (
    b"MSH|^~\\&|LAB|HOSP|EDC|TRIAL|20260531120000||ORU^R01|MSG1|P|2.5\r"
    b"PID|||S-001||DOE^JOHN||19700101|M\r"
    b"OBR|1||ACC123|CBC^Complete Blood Count^L|||20260531110000\r"
    b"OBX|1|NM|718-7^Hemoglobin^LN||14.2|g/dL|13.0-17.0|N|||F|||20260531110500\r"
    b"OBX|2|NM|2345-7^Glucose^LN||102|mg/dL|70-100|H|||F\r"
    b"OBX|3|ST|6463-4^Urine appearance^LN||CLEAR|||N|||F\r"
)


def test_hl7v2_extracts_subject_and_each_obx() -> None:
    batch = parse_lab_payload(source_format="hl7v2", raw=_HL7V2_SAMPLE)
    assert batch.source_format == "hl7v2"
    assert len(batch.results) == 3
    # All three OBX share the parent PID's subject.
    for r in batch.results:
        assert r.subject_code_hint == "S-001"


def test_hl7v2_parses_numeric_and_qualitative_values() -> None:
    batch = parse_lab_payload(source_format="hl7v2", raw=_HL7V2_SAMPLE)
    hgb = next(r for r in batch.results if r.test_code == "718-7")
    assert hgb.test_name == "Hemoglobin"
    assert hgb.value_numeric == 14.2
    assert hgb.value_text is None
    assert hgb.units == "g/dL"
    assert hgb.ref_range_low == 13.0
    assert hgb.ref_range_high == 17.0
    assert hgb.abnormal_flag == "N"

    urine = next(r for r in batch.results if r.test_code == "6463-4")
    assert urine.value_numeric is None
    assert urine.value_text == "CLEAR"


def test_hl7v2_carries_obr_collection_datetime_to_each_obx() -> None:
    batch = parse_lab_payload(source_format="hl7v2", raw=_HL7V2_SAMPLE)
    # OBX-14 explicit on the first row → uses it.
    first = batch.results[0]
    assert first.collected_at is not None
    assert first.collected_at.year == 2026
    # OBX without its own OBX-14 → falls back to OBR-7.
    third = batch.results[2]
    assert third.collected_at is not None


def test_hl7v2_handles_lf_and_crlf_line_endings() -> None:
    lf = _HL7V2_SAMPLE.replace(b"\r", b"\n")
    crlf = _HL7V2_SAMPLE.replace(b"\r", b"\r\n")
    batch_lf = parse_lab_payload(source_format="hl7v2", raw=lf)
    batch_crlf = parse_lab_payload(source_format="hl7v2", raw=crlf)
    assert len(batch_lf.results) == 3
    assert len(batch_crlf.results) == 3


def test_hl7v2_warns_on_obx_without_test_code() -> None:
    malformed = (
        b"MSH|^~\\&|LAB|HOSP|EDC|TRIAL|20260531120000||ORU^R01|MSG2|P|2.5\r"
        b"PID|||S-002\r"
        b"OBR|1||ACC1\r"
        b"OBX|1|NM|||5.5|mmol/L\r"  # missing OBX-3
    )
    batch = parse_lab_payload(source_format="hl7v2", raw=malformed)
    assert batch.results == []
    assert any("test code" in w.lower() for w in batch.warnings)


def test_hl7v2_ref_range_with_open_bound() -> None:
    msg = (
        b"MSH|^~\\&|LAB|HOSP|EDC|TRIAL|20260531120000||ORU^R01|MSG3|P|2.5\r"
        b"PID|||S-003\r"
        b"OBR|1||ACC2||||20260531120000\r"
        b"OBX|1|NM|TSH^TSH^L||0.5|mIU/L|<2.5|N|||F\r"
        b"OBX|2|NM|CRP^CRP^L||3.2|mg/L|>=0|N|||F\r"
    )
    batch = parse_lab_payload(source_format="hl7v2", raw=msg)
    tsh = next(r for r in batch.results if r.test_code == "TSH")
    assert tsh.ref_range_low == 2.5
    assert tsh.ref_range_high == 2.5


# ── CDISC LAB tab-delimited ──────────────────────────────────────────────


_CDISC_LAB_SAMPLE = (
    b"STUDYID\tSITEID\tSUBJID\tACCSNNUM\tLBTESTCD\tLBTEST\t"
    b"LBORRES\tLBORRESU\tLBORNRLO\tLBORNRHI\tLBNRIND\tLBDTC\n"
    b"STUDY1\t01\tS-001\tA1\tHGB\tHemoglobin\t14.2\tg/dL\t13\t17\tNORMAL\t2026-05-31T11:05:00\n"
    b"STUDY1\t01\tS-001\tA1\tGLUC\tGlucose\t102\tmg/dL\t70\t100\tHIGH\t2026-05-31T11:05:00\n"
    b"STUDY1\t02\tS-002\tA2\tCREAT\tCreatinine\t0.9\tmg/dL\t0.7\t1.3\tNORMAL\t2026-05-31\n"
)


def test_cdisc_lab_parses_each_data_row() -> None:
    batch = parse_lab_payload(source_format="cdisc_lab", raw=_CDISC_LAB_SAMPLE)
    assert batch.source_format == "cdisc_lab"
    assert len(batch.results) == 3
    hgb = batch.results[0]
    assert hgb.subject_code_hint == "S-001"
    assert hgb.test_code == "HGB"
    assert hgb.test_name == "Hemoglobin"
    assert hgb.value_numeric == 14.2
    assert hgb.units == "g/dL"
    assert hgb.ref_range_low == 13.0
    assert hgb.ref_range_high == 17.0
    assert hgb.abnormal_flag == "NORMAL"
    assert hgb.specimen_id == "A1"


def test_cdisc_lab_handles_date_only_lbdtc() -> None:
    batch = parse_lab_payload(source_format="cdisc_lab", raw=_CDISC_LAB_SAMPLE)
    creat = batch.results[2]
    assert creat.collected_at is not None
    assert creat.collected_at.day == 31


def test_cdisc_lab_skips_blank_trailing_rows() -> None:
    with_blanks = _CDISC_LAB_SAMPLE + b"\n\n\n"
    batch = parse_lab_payload(source_format="cdisc_lab", raw=with_blanks)
    assert len(batch.results) == 3


def test_cdisc_lab_accepts_comma_delimited_fallback() -> None:
    """Operators sometimes export to CSV; the parser auto-detects."""
    csv_sample = _CDISC_LAB_SAMPLE.replace(b"\t", b",")
    batch = parse_lab_payload(source_format="cdisc_lab", raw=csv_sample)
    assert len(batch.results) == 3


def test_cdisc_lab_warns_on_missing_test_code() -> None:
    bad = b"STUDYID\tSUBJID\tLBTESTCD\tLBTEST\tLBORRES\nS1\tS-001\t\t\t10\n"
    batch = parse_lab_payload(source_format="cdisc_lab", raw=bad)
    assert batch.results == []
    assert any("LBTESTCD" in w for w in batch.warnings)


# ── HL7 FHIR R4 ──────────────────────────────────────────────────────────


def _fhir_bundle(observations: list[dict]) -> bytes:
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": o} for o in observations],
    }
    return json.dumps(bundle).encode("utf-8")


def test_fhir_extracts_observation_fields() -> None:
    obs = {
        "resourceType": "Observation",
        "subject": {"reference": "Patient/S-001"},
        "code": {
            "coding": [{"code": "718-7", "display": "Hemoglobin", "system": "http://loinc.org"}],
            "text": "Hemoglobin",
        },
        "valueQuantity": {"value": 14.2, "unit": "g/dL", "code": "g/dL"},
        "referenceRange": [{"low": {"value": 13.0}, "high": {"value": 17.0}}],
        "interpretation": [{"coding": [{"code": "N", "display": "Normal"}]}],
        "effectiveDateTime": "2026-05-31T11:05:00Z",
        "specimen": {"reference": "Specimen/ACC1"},
    }
    batch = parse_lab_payload(source_format="fhir", raw=_fhir_bundle([obs]))
    assert batch.source_format == "fhir"
    assert len(batch.results) == 1
    r = batch.results[0]
    assert r.subject_code_hint == "S-001"
    assert r.test_code == "718-7"
    assert r.test_name == "Hemoglobin"
    assert r.value_numeric == 14.2
    assert r.units == "g/dL"
    assert r.ref_range_low == 13.0
    assert r.ref_range_high == 17.0
    assert r.abnormal_flag == "N"
    assert r.specimen_id == "ACC1"
    assert r.collected_at is not None


def test_fhir_qualitative_via_valuestring() -> None:
    obs = {
        "resourceType": "Observation",
        "subject": {"reference": "Patient/S-002"},
        "code": {"coding": [{"code": "URN-APP", "display": "Urine appearance"}]},
        "valueString": "CLEAR",
    }
    batch = parse_lab_payload(source_format="fhir", raw=_fhir_bundle([obs]))
    assert batch.results[0].value_text == "CLEAR"
    assert batch.results[0].value_numeric is None


def test_fhir_ignores_non_observation_resources() -> None:
    other = {"resourceType": "DiagnosticReport", "id": "dr1"}
    obs = {
        "resourceType": "Observation",
        "subject": {"reference": "Patient/S-003"},
        "code": {"coding": [{"code": "GLUC"}]},
        "valueQuantity": {"value": 102, "unit": "mg/dL"},
    }
    batch = parse_lab_payload(source_format="fhir", raw=_fhir_bundle([other, obs]))
    assert len(batch.results) == 1
    assert batch.results[0].test_code == "GLUC"


def test_fhir_accepts_bare_list_of_observations() -> None:
    obs = {
        "resourceType": "Observation",
        "subject": {"reference": "Patient/S-004"},
        "code": {"coding": [{"code": "TSH"}]},
        "valueQuantity": {"value": 2.1},
    }
    batch = parse_lab_payload(source_format="fhir", raw=json.dumps([obs]).encode("utf-8"))
    assert len(batch.results) == 1


def test_fhir_handles_malformed_json_without_crashing() -> None:
    batch = parse_lab_payload(source_format="fhir", raw=b"{not valid json")
    assert batch.results == []
    assert any("valid JSON" in w for w in batch.warnings)


# ── Dispatch ─────────────────────────────────────────────────────────────


def test_parse_lab_payload_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        parse_lab_payload(source_format="x12_lab", raw=b"x")


def test_parse_lab_payload_returns_batch_shape() -> None:
    batch = parse_lab_payload(source_format="hl7v2", raw=_HL7V2_SAMPLE)
    assert isinstance(batch, ParsedLabBatch)
    assert hasattr(batch, "results")
    assert hasattr(batch, "warnings")
