"""HL7 v2 ORU^R01 parser — pipe-delimited segments (P2 #6).

ORU^R01 (Observation Result Unsolicited) is the dominant clinical-lab
message format. One message carries:

  MSH — message header (separators, sender, recipient, message type)
  PID — patient identification (PID-3 = identifier list)
  OBR — observation request (OBR-7 = collection datetime, OBR-4 = test
        battery code)
  OBX — observation result, repeating (one per analyte; OBX-3 = test
        code+name, OBX-5 = value, OBX-6 = units, OBX-7 = ref range,
        OBX-8 = abnormal flag, OBX-14 = collection datetime when
        the OBX doesn't sit under an OBR)

Field separator is `|`; component separator is `^`; segment terminator
is `\\r` (some senders use `\\n` or `\\r\\n` — we accept all three).
Repetition separator is `~`; escape character is `\\`.

We don't validate against the schema; we extract the fields the
SDTM LB derivation cares about.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .common import ParsedLabBatch, ParsedLabResult

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────


def _split_segments(text: str) -> list[list[str]]:
    """Normalise CRLF / LF / CR endings → list of field-lists per segment."""
    normalised = text.replace("\r\n", "\r").replace("\n", "\r")
    raw_segs = [seg for seg in normalised.split("\r") if seg.strip()]
    return [seg.split("|") for seg in raw_segs]


def _field(segment: list[str], idx: int) -> str:
    """1-indexed field accessor (HL7 spec is 1-based; segment[0] is
    the segment id like 'OBX')."""
    if idx < 0 or idx >= len(segment):
        return ""
    return segment[idx]


def _component(field_value: str, idx: int) -> str:
    """1-indexed component accessor inside a `^`-delimited field."""
    comps = field_value.split("^")
    if idx < 0 or idx >= len(comps):
        return ""
    return comps[idx]


def _try_float(s: str | None) -> float | None:
    if s is None or not str(s).strip():
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_hl7_timestamp(ts: str | None) -> datetime | None:
    """HL7 v2 timestamp format: YYYYMMDDHHMMSS (any suffix is dropped).

    Returns None when the input can't be parsed — never raises, so a
    single malformed timestamp doesn't crash the whole ingest.
    """
    if not ts:
        return None
    digits = "".join(c for c in str(ts) if c.isdigit())
    if len(digits) < 8:
        return None
    try:
        if len(digits) >= 14:
            return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        if len(digits) >= 12:
            return datetime.strptime(digits[:12], "%Y%m%d%H%M")
        return datetime.strptime(digits[:8], "%Y%m%d")
    except ValueError:
        return None


def _parse_ref_range(rr: str) -> tuple[float | None, float | None]:
    """OBX-7 reference range — '5.0-9.0' or '5.0 - 9.0' or '<5.0' or
    '>=5.0'. Returns (low, high); unbounded sides → None."""
    if not rr or not rr.strip():
        return (None, None)
    rr = rr.strip()
    # Strip any leading "<", ">", ">=", "<=" — those are single-bound
    # ranges. We don't try to be clever about them; return that bound
    # as both low and high so SDTM ranges show up reasonably.
    if rr.startswith((">=", ">", "<=", "<")):
        # Strip the operator; what's left is the number.
        for op in (">=", "<=", ">", "<"):
            if rr.startswith(op):
                rr = rr[len(op) :].strip()
                break
        n = _try_float(rr)
        return (n, n)
    if "-" in rr:
        # Walk from the right so negative low values still split correctly
        # (e.g. "-1.0-2.0" should split as ("-1.0", "2.0"), not ("", "1.0-2.0").
        parts = rr.rsplit("-", 1)
        if len(parts) == 2:
            low = _try_float(parts[0])
            high = _try_float(parts[1])
            return (low, high)
    return (None, None)


# ── Public API ───────────────────────────────────────────────────────────


def parse(raw: bytes) -> ParsedLabBatch:
    """Parse an HL7 v2 ORU^R01 message (or batch of messages).

    Multiple MSH headers in one payload are treated as separate
    messages; OBX segments carry their parent PID's subject id.
    """
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode("replace") is total
        text = raw.decode("latin-1", errors="replace")

    segments = _split_segments(text)
    results: list[ParsedLabResult] = []
    warnings: list[str] = []

    current_subject: str | None = None
    current_obr_collected: datetime | None = None

    for seg in segments:
        if not seg:
            continue
        seg_id = seg[0].strip().upper() if seg[0] else ""
        if seg_id == "MSH":
            continue
        if seg_id == "PID":
            # PID-3 is the identifier list. Component 1 is the id value
            # (the others are check-digit + assigning authority).
            pid3 = _field(seg, 3)
            current_subject = (_component(pid3, 0) if pid3 else None) or pid3.strip() or None
            continue
        if seg_id == "OBR":
            current_obr_collected = _parse_hl7_timestamp(_field(seg, 7))
            continue
        if seg_id != "OBX":
            continue

        # OBX fields — 1-indexed in HL7 spec; our seg is also 1-indexed
        # via _field (seg[0] = "OBX" + bonus delimiter as seg[1] for
        # most senders... actually in pipe-split seg[0]='OBX', seg[1]='1'
        # for OBX-1 (set id), seg[2] = OBX-2 (value type), seg[3] = OBX-3.
        obx_3 = _field(seg, 3)
        obx_5 = _field(seg, 5)
        obx_6 = _field(seg, 6)
        obx_7 = _field(seg, 7)
        obx_8 = _field(seg, 8)
        obx_14 = _field(seg, 14)
        # OBX-3 is `code^name^code_system` (or with the LOINC
        # convention `12345-6^Hemoglobin^LN`).
        test_code = _component(obx_3, 0).strip()
        test_name = _component(obx_3, 1).strip() or None
        if not test_code:
            warnings.append(f"OBX missing test code; skipped: {seg!r}")
            continue
        # OBX-6 can be `code^name^code_system`; the first component is
        # the readable unit string when present.
        units = _component(obx_6, 0).strip() or None
        if not units and obx_6:
            units = obx_6.strip()
        # OBX-5 can be qualitative (e.g. POSITIVE) or numeric.
        v_str = obx_5.strip()
        v_num = _try_float(v_str)
        v_text = v_str if v_num is None and v_str else None
        ref_low, ref_high = _parse_ref_range(obx_7)
        abn = obx_8.strip() or None
        collected_at = _parse_hl7_timestamp(obx_14) or current_obr_collected
        result = ParsedLabResult(
            subject_code_hint=current_subject,
            test_code=test_code,
            test_name=test_name,
            value_numeric=v_num,
            value_text=v_text,
            units=units,
            ref_range_low=ref_low,
            ref_range_high=ref_high,
            abnormal_flag=abn,
            collected_at=collected_at,
            raw_segment=_segment_to_dict(seg),
        )
        results.append(result)

    return ParsedLabBatch(
        source_format="hl7v2",
        results=results,
        warnings=warnings,
    )


def _segment_to_dict(seg: list[str]) -> dict[str, Any]:
    """Snapshot of the OBX segment for audit. Index-keyed to keep the
    1-indexed HL7 field numbers obvious."""
    return {str(i): v for i, v in enumerate(seg)}


__all__ = ["parse"]
