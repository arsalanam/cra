"""CSL JSON + EndNote XML citation formats (round-trip + detect + interop)."""

from __future__ import annotations

import json

from research_assistant.services.citations import (
    Citation,
    detect_format,
    export_bibtex,
    export_csl_json,
    export_endnote_xml,
    parse,
    parse_bibtex,
    parse_csl_json,
    parse_endnote_xml,
)

_CSL_FIXTURE = json.dumps(
    [
        {
            "id": "smith2020",
            "type": "article-journal",
            "title": "On the foo",
            "author": [{"family": "Smith", "given": "John"}, {"family": "Doe", "given": "Jane"}],
            "issued": {"date-parts": [[2020]]},
            "container-title": "Bar Journal",
            "volume": "12",
            "issue": "3",
            "page": "45-67",
            "DOI": "10.1234/baz",
            "PMID": "12345678",
        }
    ]
)

_ENDNOTE_FIXTURE = """\
<xml><records>
<record>
  <ref-type name="Journal Article">17</ref-type>
  <contributors><authors>
    <author><style>Smith, John</style></author>
    <author><style>Doe, Jane</style></author>
  </authors></contributors>
  <titles>
    <title><style>On the foo</style></title>
    <secondary-title>Bar Journal</secondary-title>
  </titles>
  <dates><year>2020</year></dates>
  <volume>12</volume>
  <number>3</number>
  <pages>45-67</pages>
  <electronic-resource-num>10.1234/baz</electronic-resource-num>
  <accession-num>12345678</accession-num>
</record>
</records></xml>
"""


# ── CSL JSON ─────────────────────────────────────────────────────────────


def test_parse_csl_json_fields() -> None:
    [cit] = parse_csl_json(_CSL_FIXTURE)
    assert cit.entry_type == "article"
    assert cit.title == "On the foo"
    assert cit.authors == ["Smith, John", "Doe, Jane"]
    assert cit.year == 2020
    assert cit.journal == "Bar Journal"
    assert cit.pages == "45-67"
    assert cit.doi == "10.1234/baz"
    assert cit.pmid == "12345678"


def test_csl_json_round_trip() -> None:
    original = Citation(
        cite_key="jones2018",
        entry_type="book",
        title="Methods of Quux",
        authors=["Jones, A."],
        year=2018,
        publisher="Acme Press",
    )
    [back] = parse_csl_json(export_csl_json([original]))
    assert back.title == "Methods of Quux"
    assert back.entry_type == "book"
    assert back.authors == ["Jones, A."]
    assert back.year == 2018
    assert back.publisher == "Acme Press"


def test_csl_export_uses_csl_type_vocabulary() -> None:
    out = json.loads(export_csl_json([Citation(cite_key="k", entry_type="article")]))
    assert out[0]["type"] == "article-journal"


def test_detect_csl_json() -> None:
    assert detect_format(_CSL_FIXTURE) == "csl_json"


# ── EndNote XML ──────────────────────────────────────────────────────────


def test_parse_endnote_xml_fields() -> None:
    [cit] = parse_endnote_xml(_ENDNOTE_FIXTURE)
    assert cit.entry_type == "article"
    assert cit.title == "On the foo"  # unwrapped from <style>
    assert cit.authors == ["Smith, John", "Doe, Jane"]
    assert cit.year == 2020
    assert cit.journal == "Bar Journal"
    assert cit.volume == "12"
    assert cit.issue == "3"
    assert cit.pages == "45-67"
    assert cit.doi == "10.1234/baz"
    assert cit.pmid == "12345678"


def test_endnote_xml_round_trip() -> None:
    original = Citation(
        cite_key="smith2020",
        entry_type="article",
        title="On the foo",
        authors=["Smith, John"],
        year=2020,
        journal="Bar Journal",
        doi="10.1234/baz",
    )
    [back] = parse_endnote_xml(export_endnote_xml([original]))
    assert back.title == "On the foo"
    assert back.entry_type == "article"
    assert back.authors == ["Smith, John"]
    assert back.journal == "Bar Journal"
    assert back.doi == "10.1234/baz"


def test_detect_endnote_xml() -> None:
    assert detect_format(_ENDNOTE_FIXTURE) == "endnote_xml"


# ── Cross-format interop + dispatch ──────────────────────────────────────


def test_bibtex_to_csl_to_endnote_preserves_core_fields() -> None:
    [cit] = parse_bibtex("@article{k, title={T}, author={Smith, John}, year={2021}, journal={J}}")
    csl = export_csl_json([cit])
    [via_csl] = parse_csl_json(csl)
    xml = export_endnote_xml([via_csl])
    [via_en] = parse_endnote_xml(xml)
    assert via_en.title == "T"
    assert via_en.authors == ["Smith, John"]
    assert via_en.year == 2021
    assert via_en.journal == "J"


def test_parse_dispatch_auto_detects_new_formats() -> None:
    assert parse(_CSL_FIXTURE)[0].title == "On the foo"
    assert parse(_ENDNOTE_FIXTURE)[0].title == "On the foo"


def test_bibtex_still_detected_after_adding_formats() -> None:
    assert detect_format(export_bibtex([Citation(cite_key="k", title="X")])) == "bibtex"
