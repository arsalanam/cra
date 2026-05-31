"""Hand-rolled SAS Transport (XPT) v5 writer."""

from __future__ import annotations

import struct

import pytest

from research_assistant.cdisc._metadata import ColumnMeta, DatasetMeta
from research_assistant.cdisc.xpt_writer import (
    XptWriterError,
    ieee_to_ibm_double,
    write_xpt,
)

# ── IBM-360 float conversion ────────────────────────────────────────────


def test_ibm_double_zero_is_eight_null_bytes() -> None:
    assert ieee_to_ibm_double(0.0) == b"\x00" * 8


def test_ibm_double_nan_and_inf_are_null_sentinels() -> None:
    assert ieee_to_ibm_double(float("nan")) == b"\x00" * 8
    assert ieee_to_ibm_double(float("inf")) == b"\x00" * 8
    assert ieee_to_ibm_double(float("-inf")) == b"\x00" * 8


def test_ibm_double_positive_one_has_correct_leading_byte() -> None:
    """For value=1.0, IBM 360 form is 0.1 × 16^1 → exponent byte = 65."""
    buf = ieee_to_ibm_double(1.0)
    assert len(buf) == 8
    assert buf[0] == 0x41  # exponent = 65 (excess-64), sign positive


def test_ibm_double_negative_one_has_sign_bit_set() -> None:
    buf = ieee_to_ibm_double(-1.0)
    assert buf[0] & 0x80 == 0x80  # sign bit set
    assert buf[0] & 0x7F == 0x41  # exponent still 65


def test_ibm_double_distinct_values_produce_distinct_bytes() -> None:
    """Three values that should round to three different IBM doubles."""
    a = ieee_to_ibm_double(3.14159)
    b = ieee_to_ibm_double(2.71828)
    c = ieee_to_ibm_double(100.0)
    assert a != b
    assert a != c
    assert b != c


# ── write_xpt — header layout ───────────────────────────────────────────


def _small_ds() -> DatasetMeta:
    return DatasetMeta(
        name="TEST",
        label="Test dataset",
        structure="One record per subject",
        purpose="Tabulation",
        klass="FINDINGS",
        key_vars=("USUBJID",),
        columns=(
            ColumnMeta("STUDYID", "CHAR", 8, "Study Identifier", mandatory=True),
            ColumnMeta("USUBJID", "CHAR", 12, "Subject ID", mandatory=True),
            ColumnMeta("AGE", "NUM", 8, "Age in years"),
        ),
    )


def test_library_header_starts_with_correct_literal() -> None:
    buf = write_xpt(_small_ds(), [])
    assert buf[:53] == b"HEADER RECORD*******LIBRARY HEADER RECORD!!!!!!!00000"


def test_library_header_is_80_bytes() -> None:
    buf = write_xpt(_small_ds(), [])
    assert len(buf) % 80 == 0  # every record / region must be 80-aligned


def test_member_and_descriptor_headers_present() -> None:
    buf = write_xpt(_small_ds(), [])
    assert b"HEADER RECORD*******MEMBER  HEADER RECORD" in buf
    assert b"HEADER RECORD*******DSCRPTR HEADER RECORD" in buf
    assert b"HEADER RECORD*******NAMESTR HEADER RECORD" in buf
    assert b"HEADER RECORD*******OBS     HEADER RECORD" in buf


def test_namestr_header_carries_variable_count() -> None:
    buf = write_xpt(_small_ds(), [])
    # Variable count is encoded as a 4-digit zero-padded number
    # following 'HEADER RECORD*******NAMESTR HEADER RECORD!!!!!!!000000'.
    marker = b"NAMESTR HEADER RECORD!!!!!!!000000"
    idx = buf.find(marker) + len(marker)
    count_str = buf[idx : idx + 4]
    assert count_str == b"0003"  # 3 columns


def test_dataset_label_appears_in_descriptor_record() -> None:
    buf = write_xpt(_small_ds(), [])
    assert b"Test dataset" in buf


