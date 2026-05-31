"""SAS Transport (XPT) v5 writer — hand-rolled, no external dependency.

Implements the subset of the v5 spec the platform needs to ship CDISC-
shaped datasets to a regulator's preflight tool:

  • ASCII strings only (CHAR fields, fixed-width, blank-padded).
  • IBM-360 8-byte float numerics (the XPT mandate — NOT IEEE 754).
  • 80-byte record boundary alignment (Library / Member / Namestr /
    Observation header records + observation padding to a multiple of 80).
  • SAS v5 namestr layout (140 bytes per variable).

Bound to the 8-character column name rule by the SDTM IG; columns
beyond that length get rejected so the regulator's auto-validator
won't fail downstream.

Reference: FDA TS-140 "SAS Transport File Format" + SAS Institute's
v5 namestr specification (NAMESTR record layout, p. 27-31 of the
public TS-140 doc).
"""

from __future__ import annotations

import datetime
import io
import struct
from collections.abc import Iterable, Sequence
from typing import Any

from ._metadata import ColumnMeta, DatasetMeta

# ── IBM-360 float conversion ────────────────────────────────────────────


def ieee_to_ibm_double(value: float) -> bytes:
    """Convert an IEEE-754 double to the IBM-360 8-byte representation.

    XPT v5 mandates IBM mainframe floating point. The format:
      bit 0:      sign (1 = negative)
      bits 1–7:   exponent in excess-64, base 16
      bits 8–63:  fraction (mantissa) in 4-bit hex digits

    Special values:
      • NaN / Inf       → 8 bytes of 0x00 (XPT's "missing" sentinel)
      • Exact 0         → 8 bytes of 0x00
    """
    if value == 0 or value != value or value in (float("inf"), float("-inf")):
        return b"\x00" * 8

    sign = 0x80 if value < 0 else 0x00
    value = abs(value)

    # Extract IEEE-754 fields.
    (ieee,) = struct.unpack(">Q", struct.pack(">d", value))
    ieee_exp = (ieee >> 52) & 0x7FF
    ieee_frac = ieee & 0x000F_FFFF_FFFF_FFFF

    # Convert IEEE-754 normalised form (1.frac × 2^(exp-1023)) to IBM
    # form (.fraction × 16^(ibm_exp-64)).
    if ieee_exp == 0:
        # Subnormal — treat as zero for the regulator-grade use case.
        return b"\x00" * 8

    # IEEE binary exponent.
    bin_exp = ieee_exp - 1023
    # Re-anchor to hex (base 16) exponent: 16^k = 2^(4k), so each
    # base-16 exponent step is 4 binary exponent steps.
    ibm_exp = (bin_exp >> 2) + 65
    shift = 3 - (bin_exp & 0b11)

    # Reconstruct the 53-bit mantissa with the implicit leading 1.
    mantissa = (1 << 52) | ieee_frac
    # Re-base to a 56-bit fraction (since the IBM fraction holds 14
    # hex digits = 56 bits) accounting for the leading-digit shift.
    mantissa = mantissa << (shift + 3) if shift + 3 >= 0 else mantissa >> (-(shift + 3))
    # Trim to 56 bits.
    mantissa &= (1 << 56) - 1

    if ibm_exp < 0 or ibm_exp > 127:
        # Out of representable range — emit a missing.
        return b"\x00" * 8

    leading = sign | ibm_exp
    body: bytes = mantissa.to_bytes(7, "big")
    return bytes([leading]) + body


# ── Helpers for padded ASCII strings ────────────────────────────────────


def _ascii(value: object) -> bytes:
    """Encode to ASCII; replace non-ASCII chars with '?' so the writer
    never raises on stray UTF-8 from captured free text."""
    s = str(value)
    return s.encode("ascii", errors="replace")


def _pad_ascii(value: object, length: int) -> bytes:
    """Right-pad with spaces to the column's declared length, truncate
    if longer."""
    raw = _ascii(value)
    if len(raw) >= length:
        return raw[:length]
    return raw + b" " * (length - len(raw))


def _pad_to_80(buf: bytes) -> bytes:
    """Trail-pad to the next 80-byte boundary."""
    rem = len(buf) % 80
    if rem == 0:
        return buf
    return buf + b" " * (80 - rem)


# ── Header records ──────────────────────────────────────────────────────


