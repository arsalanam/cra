"""CDISC ODM-XML export for eCRF form definitions (E0).

Maps a `domain.ecrf.FormDefinition` to a minimal, valid ODM v1.3.2 metadata
document (Study / MetaDataVersion / FormDef / ItemGroupDef / ItemDef /
CodeList). Built with the stdlib ElementTree — no XML dependency.

This is the interoperability boundary (decision D1): we author/store JSON
internally and emit ODM at the edge. Define-XML (a submission-metadata
flavour of ODM) is a later addition.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from ..domain.ecrf import FormDefinition

_ODM_NS = "http://www.cdisc.org/ns/odm/v1.3"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# eCRF item type -> ODM DataType. Selects/files have no distinct ODM type;
# they're carried as text (selects additionally reference a CodeList).
_ODM_DATATYPE: dict[str, str] = {
    "text": "text",
    "integer": "integer",
    "decimal": "float",
    "date": "date",
    "datetime": "datetime",
    "boolean": "boolean",
    "single_select": "text",
    "multi_select": "text",
    "file": "text",
}


def _yn(value: bool) -> str:
    return "Yes" if value else "No"


def _translated_text(parent: ET.Element, text: str) -> None:
    tt = ET.SubElement(parent, "TranslatedText")
    tt.set(_XML_LANG, "en")
    tt.text = text


def form_to_odm_xml(
    form: FormDefinition,
    *,
    study_name: str,
    version: int = 1,
    study_oid: str | None = None,
    protocol_id: str | None = None,
) -> str:
    """Render a published/draft form definition as an ODM-XML metadata string."""
    now = datetime.now(UTC).isoformat()
    study_oid = study_oid or f"S.{form.name}"
    # OIDs are namespaced by form name to stay unique within the document.
    fp = form.name

    odm = ET.Element(
        "ODM",
        {
            "xmlns": _ODM_NS,
            "FileType": "Snapshot",
            "FileOID": f"{study_oid}.{fp}.v{version}",
            "CreationDateTime": now,
            "ODMVersion": "1.3.2",
        },
    )
    study = ET.SubElement(odm, "Study", {"OID": study_oid})
    gv = ET.SubElement(study, "GlobalVariables")
    ET.SubElement(gv, "StudyName").text = study_name
    ET.SubElement(gv, "StudyDescription").text = study_name
    ET.SubElement(gv, "ProtocolName").text = protocol_id or study_name

    mdv = ET.SubElement(
        study,
        "MetaDataVersion",
        {"OID": f"MDV.{fp}.v{version}", "Name": f"{form.title} v{version}"},
    )

    # FormDef -> references each section's ItemGroup.
    form_def = ET.SubElement(
        mdv, "FormDef", {"OID": f"F.{fp}", "Name": form.title, "Repeating": "No"}
    )
    for section in form.sections:
        ET.SubElement(
            form_def,
            "ItemGroupRef",
            {"ItemGroupOID": f"IG.{fp}.{section.id}", "Mandatory": "Yes"},
        )

    # ItemGroupDefs (sections) -> reference their items.
    for section in form.sections:
        ig = ET.SubElement(
            mdv,
            "ItemGroupDef",
            {
                "OID": f"IG.{fp}.{section.id}",
                "Name": section.title,
                "Repeating": _yn(section.repeating),
            },
        )
        for item in section.items:
            ET.SubElement(
                ig,
                "ItemRef",
                {"ItemOID": f"IT.{fp}.{item.id}", "Mandatory": _yn(item.required)},
            )

    # ItemDefs.
    for section in form.sections:
        for item in section.items:
            attrs = {
                "OID": f"IT.{fp}.{item.id}",
                "Name": item.cdash_var or item.id,
                "DataType": _ODM_DATATYPE.get(item.data_type, "text"),
            }
            if item.units:
                attrs["Unit"] = item.units
            item_def = ET.SubElement(mdv, "ItemDef", attrs)
            question = ET.SubElement(item_def, "Question")
            _translated_text(question, item.label)
            if item.code_list_ref:
                ET.SubElement(
                    item_def, "CodeListRef", {"CodeListOID": f"CL.{fp}.{item.code_list_ref}"}
                )

    # CodeLists.
    for code_list in form.code_lists:
        cl = ET.SubElement(
            mdv,
            "CodeList",
            {"OID": f"CL.{fp}.{code_list.id}", "Name": code_list.name, "DataType": "text"},
        )
        for entry in sorted(code_list.items, key=lambda e: e.ordinal):
            cli = ET.SubElement(cl, "CodeListItem", {"CodedValue": entry.code})
            decode = ET.SubElement(cli, "Decode")
            _translated_text(decode, entry.label)

    ET.indent(odm)
    return ET.tostring(odm, encoding="unicode", xml_declaration=True)
