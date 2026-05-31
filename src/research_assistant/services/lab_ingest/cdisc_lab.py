"""CDISC LAB tab-delimited parser (P2 #6).

CDISC LAB Model v1.x ships as a tab-delimited file with a fixed-name
header row. We extract the SDTM-relevant columns:

  SUBJID     — subject identifier (subject_code_hint)
  ACCSNNUM   — specimen / accession number (specimen_id)
  LBTEST     — test name (test_name)
  LBTESTCD   — test code (test_code; falls back to LBTEST if missing)
  LBORRES    — original result, as reported (value_text +
               value_numeric when castable)
  LBORRESU   — original units (units)
  LBORNRLO   — lower normal range (ref_range_low)
  LBORNRHI   — upper normal range (ref_range_high)
  LBNRIND    — normal range indicator (abnormal_flag)
  LBDTC      — collection date/time ISO 8601 (collected_at)

CSV / Excel-exported variants sometimes use commas instead of tabs;
we auto-detect on the header row.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any

from .common import ParsedLabBatch, ParsedLabResult

logger = logging.getLogger(__name__)


def _try_float(s: str | None) -> float | None:
    if s is None or not str(s).strip():
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_iso8601(s: str | None) -> datetime | None:
    """Parse ISO-8601-ish dates; never raises."""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    # Try a few common shapes.
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Last-ditch: Python's fromisoformat handles the common
    # "2026-05-31T10:00:00+00:00" shape.
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _detect_delimiter(header_line: str) -> str:
    """Tab beats comma when both appear (CDISC LAB is tab by spec)."""
    if "\t" in header_line:
        return "\t"
    return ","


def parse(raw: bytes) -> ParsedLabBatch:
    try:
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception:  # pragma: no cover
        text = raw.decode("latin-1", errors="replace")
    # Normalise line endings.
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ParsedLabBatch(source_format="cdisc_lab", results=[], warnings=["empty file"])
    first_newline = text.find("\n")
    header_line = text if first_newline == -1 else text[:first_newline]
    delimiter = _detect_delimiter(header_line)

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    results: list[ParsedLabResult] = []
    warnings: list[str] = []

    for row_no, row in enumerate(reader, start=1):
        # Skip blank rows produced by trailing newlines.
        if not any((v or "").strip() for v in row.values()):
            continue
        subject_hint = (row.get("SUBJID") or row.get("USUBJID") or "").strip() or None
        test_code = (row.get("LBTESTCD") or row.get("LBTEST") or "").strip()
        if not test_code:
            warnings.append(f"row {row_no}: missing LBTESTCD + LBTEST; skipped")
            continue
        test_name = (row.get("LBTEST") or test_code).strip() or None
        v_str = (row.get("LBORRES") or "").strip()
        v_num = _try_float(v_str)
        v_text = v_str if v_num is None and v_str else None
        units = (row.get("LBORRESU") or "").strip() or None
        ref_low = _try_float(row.get("LBORNRLO"))
        ref_high = _try_float(row.get("LBORNRHI"))
        abn = (row.get("LBNRIND") or "").strip() or None
        collected = _parse_iso8601(row.get("LBDTC"))
        specimen = (row.get("ACCSNNUM") or "").strip() or None
        result = ParsedLabResult(
            subject_code_hint=subject_hint,
            test_code=test_code,
            test_name=test_name,
            value_numeric=v_num,
            value_text=v_text,
            units=units,
            ref_range_low=ref_low,
            ref_range_high=ref_high,
            abnormal_flag=abn,
            specimen_id=specimen,
            collected_at=collected,
            raw_segment=_row_to_dict(row),
        )
        results.append(result)

    return ParsedLabBatch(
        source_format="cdisc_lab",
        results=results,
        warnings=warnings,
    )


def _row_to_dict(row: dict[str, str]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if v is not None}


__all__ = ["parse"]
