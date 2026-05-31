"""HL7 FHIR R4 DiagnosticReport+Observation parser (P2 #6).

Walks a `Bundle` of resources, extracting `Observation` entries (whether
they appear standalone or referenced from a `DiagnosticReport`).
Per-Observation field mapping:

  subject.reference     → subject_code_hint (the trailing "/<id>" segment)
  code.coding[0].code   → test_code
  code.coding[0].display → test_name (falls back to code.text)
  valueQuantity.value   → value_numeric
  valueQuantity.unit    → units
  valueString           → value_text (for qualitative results)
  referenceRange[0].low.value  → ref_range_low
  referenceRange[0].high.value → ref_range_high
  interpretation[0].coding[0].code → abnormal_flag
  effectiveDateTime     → collected_at
  specimen.reference    → specimen_id

Stdlib-only: just walks `json.loads(...)`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from .common import ParsedLabBatch, ParsedLabResult

logger = logging.getLogger(__name__)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    # FHIR uses ISO-8601; Python's fromisoformat handles the common
    # shapes. Drop trailing "Z" → "+00:00" for older Python compat.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _ref_trailing_id(ref: Any) -> str | None:
    """Extract the trailing id from a FHIR reference string.

    `Patient/12345` → `12345`. `urn:uuid:abc-def` → `abc-def`.
    """
    if not isinstance(ref, str):
        return None
    if not ref:
        return None
    parts = ref.rsplit("/", 1)
    if len(parts) == 2 and parts[1]:
        return parts[1]
    if ref.startswith("urn:uuid:"):
        return ref[len("urn:uuid:") :]
    return ref


def _ref_range_bounds(rr: Any) -> tuple[float | None, float | None]:
    """First entry of an Observation.referenceRange list → (low, high)."""
    if not isinstance(rr, list) or not rr:
        return (None, None)
    first = rr[0]
    if not isinstance(first, dict):
        return (None, None)
    low = None
    high = None
    low_field = first.get("low")
    high_field = first.get("high")
    if isinstance(low_field, dict):
        try:
            v = low_field.get("value")
            low = float(v) if v is not None else None
        except (TypeError, ValueError):
            low = None
    if isinstance(high_field, dict):
        try:
            v = high_field.get("value")
            high = float(v) if v is not None else None
        except (TypeError, ValueError):
            high = None
    return (low, high)


def _interpretation_code(interp: Any) -> str | None:
    """interpretation[0].coding[0].code if present."""
    if not isinstance(interp, list) or not interp:
        return None
    first = interp[0]
    if not isinstance(first, dict):
        return None
    codings = first.get("coding")
    if isinstance(codings, list) and codings:
        c = codings[0]
        if isinstance(c, dict):
            code = c.get("code")
            if isinstance(code, str) and code.strip():
                return code.strip()
    text = first.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def _observation_to_parsed(obs: dict[str, Any]) -> ParsedLabResult | None:
    subject_ref = obs.get("subject", {})
    if isinstance(subject_ref, dict):
        subj_id = _ref_trailing_id(subject_ref.get("reference"))
    else:
        subj_id = None

    code = obs.get("code", {})
    test_code = ""
    test_name: str | None = None
    if isinstance(code, dict):
        coding = code.get("coding")
        if isinstance(coding, list) and coding:
            first_code = coding[0]
            if isinstance(first_code, dict):
                test_code = str(first_code.get("code") or "").strip()
                disp = first_code.get("display")
                if isinstance(disp, str) and disp.strip():
                    test_name = disp.strip()
        if not test_name:
            txt = code.get("text")
            if isinstance(txt, str) and txt.strip():
                test_name = txt.strip()
    if not test_code:
        return None

    value_numeric: float | None = None
    value_text: str | None = None
    units: str | None = None
    vq = obs.get("valueQuantity")
    if isinstance(vq, dict):
        v = vq.get("value")
        try:
            value_numeric = float(v) if v is not None else None
        except (TypeError, ValueError):
            value_numeric = None
        unit = vq.get("unit") or vq.get("code")
        if isinstance(unit, str) and unit.strip():
            units = unit.strip()
    vs = obs.get("valueString")
    if isinstance(vs, str) and vs.strip():
        value_text = vs.strip()
    vc = obs.get("valueCodeableConcept")
    if isinstance(vc, dict) and value_text is None:
        txt = vc.get("text")
        if isinstance(txt, str) and txt.strip():
            value_text = txt.strip()

    ref_low, ref_high = _ref_range_bounds(obs.get("referenceRange"))
    abnormal = _interpretation_code(obs.get("interpretation"))
    collected = _parse_iso(obs.get("effectiveDateTime"))
    if collected is None:
        ep = obs.get("effectivePeriod")
        if isinstance(ep, dict):
            collected = _parse_iso(ep.get("start"))
    specimen_ref = obs.get("specimen")
    specimen_id: str | None = None
    if isinstance(specimen_ref, dict):
        specimen_id = _ref_trailing_id(specimen_ref.get("reference"))

    return ParsedLabResult(
        subject_code_hint=subj_id,
        test_code=test_code,
        test_name=test_name,
        value_numeric=value_numeric,
        value_text=value_text,
        units=units,
        ref_range_low=ref_low,
        ref_range_high=ref_high,
        abnormal_flag=abnormal,
        specimen_id=specimen_id,
        collected_at=collected,
        raw_segment=obs,
    )


def parse(raw: bytes) -> ParsedLabBatch:
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (TypeError, ValueError, json.JSONDecodeError) as e:
        return ParsedLabBatch(
            source_format="fhir",
            results=[],
            warnings=[f"FHIR payload is not valid JSON: {e}"],
        )

    results: list[ParsedLabResult] = []
    warnings: list[str] = []

    resources: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if payload.get("resourceType") == "Bundle":
            entries = payload.get("entry")
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict):
                        res = entry.get("resource")
                        if isinstance(res, dict):
                            resources.append(res)
        else:
            resources.append(payload)
    elif isinstance(payload, list):
        # Some senders ship a bare list of Observations.
        for r in payload:
            if isinstance(r, dict):
                resources.append(r)
    else:
        warnings.append("FHIR payload was neither Bundle nor Resource dict; ignored")

    for r in resources:
        if r.get("resourceType") != "Observation":
            continue
        parsed = _observation_to_parsed(r)
        if parsed is None:
            warnings.append("Observation missing test code; skipped")
            continue
        results.append(parsed)

    return ParsedLabBatch(source_format="fhir", results=results, warnings=warnings)


__all__ = ["parse"]
