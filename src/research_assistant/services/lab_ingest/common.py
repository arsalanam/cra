"""Shared types + dispatch for lab-data parsers (P2 #6).

`ParsedLabResult` is the source-format-agnostic shape every parser
produces. The repository (`ClinicalRepository.ingest_lab_batch`)
takes a list of these and persists `LabResult` rows verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ParsedLabResult:
    """One parsed lab observation in the canonical shape.

    Mirrors the persistence-layer `LabResult` model; the repository
    maps field-by-field. `subject_code_hint` is the raw subject
    identifier from the source message (the platform tries to match
    it to a registered Subject; a Subject row created later can
    backfill the link).
    """

    subject_code_hint: str | None
    test_code: str
    test_name: str | None = None
    value_numeric: float | None = None
    value_text: str | None = None
    units: str | None = None
    ref_range_low: float | None = None
    ref_range_high: float | None = None
    abnormal_flag: str | None = None
    specimen_id: str | None = None
    collected_at: datetime | None = None
    raw_segment: dict[str, object] = field(default_factory=dict)


@dataclass
class ParsedLabBatch:
    """Result of parsing one wire payload.

    `source_format` is the canonical id ('hl7v2' | 'cdisc_lab' |
    'fhir') used to populate `LabBatch.source_format`. `warnings` is
    a list of soft parse issues (e.g. "OBX-5 missing for row 3") —
    surfaced to the operator without rejecting the whole batch.
    """

    source_format: str
    results: list[ParsedLabResult]
    warnings: list[str] = field(default_factory=list)


def parse_lab_payload(*, source_format: str, raw: bytes) -> ParsedLabBatch:
    """Dispatch to the right parser by source_format.

    Imports live inside the function so each parser stays lazy
    (importing `lab_ingest` shouldn't pull every format if the
    operator only ever uses one).
    """
    fmt = source_format.lower().strip()
    if fmt == "hl7v2":
        from . import hl7v2

        return hl7v2.parse(raw)
    if fmt == "cdisc_lab":
        from . import cdisc_lab

        return cdisc_lab.parse(raw)
    if fmt == "fhir":
        from . import fhir

        return fhir.parse(raw)
    raise ValueError(
        f"Unsupported lab source_format {source_format!r}; expected hl7v2 | cdisc_lab | fhir."
    )


__all__ = ["ParsedLabBatch", "ParsedLabResult", "parse_lab_payload"]
