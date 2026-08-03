"""Bundle ZIP now ships XPT files + define.xml alongside CSV."""

from __future__ import annotations

import io
import zipfile

from defusedxml import ElementTree as ET

from research_assistant.cdisc.exporter import build_submission_bundle


def _payload() -> bytes:
    return build_submission_bundle(
        study_id="RS-1",
        triggered_at=None,
        dm=[],
        ae=[],
        vs=[],
        adsl=[],
        tlfs=[],
    )


def test_bundle_contains_define_xml_at_root() -> None:
    z = zipfile.ZipFile(io.BytesIO(_payload()))
    assert "define.xml" in z.namelist()


def test_define_xml_is_well_formed_and_carries_study_id() -> None:
    z = zipfile.ZipFile(io.BytesIO(_payload()))
    define_bytes = z.read("define.xml")
    root = ET.fromstring(define_bytes)
    # Study name is the study_id we passed.
    study_name = root.find(".//{http://www.cdisc.org/ns/odm/v1.3}StudyName")
    assert study_name is not None
    assert study_name.text == "RS-1"


def test_bundle_includes_define_overview_txt_for_human_inspection() -> None:
    """The plain-text manifest stays alongside define.xml."""
    z = zipfile.ZipFile(io.BytesIO(_payload()))
    assert "define-overview.txt" in z.namelist()


def test_bundle_contains_xpt_for_every_sdtm_domain_plus_adam() -> None:
    z = zipfile.ZipFile(io.BytesIO(_payload()))
    names = set(z.namelist())
    expected_xpt = {
        "sdtm/dm.xpt",
        "sdtm/ae.xpt",
        "sdtm/vs.xpt",
        "sdtm/lb.xpt",
        "sdtm/ex.xpt",
        "sdtm/cm.xpt",
        "sdtm/mh.xpt",
        "adam/adsl.xpt",
        "adam/adtte.xpt",
    }
    missing = expected_xpt - names
    assert not missing, f"Missing XPT files: {missing}"


def test_bundle_still_contains_csvs_alongside_xpt() -> None:
    z = zipfile.ZipFile(io.BytesIO(_payload()))
    names = set(z.namelist())
    expected_csv = {
        "sdtm/dm.csv",
        "sdtm/ae.csv",
        "adam/adsl.csv",
        "adam/adtte.csv",
    }
    assert expected_csv.issubset(names)


def test_xpt_files_have_v5_library_header() -> None:
    z = zipfile.ZipFile(io.BytesIO(_payload()))
    dm_xpt = z.read("sdtm/dm.xpt")
    assert dm_xpt.startswith(b"HEADER RECORD*******LIBRARY HEADER RECORD")


def test_manifest_text_mentions_define_xml_and_xpt() -> None:
    z = zipfile.ZipFile(io.BytesIO(_payload()))
    manifest = z.read("define-overview.txt").decode("utf-8")
    assert "define.xml" in manifest
    assert "XPT" in manifest or "xpt" in manifest