def _now_v5() -> str:
    """SAS v5 datetime format: DDMMMYY:HH:MM:SS (16 chars)."""
    now = datetime.datetime.now(datetime.UTC)
    months = [
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEC",
    ]
    date_part = f"{now.day:02d}{months[now.month - 1]}{now.year % 100:02d}"
    return f"{date_part}:{now.hour:02d}:{now.minute:02d}:{now.second:02d}"


def _library_header() -> bytes:
    """First 80-byte record: the library marker.

    The literal below is fixed by the spec.
    """
    return b"HEADER RECORD*******LIBRARY HEADER RECORD!!!!!!!000000000000000000000000000000  "


def _sas_metadata(timestamp: str) -> bytes:
    """Second 80-byte record: SAS version + OS + created timestamp.

    Fields are space-padded:
      • 'SAS'      8 chars  — system identifier
      • 'SAS'      8 chars  — institution name
      • 'SASLIB'   8 chars  — package
      • '9.4'      8 chars  — version
      • OS         8 chars  — operating system
      • blank      24 chars
      • created    16 chars (DDMMMYY:HH:MM:SS)
    """
    parts = b"SAS     " + b"SAS     " + b"SASLIB  " + b"9.4     " + b"PYTHON  " + b" " * 24
    return parts + timestamp.encode("ascii")


def _modified_record(timestamp: str) -> bytes:
    """Third 80-byte record: modification timestamp + 64 spaces."""
    return timestamp.encode("ascii") + b" " * 64


def _member_header() -> bytes:
    """Fourth 80-byte record: member header literal."""
    return b"HEADER RECORD*******MEMBER  HEADER RECORD!!!!!!!000000000000000001600000000140  "


def _descriptor_header() -> bytes:
    """Fifth 80-byte record: descriptor header literal."""
    return b"HEADER RECORD*******DSCRPTR HEADER RECORD!!!!!!!000000000000000000000000000000  "


def _descriptor_metadata(dataset_name: str, dataset_label: str, timestamp: str) -> bytes:
    """Two 80-byte records describing the dataset.

    Record A (80 bytes — descriptor):
      • chars 1-8:    'SAS'
      • chars 9-16:   'SAS'
      • chars 17-24:  dataset name (8-char max, blank-padded)
      • chars 25-32:  'SASDATA'
      • chars 33-40:  version ('9.4     ')
      • chars 41-48:  OS ('PYTHON  ')
      • chars 49-64:  16 blanks
      • chars 65-80:  created (DDMMMYY:HH:MM:SS, 16 chars)

    Record B (80 bytes — labels):
      • chars 1-16:   modified date
      • chars 17-32:  16 blanks
      • chars 33-72:  dataset label (40-char max, blank-padded)
      • chars 73-80:  dataset type (8 chars, blank)
    """
    name_field = _pad_ascii(dataset_name.upper(), 8)
    label_field = _pad_ascii(dataset_label, 40)
    record_a = (
        b"SAS     "
        + b"SAS     "
        + name_field
        + b"SASDATA "
        + b"9.4     "
        + b"PYTHON  "
        + b" " * 16
        + timestamp.encode("ascii")
    )
    record_b = timestamp.encode("ascii") + b" " * 16 + label_field + b" " * 8
    return record_a + record_b


# ── Namestr records (140 bytes per variable in v5) ──────────────────────


def _namestr(col: ColumnMeta, position: int, var_number: int) -> bytes:
    """Single namestr record for one column.

    Field layout (offsets are within the 140-byte record):
        0   ntype     short  1=numeric, 2=character
        2   nhfun     short  hash of name (unused — set to 0)
        4   nlng      short  field length
        6   nvar0     short  variable number (1-based)
        8   nname     char8  variable name (UPPERCASE, blank-padded)
       16   nlabel    char40 label
       56   nform     char8  format (blank for our use case)
       64   nfl       short  format length
       66   nfd       short  format decimals
       68   nfj       short  justification (0=left, 1=right)
       70   nfill     char2  blank
       72   niform    char8  informat (blank)
       80   nifl      short  informat length
       82   nifd      short  informat decimals
       84   npos      long   position in observation record
       88   …         52 bytes padding
    """
    ntype = 1 if col.type == "NUM" else 2
    field_length = 8 if col.type == "NUM" else col.length
    name = _pad_ascii(col.name.upper(), 8)
    label = _pad_ascii(col.label, 40)
    buf = bytearray(140)
    struct.pack_into(">hhhh", buf, 0, ntype, 0, field_length, var_number)
    buf[8:16] = name
    buf[16:56] = label
    buf[56:64] = b" " * 8  # nform
    struct.pack_into(">hhh", buf, 64, 0, 0, 0)
    buf[70:72] = b"  "  # nfill
    buf[72:80] = b" " * 8  # niform
    struct.pack_into(">hh", buf, 80, 0, 0)
    struct.pack_into(">i", buf, 84, position)
    # bytes 88-139 left as 0x00 — namestr padding.
    return bytes(buf)


