"""Define-XML v2.1 generator.

Builds a CDISC Define-XML v2.1 document describing every dataset in
the submission bundle. The schema (ODM 1.3 with define-2-1 extension)
is documented in CDISC's "Define-XML v2.1 Standard for SDTM, ADaM, and
SEND" (2019).

Coverage:
  • One <ItemGroupDef> per dataset (all 9 in the bundle), with key
    variables and class.
  • One <ItemDef> per column with type, length, label, and an
    optional <CodeListRef> or <MethodOID>.
  • <CodeList>s for every controlled-terminology field captured in
    `cdisc/terminology/*.json` + a few hard-coded ones (NY,
    PARAMCD.ADTTE, AVALU, CNSR).
  • <MethodDef>s for derived columns (LBNRIND, MHONGO, AGEGR1,
    SAFFL, DTHFL, ADTTE AVAL + CNSR).

XML is generated with `xml.etree.ElementTree` so the output is
deterministic + well-formed without an external dep. Namespaces are
declared on the root element.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

from . import _metadata
from ._metadata import DATASETS, ColumnMeta, DatasetMeta
from .terminology import load as _load_terminology

# ── Namespaces ──────────────────────────────────────────────────────────


_NS_ODM = "http://www.cdisc.org/ns/odm/v1.3"
_NS_DEF = "http://www.cdisc.org/ns/def/v2.1"
_NS_XLINK = "http://www.w3.org/1999/xlink"


def _q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ── Codelist registry ───────────────────────────────────────────────────


def _codelist_from_terminology(
    json_filename: str, value_key: str = "value"
) -> list[tuple[str, str]]:
    """Read a terminology JSON file and return (coded, decoded) tuples.

    For lookups whose mapping is `key → decoded string`, both fields
    are the same (the CT JSONs are platform-internal lookups, not
    CDISC's external codelists — we surface them as CodedValues for
    the Define-XML reader's reference).
    """
    payload = _load_terminology(json_filename)
    mapping: dict[str, Any] = payload.get("mapping", {})
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for k, v in mapping.items():
        coded = str(v) if isinstance(v, str) else str(v.get(value_key, k))
        if coded in seen:
            continue
        seen.add(coded)
        out.append((coded, coded))
    return sorted(out)


def _codelist_from_test_codes(json_filename: str, code_field: str) -> list[tuple[str, str]]:
    """For VS/LB test-code JSONs whose mapping values are nested dicts."""
    payload = _load_terminology(json_filename)
    mapping: dict[str, Any] = payload.get("mapping", {})
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for v in mapping.values():
        if not isinstance(v, dict):
            continue
        code = v.get(code_field)
        if not code or code in seen:
            continue
        seen.add(code)
        # Use the next field as the decoded name when present.
        decoded = v.get(code_field.replace("CD", "")) or code
        out.append((str(code), str(decoded)))
    return sorted(out)


def _codelists() -> dict[str, tuple[str, str, list[tuple[str, str]]]]:
    """Return ``oid → (name, data_type, list[(coded, decoded)])``.

    Data type is "text" for character codelists, "integer" for CNSR
    (the only numeric one).
    """
    cls: dict[str, tuple[str, str, list[tuple[str, str]]]] = {}
    cls[_metadata.CL_SEX] = ("Sex", "text", _codelist_from_terminology("dm_sex.json"))
    cls[_metadata.CL_RACE] = ("Race", "text", _codelist_from_terminology("dm_race.json"))
    cls[_metadata.CL_AESEV] = (
        "AE Severity",
        "text",
        _codelist_from_terminology("ae_severity.json"),
    )
    cls[_metadata.CL_AEOUT] = (
        "AE Outcome",
        "text",
        _codelist_from_terminology("ae_outcome.json"),
    )
    cls[_metadata.CL_AEREL] = (
        "AE Relationship",
        "text",
        _codelist_from_terminology("ae_relationship.json"),
    )
    cls[_metadata.CL_VSTESTCD] = (
        "VS Test Code",
        "text",
        _codelist_from_test_codes("vs_test_codes.json", "VSTESTCD"),
    )
    cls[_metadata.CL_LBTESTCD] = (
        "LB Test Code",
        "text",
        _codelist_from_test_codes("lb_test_codes.json", "LBTESTCD"),
    )
    cls[_metadata.CL_EXROUTE] = (
        "EX Route",
        "text",
        _codelist_from_terminology("ex_routes.json"),
    )
    cls[_metadata.CL_MHCAT] = (
        "MH Category",
        "text",
        _codelist_from_terminology("mh_categories.json"),
    )
    cls[_metadata.CL_NY] = ("Yes/No", "text", [("Y", "Yes"), ("N", "No")])
    cls[_metadata.CL_AVALU] = (
        "Analysis Value Unit",
        "text",
        [("DAYS", "Days"), ("MONTHS", "Months"), ("YEARS", "Years")],
    )
    cls[_metadata.CL_CNSR] = (
        "ADTTE Censor",
        "integer",
        [("0", "Event observed"), ("1", "Censored")],
    )
    cls[_metadata.CL_PARAMCD_ADTTE] = (
        "ADTTE Parameter Code",
        "text",
        [
            ("TTAE", "Time to First AE"),
            ("TTSAE", "Time to First Serious AE"),
            ("DEATH", "Overall Survival (Time to Death)"),
        ],
    )
    return cls


# ── Method registry ─────────────────────────────────────────────────────


_METHODS: dict[str, tuple[str, str, str]] = {
    _metadata.M_AGEGR1: (
        "Age Group 1",
        "Computation",
        "AGEGR1 = '<18' if AGE<18 else '18-64' if AGE<65 else '65-74' if AGE<75 else '>=75'.",
    ),
    _metadata.M_LBNRIND: (
        "Reference Range Indicator",
        "Computation",
        (
            "LBNRIND = 'LOW' if LBSTRESN < LBSTNRLO, 'HIGH' if LBSTRESN > LBSTNRHI, "
            "else 'NORMAL'. Blank when LBSTRESN or reference limits are missing."
        ),
    ),
    _metadata.M_MHONGO: (
        "Ongoing Event Flag",
        "Computation",
        "MHONGO = 'Y' when MHENDTC is missing; 'N' otherwise.",
    ),
    _metadata.M_SAFFL: (
        "Safety Population Flag",
        "Computation",
        (
            "SAFFL = 'Y' iff subject has any captured post-baseline ItemData; "
            "'N' otherwise. Heuristic stand-in for first-dose-event criterion "
            "until the dosing event is captured natively."
        ),
    ),
    _metadata.M_DTHFL: (
        "Death Flag",
        "Computation",
        "DTHFL = 'Y' iff any SDTM AE row for the subject has AEOUT='FATAL'.",
    ),
    _metadata.M_ADTTE_AVAL: (
        "Time-to-Event Analysis Value",
        "Computation",
        (
            "AVAL = number of days from DM.RFSTDTC to the event date "
            "(when CNSR=0) or the last follow-up date (when CNSR=1). "
            "Negative intervals are clamped to 0."
        ),
    ),
    _metadata.M_ADTTE_CNSR: (
        "Censoring Flag",
        "Computation",
        (
            "CNSR = 0 when a qualifying event exists for the PARAMCD "
            "(earliest AE, earliest SAE, or earliest FATAL AE); CNSR = 1 "
            "and ADT = last-follow-up otherwise."
        ),
    ),
}


# ── XML construction ────────────────────────────────────────────────────


def _add_global_variables(parent: ET.Element, study_id: str, study_name: str) -> None:
    gv = ET.SubElement(parent, "GlobalVariables")
    name_el = ET.SubElement(gv, "StudyName")
    name_el.text = study_name
    desc_el = ET.SubElement(gv, "StudyDescription")
    desc_el.text = f"Submission bundle for study {study_id}."
    proto_el = ET.SubElement(gv, "ProtocolName")
    proto_el.text = study_id


def _add_standards(parent: ET.Element) -> None:
    standards = ET.SubElement(parent, _q(_NS_DEF, "Standards"))
    sdtm = ET.SubElement(
        standards,
        _q(_NS_DEF, "Standard"),
        attrib={
            "OID": "STD.SDTMIG.3.4",
            "Name": "SDTMIG",
            "Type": "IG",
            "Version": "3.4",
            "Status": "Final",
            "PublishingSet": "SDTM",
        },
    )
    del sdtm  # only built for the side effect of attaching it
    adam = ET.SubElement(
        standards,
        _q(_NS_DEF, "Standard"),
        attrib={
            "OID": "STD.ADAMIG.1.2",
            "Name": "ADaMIG",
            "Type": "IG",
            "Version": "1.2",
            "Status": "Final",
            "PublishingSet": "ADaM",
        },
    )
    del adam


def _item_oid(dataset: str, column: str) -> str:
    return f"IT.{dataset}.{column}"


def _add_item_group(parent: ET.Element, dataset: DatasetMeta) -> None:
    is_adam = dataset.purpose == "Analysis"
    attribs = {
        "OID": f"IG.{dataset.name}",
        "Name": dataset.name,
        "Repeating": "Yes" if dataset.structure.startswith("One record per") else "No",
        "IsReferenceData": "No",
        "Purpose": dataset.purpose,
        _q(_NS_DEF, "Structure"): dataset.structure,
        _q(_NS_DEF, "Class"): dataset.klass or ("ADaM" if is_adam else "TABULATION"),
        _q(_NS_DEF, "ArchiveLocationID"): f"LF.{dataset.name}",
    }
    if dataset.key_vars:
        attribs["SASDatasetName"] = dataset.name
    ig = ET.SubElement(parent, "ItemGroupDef", attrib=attribs)
    desc = ET.SubElement(ig, "Description")
    trans = ET.SubElement(desc, "TranslatedText", attrib={"xml:lang": "en"})
    trans.text = dataset.label
    for i, col in enumerate(dataset.columns, start=1):
        ref_attribs = {
            "ItemOID": _item_oid(dataset.name, col.name),
            "OrderNumber": str(i),
            "Mandatory": "Yes" if col.mandatory else "No",
        }
        if col.name in dataset.key_vars:
            ref_attribs["KeySequence"] = str(dataset.key_vars.index(col.name) + 1)
        ET.SubElement(ig, "ItemRef", attrib=ref_attribs)


def _add_item_def(parent: ET.Element, dataset: DatasetMeta, col: ColumnMeta) -> None:
    attribs = {
        "OID": _item_oid(dataset.name, col.name),
        "Name": col.name,
        "DataType": "float" if col.type == "NUM" else "text",
        "Length": "8" if col.type == "NUM" else str(col.length),
        "SASFieldName": col.name,
    }
    if col.method_oid:
        attribs[_q(_NS_DEF, "OriginType")] = "Derived"
    item = ET.SubElement(parent, "ItemDef", attrib=attribs)
    desc = ET.SubElement(item, "Description")
    trans = ET.SubElement(desc, "TranslatedText", attrib={"xml:lang": "en"})
    trans.text = col.label
    if col.codelist_oid:
        ET.SubElement(item, "CodeListRef", attrib={"CodeListOID": col.codelist_oid})
    if col.method_oid:
        ET.SubElement(item, "MethodOID", attrib={"MethodOID": col.method_oid})


def _add_codelist(
    parent: ET.Element, oid: str, name: str, datatype: str, values: list[tuple[str, str]]
) -> None:
    cl = ET.SubElement(
        parent,
        "CodeList",
        attrib={"OID": oid, "Name": name, "DataType": datatype},
    )
    desc = ET.SubElement(cl, "Description")
    trans = ET.SubElement(desc, "TranslatedText", attrib={"xml:lang": "en"})
    trans.text = name
    for coded, decoded in values:
        ci = ET.SubElement(cl, "CodeListItem", attrib={"CodedValue": coded})
        dec = ET.SubElement(ci, "Decode")
        t = ET.SubElement(dec, "TranslatedText", attrib={"xml:lang": "en"})
        t.text = decoded


def _add_method_def(parent: ET.Element, oid: str, name: str, mtype: str, description: str) -> None:
    md = ET.SubElement(
        parent,
        "MethodDef",
        attrib={"OID": oid, "Name": name, "Type": mtype},
    )
    desc = ET.SubElement(md, "Description")
    trans = ET.SubElement(desc, "TranslatedText", attrib={"xml:lang": "en"})
    trans.text = description


def build_define_xml(
    *,
    study_id: str,
    study_name: str | None = None,
    datasets: tuple[DatasetMeta, ...] = DATASETS,
    timestamp: str | None = None,
) -> bytes:
    """Render the Define-XML document as UTF-8 bytes (with the XML
    declaration). Returns bytes so the bundle can include it directly."""
    ts = timestamp or _now()

    ET.register_namespace("", _NS_ODM)
    ET.register_namespace("def", _NS_DEF)
    ET.register_namespace("xlink", _NS_XLINK)

    odm = ET.Element(
        _q(_NS_ODM, "ODM"),
        attrib={
            "FileOID": f"FILE.{study_id}.{ts.replace(':', '')}",
            "FileType": "Snapshot",
            "CreationDateTime": ts,
            "ODMVersion": "1.3.2",
            "Originator": "CRA platform",
            _q(_NS_DEF, "Context"): "Submission",
        },
    )

    study = ET.SubElement(odm, "Study", attrib={"OID": f"STUDY.{study_id}"})
    _add_global_variables(study, study_id, study_name or study_id)

    mdv = ET.SubElement(
        study,
        "MetaDataVersion",
        attrib={
            "OID": "MDV.1",
            "Name": "Submission metadata",
            "Description": "Define-XML v2.1 generated by the CRA platform.",
            _q(_NS_DEF, "DefineVersion"): "2.1.0",
        },
    )
    _add_standards(mdv)

    for ds in datasets:
        _add_item_group(mdv, ds)

    # Emit ItemDefs only once per (dataset, column) pair — datasets in
    # the registry never share column OIDs, so the simple loop is safe.
    for ds in datasets:
        for col in ds.columns:
            _add_item_def(mdv, ds, col)

    for oid, (name, datatype, values) in _codelists().items():
        if values:
            _add_codelist(mdv, oid, name, datatype, values)

    for oid, (name, mtype, description) in _METHODS.items():
        _add_method_def(mdv, oid, name, mtype, description)

    ET.indent(odm, space="  ")
    xml_bytes: bytes = ET.tostring(
        odm, encoding="utf-8", xml_declaration=True, short_empty_elements=False
    )
    return xml_bytes


__all__ = ["build_define_xml"]
