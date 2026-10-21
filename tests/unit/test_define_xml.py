"""Define-XML v2.1 generator."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from defusedxml import ElementTree as DET

from research_assistant.cdisc.define_xml import build_define_xml

_NS_ODM = "http://www.cdisc.org/ns/odm/v1.3"
_NS_DEF = "http://www.cdisc.org/ns/def/v2.1"


def _doc() -> ET.Element:
    return DET.fromstring(build_define_xml(study_id="RS-1"))


def test_xml_is_well_formed() -> None:
    bytes_out = build_define_xml(study_id="RS-1")
    assert bytes_out.startswith(b"<?xml")
    DET.fromstring(bytes_out)  # raises if not well-formed


def test_root_is_odm_element_with_correct_namespaces() -> None:
    root = _doc()
    assert root.tag == f"{{{_NS_ODM}}}ODM"
    assert root.get("ODMVersion") == "1.3.2"
    assert root.get("FileType") == "Snapshot"


def test_metadata_version_carries_define_version() -> None:
    root = _doc()
    mdv = root.find(f".//{{{_NS_ODM}}}MetaDataVersion")
    assert mdv is not None
    assert mdv.get(f"{{{_NS_DEF}}}DefineVersion") == "2.1.0"


def test_standards_block_lists_sdtm_and_adam() -> None:
    root = _doc()
    standards = root.findall(f".//{{{_NS_DEF}}}Standard")
    publishing_sets = {s.get("PublishingSet") for s in standards}
    assert publishing_sets == {"SDTM", "ADaM"}


def test_one_itemgroupdef_per_dataset() -> None:
    root = _doc()
    igs = root.findall(f".//{{{_NS_ODM}}}ItemGroupDef")
    names = {ig.get("Name") for ig in igs}
    expected = {
        "DM",
        "AE",
        "VS",
        "LB",
        "EX",
        "CM",
        "MH",
        "DA",
        "SV",
        "DS",
        "ADSL",
        "ADAE",
        "ADCM",
        "ADLB",
        "ADVS",
        "ADTTE",
    }
    assert names == expected


def test_dm_itemgroupdef_has_key_variables() -> None:
    root = _doc()
    dm = root.find(f".//{{{_NS_ODM}}}ItemGroupDef[@Name='DM']")
    assert dm is not None
    item_refs = dm.findall(f"./{{{_NS_ODM}}}ItemRef")
    key_seqs = {ref.get("KeySequence") for ref in item_refs if ref.get("KeySequence")}
    assert "1" in key_seqs  # STUDYID
    assert "2" in key_seqs  # USUBJID


def test_itemdef_count_matches_total_columns() -> None:
    """Every column in the metadata registry should have an ItemDef."""
    from research_assistant.cdisc._metadata import DATASETS

    expected_cols = sum(len(ds.columns) for ds in DATASETS)
    root = _doc()
    item_defs = root.findall(f".//{{{_NS_ODM}}}ItemDef")
    assert len(item_defs) == expected_cols


def test_codelist_for_sex_present() -> None:
    root = _doc()
    sex = root.find(f".//{{{_NS_ODM}}}CodeList[@OID='CL.SEX']")
    assert sex is not None
    coded_values = {item.get("CodedValue") for item in sex.findall(f"./{{{_NS_ODM}}}CodeListItem")}
    # dm_sex.json maps to F / M / U / I per CDISC controlled terms.
    assert "F" in coded_values
    assert "M" in coded_values


def test_codelist_for_ny_carries_yes_and_no() -> None:
    root = _doc()
    ny = root.find(f".//{{{_NS_ODM}}}CodeList[@OID='CL.NY']")
    assert ny is not None
    coded_values = {item.get("CodedValue") for item in ny.findall(f"./{{{_NS_ODM}}}CodeListItem")}
    assert coded_values == {"Y", "N"}


def test_codelist_for_paramcd_adtte_has_three_params() -> None:
    root = _doc()
    pc = root.find(f".//{{{_NS_ODM}}}CodeList[@OID='CL.PARAMCD.ADTTE']")
    assert pc is not None
    coded_values = {item.get("CodedValue") for item in pc.findall(f"./{{{_NS_ODM}}}CodeListItem")}
    assert coded_values == {"TTAE", "TTSAE", "DEATH"}


def test_codelist_for_cnsr_is_integer() -> None:
    root = _doc()
    cnsr = root.find(f".//{{{_NS_ODM}}}CodeList[@OID='CL.CNSR']")
    assert cnsr is not None
    assert cnsr.get("DataType") == "integer"


def test_methoddef_covers_derived_columns() -> None:
    root = _doc()
    method_oids = {md.get("OID") for md in root.findall(f".//{{{_NS_ODM}}}MethodDef")}
    expected = {
        "M.AGEGR1",
        "M.LBNRIND",
        "M.MHONGO",
        "M.SAFFL",
        "M.DTHFL",
        "M.ADTTE.AVAL",
        "M.ADTTE.CNSR",
    }
    assert expected.issubset(method_oids)


def test_lbnrind_itemdef_references_method() -> None:
    root = _doc()
    item = root.find(f".//{{{_NS_ODM}}}ItemDef[@OID='IT.LB.LBNRIND']")
    assert item is not None
    method_ref = item.find(f"./{{{_NS_ODM}}}MethodOID")
    assert method_ref is not None
    assert method_ref.get("MethodOID") == "M.LBNRIND"


def test_sex_itemdef_references_codelist() -> None:
    root = _doc()
    item = root.find(f".//{{{_NS_ODM}}}ItemDef[@OID='IT.DM.SEX']")
    assert item is not None
    cl_ref = item.find(f"./{{{_NS_ODM}}}CodeListRef")
    assert cl_ref is not None
    assert cl_ref.get("CodeListOID") == "CL.SEX"


def test_age_itemdef_has_float_datatype_and_length_8() -> None:
    root = _doc()
    item = root.find(f".//{{{_NS_ODM}}}ItemDef[@OID='IT.DM.AGE']")
    assert item is not None
    assert item.get("DataType") == "float"
    assert item.get("Length") == "8"


def test_global_variables_carry_study_id_and_name() -> None:
    root = _doc()
    study_name_el = root.find(f".//{{{_NS_ODM}}}StudyName")
    assert study_name_el is not None
    assert study_name_el.text == "RS-1"
    proto = root.find(f".//{{{_NS_ODM}}}ProtocolName")
    assert proto is not None
    assert proto.text == "RS-1"
