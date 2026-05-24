"""Unit tests for cross-source study dedupe."""

from __future__ import annotations

from research_assistant.tools.clinical.sources.normalize import (
    _study_keys,
    dedupe_studies,
)


def _study(
    source: str,
    source_id: str,
    *,
    pmid: str | None = None,
    doi: str | None = None,
    title: str = "Test",
) -> dict:
    return {
        "source": source,
        "source_id": source_id,
        "pmid": pmid,
        "doi": doi,
        "title": title,
    }


def test_keeps_first_on_pmid_match() -> None:
    studies = [
        _study("pubmed", "111", pmid="111", title="PubMed copy"),
        _study("europepmc", "111", pmid="111", title="Europe PMC copy"),
    ]
    out = dedupe_studies(studies)
    assert len(out) == 1
    assert out[0]["title"] == "PubMed copy"


def test_keeps_first_on_doi_match() -> None:
    studies = [
        _study("pubmed", "111", doi="10.1/abc", title="PubMed"),
        _study("europepmc", "PPR1", doi="10.1/ABC", title="EPMC preprint"),
    ]
    out = dedupe_studies(studies)
    assert len(out) == 1
    assert out[0]["title"] == "PubMed"


def test_doi_match_is_case_insensitive() -> None:
    studies = [
        _study("pubmed", "1", doi="10.1/X"),
        _study("europepmc", "2", doi="10.1/x"),
    ]
    assert len(dedupe_studies(studies)) == 1


def test_falls_back_to_source_id_when_no_pmid_or_doi() -> None:
    studies = [
        _study("europepmc", "PPR1"),
        _study("europepmc", "PPR1"),
        _study("europepmc", "PPR2"),
    ]
    out = dedupe_studies(studies)
    assert len(out) == 2
    assert {s["source_id"] for s in out} == {"PPR1", "PPR2"}


def test_distinct_records_kept() -> None:
    studies = [
        _study("pubmed", "111", pmid="111"),
        _study("pubmed", "222", pmid="222"),
        _study("europepmc", "333", pmid="333"),
    ]
    assert len(dedupe_studies(studies)) == 3


def test_pmid_match_wins_over_distinct_source_ids() -> None:
    """Same PMID across sources is one paper, even with different source_ids."""
    studies = [
        _study("pubmed", "111", pmid="111"),
        _study("europepmc", "EPMC-A", pmid="111"),
    ]
    assert len(dedupe_studies(studies)) == 1


def test_study_keys_priority() -> None:
    s = {"source": "pubmed", "source_id": "111", "pmid": "111", "doi": "10.1/x"}
    keys = _study_keys(s)
    assert keys[0] == "pmid:111"
    assert keys[1] == "doi:10.1/x"
    assert keys[2] == "src:pubmed:111"


def test_empty_pmid_string_treated_as_missing() -> None:
    """Empty-string pmid shouldn't accidentally match other empty-string pmids."""
    studies = [
        _study("europepmc", "A", pmid=""),
        _study("europepmc", "B", pmid=""),
    ]
    assert len(dedupe_studies(studies)) == 2
