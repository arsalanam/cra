"""BibTeX + RIS parser/exporter tests (P1 #8)."""

from __future__ import annotations

import pytest

from research_assistant.services.citations import (
    Citation,
    detect_format,
    export_bibtex,
    export_ris,
    parse_bibtex,
    parse_ris,
)

_BIBTEX_FIXTURE = """\
@article{smith2020,
  title = {On the foo},
  author = {Smith, John and Doe, Jane},
  year = {2020},
  journal = {Bar Journal},
  volume = {12},
  number = {3},
  pages = {45--67},
  doi = {10.1234/baz},
  pmid = {12345678},
}

@book{jones2018,
  title = {Methods of Quux},
  author = {Jones, A.},
  year = {2018},
  publisher = {Acme Press},
}
"""


_RIS_FIXTURE = """\
TY  - JOUR
TI  - On the foo
AU  - Smith, John
AU  - Doe, Jane
PY  - 2020
JO  - Bar Journal
VL  - 12
IS  - 3
SP  - 45
EP  - 67
DO  - 10.1234/baz
AN  - 12345678
ER  -

TY  - BOOK
TI  - Methods of Quux
AU  - Jones, A.
PY  - 2018
PB  - Acme Press
ER  -
"""


# ── Format detection ────────────────────────────────────────────────────


def test_detect_bibtex() -> None:
    assert detect_format(_BIBTEX_FIXTURE) == "bibtex"


def test_detect_ris() -> None:
    assert detect_format(_RIS_FIXTURE) == "ris"


def test_detect_unknown_returns_none() -> None:
    assert detect_format("nothing useful here") is None


def test_detect_empty_returns_none() -> None:
    assert detect_format("") is None


# ── BibTeX parser ───────────────────────────────────────────────────────


def test_parse_bibtex_returns_two_entries() -> None:
    out = parse_bibtex(_BIBTEX_FIXTURE)
    assert len(out) == 2
    assert {c.cite_key for c in out} == {"smith2020", "jones2018"}


def test_parse_bibtex_extracts_canonical_fields() -> None:
    [first, _] = parse_bibtex(_BIBTEX_FIXTURE)
    assert first.entry_type == "article"
    assert first.title == "On the foo"
    assert first.authors == ["Smith, John", "Doe, Jane"]
    assert first.year == 2020
    assert first.journal == "Bar Journal"
    assert first.volume == "12"
    assert first.issue == "3"
    assert first.pages == "45--67"
    assert first.doi == "10.1234/baz"
    assert first.pmid == "12345678"


def test_parse_bibtex_preserves_unknown_fields_in_raw() -> None:
    src = "@article{x, title={t}, custom={cval}}"
    [c] = parse_bibtex(src)
    assert c.raw_fields.get("custom") == "cval"
    assert c.title == "t"


def test_parse_bibtex_quoted_strings_supported() -> None:
    src = '@article{x, title="quoted title", year="2021"}'
    [c] = parse_bibtex(src)
    assert c.title == "quoted title"
    assert c.year == 2021


def test_parse_bibtex_ignores_garbage() -> None:
    """Lines outside @entry{...} should be ignored, not crash."""
    src = "some comments\n@article{a, title={t}}\nmore text\n"
    out = parse_bibtex(src)
    assert len(out) == 1


# ── BibTeX exporter ─────────────────────────────────────────────────────


def test_export_bibtex_emits_required_lines() -> None:
    c = Citation(
        cite_key="x",
        entry_type="article",
        title="hello",
        authors=["A B", "C D"],
        year=2020,
        journal="J",
    )
    out = export_bibtex([c])
    assert "@article{x," in out
    assert "title = {hello}" in out
    assert "author = {A B and C D}" in out
    assert "year = {2020}" in out
    assert "journal = {J}" in out


def test_export_bibtex_drops_none_fields() -> None:
    c = Citation(cite_key="x", entry_type="misc", title="t")
    out = export_bibtex([c])
    assert "year" not in out
    assert "journal" not in out


def test_export_bibtex_escapes_braces_in_values() -> None:
    c = Citation(cite_key="x", title="weird {brace} title")
    out = export_bibtex([c])
    assert "{brace}" not in out  # opening { replaced
    assert "(brace)" in out


