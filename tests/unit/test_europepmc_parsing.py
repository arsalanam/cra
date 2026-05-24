"""Unit tests for the Europe PMC record parser."""

from __future__ import annotations

from research_assistant.tools.clinical.sources.europepmc import (
    _parse_europepmc_record,
)


def test_parse_medline_indexed_record() -> None:
    rec = {
        "id": "12345678",
        "source": "MED",
        "pmid": "12345678",
        "doi": "10.1000/example",
        "title": "  Title with whitespace  ",
        "authorString": "Smith J, Doe A, Brown B",
        "journalTitle": "Test Journal",
        "pubYear": "2024",
        "abstractText": "Abstract body.",
        "pubTypeList": {
            "pubType": ["Journal Article", "Randomized Controlled Trial"],
        },
        "meshHeadingList": {
            "meshHeading": [
                {"descriptorName": "Heart Failure"},
                {"descriptorName": "Aspirin"},
            ],
        },
    }
    parsed = _parse_europepmc_record(rec)
    assert parsed["source"] == "europepmc"
    assert parsed["source_id"] == "12345678"
    assert parsed["pmid"] == "12345678"
    assert parsed["doi"] == "10.1000/example"
    assert parsed["title"] == "Title with whitespace"
    assert parsed["journal"] == "Test Journal"
    assert parsed["year"] == 2024
    assert parsed["authors"] == ["Smith J", "Doe A", "Brown B"]
    assert parsed["abstract"] == "Abstract body."
    assert parsed["publication_types"] == ["Journal Article", "Randomized Controlled Trial"]
    assert parsed["mesh_headings"] == ["Heart Failure", "Aspirin"]


def test_parse_non_medline_record_no_pmid() -> None:
    """Preprints/non-Medline records still parse — pmid stays None."""
    rec = {
        "id": "PPR12345",
        "source": "PPR",
        "doi": "10.1000/preprint",
        "title": "Preprint title",
        "authorString": "Anon X",
        "journalTitle": None,
        "pubYear": "2024",
        "abstractText": None,
    }
    parsed = _parse_europepmc_record(rec)
    assert parsed["source"] == "europepmc"
    assert parsed["source_id"] == "PPR12345"
    assert parsed["pmid"] is None
    assert parsed["doi"] == "10.1000/preprint"
    assert parsed["title"] == "Preprint title"
    assert parsed["journal"] is None
    assert parsed["abstract"] is None
    assert parsed["publication_types"] == []
    assert parsed["mesh_headings"] == []


def test_parse_singleton_pub_type_string() -> None:
    """Europe PMC sometimes returns a string instead of a list — handle both."""
    rec = {
        "id": "999",
        "pubTypeList": {"pubType": "Editorial"},
    }
    parsed = _parse_europepmc_record(rec)
    assert parsed["publication_types"] == ["Editorial"]


def test_parse_singleton_mesh_dict() -> None:
    """Single MeSH heading may come as a bare dict, not a list."""
    rec = {
        "id": "888",
        "meshHeadingList": {"meshHeading": {"descriptorName": "Diabetes"}},
    }
    parsed = _parse_europepmc_record(rec)
    assert parsed["mesh_headings"] == ["Diabetes"]


def test_parse_invalid_year_falls_back_to_none() -> None:
    rec = {"id": "1", "pubYear": "not-a-year"}
    parsed = _parse_europepmc_record(rec)
    assert parsed["year"] is None


def test_authors_capped_at_ten() -> None:
    rec = {
        "id": "2",
        "authorString": ", ".join(f"Author {i}" for i in range(15)),
    }
    parsed = _parse_europepmc_record(rec)
    assert len(parsed["authors"]) == 10
    assert parsed["authors"][0] == "Author 0"
    assert parsed["authors"][9] == "Author 9"


def test_empty_strings_become_none() -> None:
    rec = {"id": "3", "doi": "", "abstractText": "   ", "journalTitle": ""}
    parsed = _parse_europepmc_record(rec)
    assert parsed["doi"] is None
    assert parsed["abstract"] is None
    assert parsed["journal"] is None