def test_variable_names_in_namestr_are_uppercase_padded() -> None:
    buf = write_xpt(_small_ds(), [])
    # Namestr records start after the namestr header at offset 5*80 + 0
    # (lib header + sas meta + modified + member + descriptor a + b +
    # namestr header = 8*80 = 640).
    namestr_start = 8 * 80
    first_name = buf[namestr_start + 8 : namestr_start + 16]
    assert first_name == b"STUDYID "
    second_name = buf[namestr_start + 148 : namestr_start + 156]
    assert second_name == b"USUBJID "


def test_namestr_carries_per_column_position() -> None:
    buf = write_xpt(_small_ds(), [])
    namestr_start = 8 * 80
    # npos lives at offset 84 inside each 140-byte namestr.
    pos_studyid = struct.unpack(">i", buf[namestr_start + 84 : namestr_start + 88])[0]
    pos_usubjid = struct.unpack(">i", buf[namestr_start + 224 : namestr_start + 228])[0]
    pos_age = struct.unpack(">i", buf[namestr_start + 364 : namestr_start + 368])[0]
    assert pos_studyid == 0
    assert pos_usubjid == 8  # after CHAR(8)
    assert pos_age == 20  # after CHAR(8) + CHAR(12)


# ── write_xpt — observation records ─────────────────────────────────────


def test_observation_record_is_written_after_obs_header() -> None:
    ds = _small_ds()
    rows = [{"STUDYID": "RS-1", "USUBJID": "RS-1-S-001", "AGE": 42.0}]
    buf = write_xpt(ds, rows)
    obs_marker = b"HEADER RECORD*******OBS     HEADER RECORD"
    obs_start = buf.find(obs_marker)
    assert obs_start > 0
    # Observations begin one record (80 bytes) after the header marker.
    rec = buf[obs_start + 80 : obs_start + 80 + 28]  # 8 + 12 + 8
    assert rec[:8] == b"RS-1    "  # STUDYID padded
    assert rec[8:20] == b"RS-1-S-001  "  # USUBJID padded
    # AGE = 42.0 → IBM double; first byte should be 0x42 (exp 66) for
    # values in [16, 256).
    assert rec[20] == 0x42


def test_missing_numeric_value_is_eight_null_bytes() -> None:
    ds = _small_ds()
    rows = [{"STUDYID": "RS-1", "USUBJID": "S-001", "AGE": None}]
    buf = write_xpt(ds, rows)
    obs_start = buf.find(b"HEADER RECORD*******OBS     HEADER RECORD") + 80
    # AGE bytes (last 8 of the 28-byte record)
    assert buf[obs_start + 20 : obs_start + 28] == b"\x00" * 8


def test_string_value_with_non_ascii_is_replaced_not_raised() -> None:
    ds = _small_ds()
    rows = [{"STUDYID": "RS-1", "USUBJID": "Subject-π", "AGE": None}]
    buf = write_xpt(ds, rows)
    # The Greek π should have been replaced with '?'.
    assert b"Subject-?" in buf


def test_observation_region_padded_to_80_byte_boundary() -> None:
    ds = _small_ds()
    rows = [{"STUDYID": "RS-1", "USUBJID": "S-001", "AGE": 40.0}]
    buf = write_xpt(ds, rows)
    # Total file length is 80-byte aligned.
    assert len(buf) % 80 == 0


# ── write_xpt — validation ──────────────────────────────────────────────


def test_column_name_longer_than_8_rejected() -> None:
    ds = DatasetMeta(
        name="X",
        label="x",
        structure="One per subject",
        purpose="Tabulation",
        columns=(ColumnMeta("VERYLONGNAME", "CHAR", 8, ""),),
    )
    with pytest.raises(XptWriterError, match="8-char limit"):
        write_xpt(ds, [])


def test_column_length_over_200_rejected() -> None:
    ds = DatasetMeta(
        name="X",
        label="x",
        structure="One per subject",
        purpose="Tabulation",
        columns=(ColumnMeta("TOOWIDE", "CHAR", 500, ""),),
    )
    with pytest.raises(XptWriterError, match="v5 limit"):
        write_xpt(ds, [])


def test_zero_rows_still_produces_valid_aligned_bytes() -> None:
    buf = write_xpt(_small_ds(), [])
    assert len(buf) > 0
    assert len(buf) % 80 == 0