def test_bibtex_roundtrip_preserves_semantics() -> None:
    """parse(export(x)) yields the same headline fields as x."""
    originals = parse_bibtex(_BIBTEX_FIXTURE)
    text = export_bibtex(originals)
    rebuilt = parse_bibtex(text)
    assert len(rebuilt) == len(originals)
    for a, b in zip(rebuilt, originals, strict=True):
        assert a.cite_key == b.cite_key
        assert a.title == b.title
        assert a.authors == b.authors
        assert a.year == b.year
        assert a.journal == b.journal


# ── RIS parser ──────────────────────────────────────────────────────────


def test_parse_ris_returns_two_entries() -> None:
    out = parse_ris(_RIS_FIXTURE)
    assert len(out) == 2


def test_parse_ris_extracts_canonical_fields() -> None:
    [first, second] = parse_ris(_RIS_FIXTURE)
    assert first.entry_type == "article"  # TY=JOUR
    assert first.title == "On the foo"
    assert first.authors == ["Smith, John", "Doe, Jane"]
    assert first.year == 2020
    assert first.journal == "Bar Journal"
    assert first.volume == "12"
    assert first.issue == "3"
    assert first.pages == "45-67"
    assert first.doi == "10.1234/baz"
    assert first.pmid == "12345678"
    assert second.entry_type == "book"
    assert second.publisher == "Acme Press"


def test_parse_ris_ignores_records_without_er() -> None:
    """Records missing ER  - shouldn't be returned (partial record)."""
    src = "TY  - JOUR\nTI  - missing ER\nAU  - A B\n"
    out = parse_ris(src)
    assert out == []


# ── RIS exporter ────────────────────────────────────────────────────────


def test_export_ris_emits_ty_and_er() -> None:
    c = Citation(cite_key="x", entry_type="article", title="t", authors=["A B"], year=2021)
    out = export_ris([c])
    assert "TY  - JOUR" in out
    assert "TI  - t" in out
    assert "AU  - A B" in out
    assert "PY  - 2021" in out
    assert "ER  - " in out


def test_export_ris_emits_sp_ep_when_pages_has_dash() -> None:
    c = Citation(cite_key="x", pages="45-67")
    out = export_ris([c])
    assert "SP  - 45" in out
    assert "EP  - 67" in out


def test_export_ris_maps_book_type_to_book() -> None:
    c = Citation(cite_key="x", entry_type="book", title="t")
    assert "TY  - BOOK" in export_ris([c])


def test_ris_roundtrip_preserves_semantics() -> None:
    originals = parse_ris(_RIS_FIXTURE)
    text = export_ris(originals)
    rebuilt = parse_ris(text)
    assert len(rebuilt) == len(originals)
    for a, b in zip(rebuilt, originals, strict=True):
        assert a.title == b.title
        assert a.authors == b.authors
        assert a.year == b.year


# ── Cross-format consistency ───────────────────────────────────────────


def test_bibtex_to_ris_to_bibtex_preserves_titles() -> None:
    """Round trip: bibtex → parse → ris → parse → titles same."""
    originals = parse_bibtex(_BIBTEX_FIXTURE)
    ris_text = export_ris(originals)
    back = parse_ris(ris_text)
    assert {c.title for c in back} == {c.title for c in originals}


def test_format_detection_consistent_with_exports() -> None:
    """Output of export_bibtex sniffs as bibtex; export_ris sniffs as ris."""
    cit = Citation(cite_key="x", title="t", authors=["A B"], year=2020)
    assert detect_format(export_bibtex([cit])) == "bibtex"
    assert detect_format(export_ris([cit])) == "ris"


def test_parse_dispatch_via_auto_detect() -> None:
    """`parse(text)` should pick the right parser without an explicit fmt."""
    from research_assistant.services.citations import parse

    bib = parse(_BIBTEX_FIXTURE)
    ris = parse(_RIS_FIXTURE)
    assert len(bib) == 2
    assert len(ris) == 2


def test_parse_unknown_format_raises() -> None:
    from research_assistant.services.citations import parse

    with pytest.raises(ValueError, match="Could not detect"):
        parse("garbage", fmt=None)
