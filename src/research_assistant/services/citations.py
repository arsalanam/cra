"""Citation-manager round-trip — BibTeX + RIS parsers and exporters (P1 #8).

Pure stdlib (re, dataclasses). Covers Zotero / EndNote / Mendeley
import + export via two file formats every citation manager supports:

  • BibTeX (.bib) — academic-standard tagged format. We parse and emit
    a conservative subset: ``@type{cite_key, field = {value}, ...}``.
  • RIS (.ris) — line-oriented ``TAG  - value`` format. We parse and
    emit per RFC-like spec used by Endnote and Mendeley.

Both round-trip through the `Citation` dataclass below. The intent is
NOT to be a full reference-manager — it's to let an operator drag in
their existing library and have the assistant pull references mid-draft
(via the `import_citations` tool registered on manuscript / sr_protocol
specialists), or export the assistant-built reference list back out for
the manager.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

CitationFormat = Literal["bibtex", "ris"]


_BIBTEX_TYPE_BY_RIS_TY: dict[str, str] = {
    "JOUR": "article",
    "BOOK": "book",
    "CHAP": "incollection",
    "CONF": "inproceedings",
    "RPRT": "techreport",
    "THES": "phdthesis",
    "ELEC": "misc",
    "GEN": "misc",
}

_RIS_TY_BY_BIBTEX_TYPE: dict[str, str] = {
    "article": "JOUR",
    "book": "BOOK",
    "incollection": "CHAP",
    "inproceedings": "CONF",
    "techreport": "RPRT",
    "phdthesis": "THES",
    "misc": "GEN",
}


@dataclass
class Citation:
    """Canonical citation. `entry_type` mirrors the BibTeX taxonomy
    (article, book, inproceedings, …); the RIS exporter maps it back via
    `_RIS_TY_BY_BIBTEX_TYPE`."""

    cite_key: str
    entry_type: str = "article"
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    doi: str | None = None
    pmid: str | None = None
    url: str | None = None
    abstract: str | None = None
    raw_fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "cite_key": self.cite_key,
            "entry_type": self.entry_type,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "journal": self.journal,
            "volume": self.volume,
            "issue": self.issue,
            "pages": self.pages,
            "publisher": self.publisher,
            "doi": self.doi,
            "pmid": self.pmid,
            "url": self.url,
            "abstract": self.abstract,
            "raw_fields": dict(self.raw_fields),
        }


def detect_format(text: str) -> CitationFormat | None:
    """Sniff BibTeX vs RIS from the first few non-empty lines."""
    head = "\n".join(text.splitlines()[:20]).strip()
    if not head:
        return None
    if "@" in head and re.search(r"@\w+\s*[{(]", head):
        return "bibtex"
    if re.search(r"^[A-Z][A-Z0-9]\s*-\s*", head, re.MULTILINE):
        return "ris"
    return None


# ── BibTeX parser ──────────────────────────────────────────────────────


_BIBTEX_ENTRY_HEAD = re.compile(r"@(?P<type>\w+)\s*[{(]\s*(?P<key>[^,\s]+)\s*,", re.IGNORECASE)


def _split_bibtex_entries(text: str) -> list[tuple[str, str, str]]:
    """Return [(entry_type, cite_key, body)] — body is between the
    opening `{` and the matching closing `}` (depth-aware to tolerate
    braces inside field values)."""
    entries: list[tuple[str, str, str]] = []
    i = 0
    while True:
        m = _BIBTEX_ENTRY_HEAD.search(text, i)
        if m is None:
            break
        depth = 1
        j = m.end()
        body_start = j
        while j < len(text) and depth > 0:
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[body_start:j]
        entries.append((m.group("type").lower(), m.group("key"), body))
        i = j + 1
    return entries


_BIBTEX_FIELD = re.compile(
    r"\s*(?P<key>\w+)\s*=\s*(?P<value>\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,]+)\s*,?",
)


def _strip_bibtex_braces(value: str) -> str:
    v = value.strip()
    if v.endswith(","):
        v = v[:-1].rstrip()
    if (v.startswith("{") and v.endswith("}")) or (
        v.startswith('"') and v.endswith('"')
    ):
        v = v[1:-1]
    return v.strip()


def _bibtex_authors(raw: str) -> list[str]:
    return [a.strip() for a in re.split(r"\s+and\s+", raw, flags=re.IGNORECASE) if a.strip()]


def parse_bibtex(text: str) -> list[Citation]:
    """Parse `text` as BibTeX. Conservative: returns Citations for every
    @type{...} entry the splitter can recognise. Unknown fields land in
    `raw_fields` so the export round-trip preserves them."""
    out: list[Citation] = []
    for entry_type, key, body in _split_bibtex_entries(text):
        cit = Citation(cite_key=key, entry_type=entry_type)
        for m in _BIBTEX_FIELD.finditer(body):
            fkey = m.group("key").lower()
            fval = _strip_bibtex_braces(m.group("value"))
            cit.raw_fields[fkey] = fval
            if fkey == "title":
                cit.title = fval
            elif fkey == "author":
                cit.authors = _bibtex_authors(fval)
            elif fkey == "year":
                try:
                    cit.year = int(re.sub(r"\D", "", fval) or "0") or None
                except ValueError:
                    cit.year = None
            elif fkey == "journal":
                cit.journal = fval
            elif fkey == "volume":
                cit.volume = fval
            elif fkey == "number":
                cit.issue = fval
            elif fkey == "pages":
                cit.pages = fval
            elif fkey == "publisher":
                cit.publisher = fval
            elif fkey == "doi":
                cit.doi = fval
            elif fkey == "pmid":
                cit.pmid = fval
            elif fkey == "url":
                cit.url = fval
            elif fkey == "abstract":
                cit.abstract = fval
        out.append(cit)
    return out


# ── BibTeX exporter ────────────────────────────────────────────────────


def _bibtex_field(name: str, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    safe = str(value).replace("{", "(").replace("}", ")")
    return f"  {name} = {{{safe}}}"


def export_bibtex(citations: list[Citation]) -> str:
    out: list[str] = []
    for cit in citations:
        lines = [f"@{cit.entry_type}{{{cit.cite_key},"]
        for tag, value in (
            ("title", cit.title),
            ("author", " and ".join(cit.authors) if cit.authors else None),
            ("year", str(cit.year) if cit.year else None),
            ("journal", cit.journal),
            ("volume", cit.volume),
            ("number", cit.issue),
            ("pages", cit.pages),
            ("publisher", cit.publisher),
            ("doi", cit.doi),
            ("pmid", cit.pmid),
            ("url", cit.url),
            ("abstract", cit.abstract),
        ):
            line = _bibtex_field(tag, value)
            if line is not None:
                lines.append(line + ",")
        if lines[-1].endswith(","):
            lines[-1] = lines[-1][:-1]
        lines.append("}")
        out.append("\n".join(lines))
    return "\n\n".join(out) + ("\n" if out else "")


# ── RIS parser ─────────────────────────────────────────────────────────


_RIS_TAG = re.compile(r"^(?P<tag>[A-Z][A-Z0-9])\s{1,3}-\s?(?P<value>.*)$")


def parse_ris(text: str) -> list[Citation]:
    """Parse RIS (Endnote / Mendeley) — line-oriented `TAG  - value`."""
    out: list[Citation] = []
    fields: dict[str, list[str]] = {}
    ty: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = _RIS_TAG.match(line)
        if m is None:
            continue
        tag = m.group("tag")
        value = m.group("value").strip()
        if tag == "TY":
            ty = value
            fields = {"TY": [value]}
        elif tag == "ER":
            if ty is None:
                continue
            out.append(_ris_record_to_citation(fields))
            ty = None
            fields = {}
        else:
            fields.setdefault(tag, []).append(value)
    return out


def _ris_record_to_citation(fields: dict[str, list[str]]) -> Citation:
    ty = (fields.get("TY") or ["GEN"])[0]
    entry_type = _BIBTEX_TYPE_BY_RIS_TY.get(ty, "misc")
    authors: list[str] = []
    for tag in ("AU", "A1", "A2"):
        authors.extend(fields.get(tag, []))
    title = _first(fields, "TI", "T1", "BT")
    journal = _first(fields, "JO", "JF", "T2")
    year: int | None = None
    for tag in ("PY", "Y1", "DA"):
        raw = _first(fields, tag)
        if raw is None:
            continue
        digits = re.sub(r"\D", "", raw[:4])
        if digits:
            year = int(digits)
            break
    cite_anchor = (authors[0].split(",")[0] if authors else "ref").strip()
    cite_key = f"{cite_anchor}_{year or 'na'}".replace(" ", "_")
    return Citation(
        cite_key=cite_key,
        entry_type=entry_type,
        title=title,
        authors=authors,
        year=year,
        journal=journal,
        volume=_first(fields, "VL"),
        issue=_first(fields, "IS"),
        pages=_ris_pages(fields),
        publisher=_first(fields, "PB"),
        doi=_first(fields, "DO", "DI"),
        pmid=_ris_pmid(fields),
        url=_first(fields, "UR", "L1"),
        abstract=_first(fields, "AB", "N2"),
        raw_fields={tag: " | ".join(vals) for tag, vals in fields.items()},
    )


def _first(fields: dict[str, list[str]], *tags: str) -> str | None:
    for tag in tags:
        values = fields.get(tag)
        if values:
            return values[0]
    return None


def _ris_pages(fields: dict[str, list[str]]) -> str | None:
    sp = _first(fields, "SP")
    ep = _first(fields, "EP")
    if sp and ep:
        return f"{sp}-{ep}"
    return sp


def _ris_pmid(fields: dict[str, list[str]]) -> str | None:
    """Detect PubMed IDs in RIS records that put them in ID/AN tags."""
    for tag in ("ID", "AN", "PM"):
        raw = _first(fields, tag)
        if not raw:
            continue
        if raw.isdigit() and 4 <= len(raw) <= 10:
            return raw
    return None


# ── RIS exporter ───────────────────────────────────────────────────────


def export_ris(citations: list[Citation]) -> str:
    lines: list[str] = []
    for cit in citations:
        ty = _RIS_TY_BY_BIBTEX_TYPE.get(cit.entry_type, "GEN")
        lines.append(f"TY  - {ty}")
        for author in cit.authors:
            lines.append(f"AU  - {author}")
        if cit.title:
            lines.append(f"TI  - {cit.title}")
        if cit.journal:
            lines.append(f"JO  - {cit.journal}")
        if cit.year:
            lines.append(f"PY  - {cit.year}")
        if cit.volume:
            lines.append(f"VL  - {cit.volume}")
        if cit.issue:
            lines.append(f"IS  - {cit.issue}")
        if cit.pages:
            if "-" in cit.pages:
                sp, _, ep = cit.pages.partition("-")
                lines.append(f"SP  - {sp.strip()}")
                lines.append(f"EP  - {ep.strip()}")
            else:
                lines.append(f"SP  - {cit.pages}")
        if cit.publisher:
            lines.append(f"PB  - {cit.publisher}")
        if cit.doi:
            lines.append(f"DO  - {cit.doi}")
        if cit.pmid:
            lines.append(f"AN  - {cit.pmid}")
        if cit.url:
            lines.append(f"UR  - {cit.url}")
        if cit.abstract:
            lines.append(f"AB  - {cit.abstract}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


# ── Convenience ────────────────────────────────────────────────────────


def parse(text: str, fmt: CitationFormat | None = None) -> list[Citation]:
    """Auto-detect format when fmt is None."""
    detected = fmt or detect_format(text)
    if detected is None:
        raise ValueError(
            "Could not detect citation format. Supply `format='bibtex'` "
            "or `format='ris'` explicitly."
        )
    if detected == "bibtex":
        return parse_bibtex(text)
    return parse_ris(text)


def export(citations: list[Citation], fmt: CitationFormat) -> str:
    if fmt == "bibtex":
        return export_bibtex(citations)
    return export_ris(citations)


__all__ = [
    "Citation",
    "CitationFormat",
    "detect_format",
    "export",
    "export_bibtex",
    "export_ris",
    "parse",
    "parse_bibtex",
    "parse_ris",
]