def _namestr_header(n_vars: int) -> bytes:
    """Namestr header record — embeds the variable count for readers.

    Literal: ``HEADER RECORD*******NAMESTR HEADER RECORD!!!!!!!000000xxxx000000  ``
    where `xxxx` is the 4-digit variable count.
    """
    n_str = f"{n_vars:04d}".encode("ascii")
    return (
        b"HEADER RECORD*******NAMESTR HEADER RECORD!!!!!!!000000"
        + n_str
        + b"00000000000000000000  "
    )


def _observation_header() -> bytes:
    return b"HEADER RECORD*******OBS     HEADER RECORD!!!!!!!000000000000000000000000000000  "


# ── Public API ──────────────────────────────────────────────────────────


class XptWriterError(ValueError):
    """Raised when column metadata or row data violates XPT v5 constraints."""


def _validate_columns(columns: Sequence[ColumnMeta]) -> None:
    for col in columns:
        if not col.name:
            raise XptWriterError("Column has empty name.")
        if len(col.name) > 8:
            raise XptWriterError(f"Column {col.name!r} exceeds the 8-char limit of SAS v5 XPT.")
        if not col.name.replace("_", "").isalnum():
            raise XptWriterError(f"Column {col.name!r} contains non-alphanumeric characters.")
        if col.type not in ("CHAR", "NUM"):
            raise XptWriterError(f"Column {col.name!r} has unsupported type {col.type!r}.")
        if col.type == "CHAR" and col.length < 1:
            raise XptWriterError(f"Column {col.name!r} declares CHAR length < 1.")
        if col.length > 200:
            raise XptWriterError(f"Column {col.name!r} length {col.length} > 200 — v5 limit.")


def _row_value(row: Any, name: str) -> Any:
    """Pull a value from an ORM row or dict — supports both shapes so
    tests can pass dicts without instantiating models."""
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _encode_row(columns: Sequence[ColumnMeta], row: Any) -> bytes:
    out: list[bytes] = []
    for col in columns:
        value = _row_value(row, col.name)
        if col.type == "NUM":
            if value is None or value == "":
                out.append(b"\x00" * 8)
            else:
                try:
                    out.append(ieee_to_ibm_double(float(value)))
                except (TypeError, ValueError):
                    out.append(b"\x00" * 8)
        else:
            if value is None:
                out.append(b" " * col.length)
            else:
                out.append(_pad_ascii(value, col.length))
    return b"".join(out)


def write_xpt(
    dataset: DatasetMeta,
    rows: Iterable[Any],
    *,
    timestamp: str | None = None,
) -> bytes:
    """Serialise rows to v5 XPT bytes. `rows` items may be dicts or
    ORM instances — both shapes are accepted."""
    _validate_columns(dataset.columns)
    ts = timestamp or _now_v5()

    # Compute observation-record stride + per-column positions for the
    # namestr records.
    positions: list[int] = []
    pos = 0
    for col in dataset.columns:
        positions.append(pos)
        pos += 8 if col.type == "NUM" else col.length

    # Build the library + member preamble.
    buf = io.BytesIO()
    buf.write(_library_header())
    buf.write(_sas_metadata(ts))
    buf.write(_modified_record(ts))
    buf.write(_member_header())
    buf.write(_descriptor_header())
    buf.write(_descriptor_metadata(dataset.name, dataset.label, ts))
    buf.write(_namestr_header(len(dataset.columns)))

    namestr_bytes = b"".join(
        _namestr(col, positions[i], i + 1) for i, col in enumerate(dataset.columns)
    )
    buf.write(_pad_to_80(namestr_bytes))

    buf.write(_observation_header())

    encoded_rows = b"".join(_encode_row(dataset.columns, r) for r in rows)
    buf.write(_pad_to_80(encoded_rows) if encoded_rows else b"")
    return buf.getvalue()


__all__ = [
    "XptWriterError",
    "ieee_to_ibm_double",
    "write_xpt",
]
