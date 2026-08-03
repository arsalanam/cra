"""Unit tests for the ODM-XML export of an eCRF form definition."""

from __future__ import annotations

from defusedxml import ElementTree as ET

from research_assistant.domain.ecrf import (
    CodeList,
    CodeListItem,
    FormDefinition,
    Item,
    Section,
)
from research_assistant.ecrf import form_to_odm_xml


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _form() -> FormDefinition:
    return FormDefinition(
        name="demographics",
        title="Demographics",
        code_lists=[
            CodeList(
                id="cl_sex",
                name="Sex",
                items=[
                    CodeListItem(code="M", label="Male"),
                    CodeListItem(code="F", label="Female"),
                ],
            )
        ],
        sections=[
            Section(
                id="main",
                title="Main",
                items=[
                    Item(id="age", label="Age", data_type="integer", required=True),
                    Item(id="sex", label="Sex", data_type="single_select", code_list_ref="cl_sex"),
                ],
            )
        ],
    )


def test_odm_export_structure() -> None:
    xml = form_to_odm_xml(_form(), study_name="SGLT2 Trial", version=2, protocol_id="PROTO-1")

    assert xml.startswith("<?xml")
    assert 'xmlns="http://www.cdisc.org/ns/odm/v1.3"' in xml

    root = ET.fromstring(xml)
    assert _local(root.tag) == "ODM"
    assert root.attrib["ODMVersion"] == "1.3.2"

    tags = [_local(e.tag) for e in root.iter()]
    assert tags.count("FormDef") == 1
    assert tags.count("ItemGroupDef") == 1
    assert tags.count("ItemDef") == 2
    assert tags.count("CodeList") == 1
    assert tags.count("CodeListItem") == 2

    # DataType mapping: integer -> integer, single_select -> text.
    item_defs = {
        e.attrib["OID"]: e.attrib["DataType"] for e in root.iter() if _local(e.tag) == "ItemDef"
    }
    assert item_defs["IT.demographics.age"] == "integer"
    assert item_defs["IT.demographics.sex"] == "text"

    # The select item references its code list.
    assert "CL.demographics.cl_sex" in xml
    assert 'CodedValue="M"' in xml
    # Localised question text uses xml:lang.
    assert "xml:lang" in xml
