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

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Literal

from defusedxml.ElementTree import fromstring as _xml_fromstring

CitationFormat = Literal["bibtex", "ris", "csl_json", "endnote_xml"]


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
    """Sniff the citation format from the leading content."""
    head = "\n".join(text.splitlines()[:20]).strip()
    if not head:
        return None
    if "@" in head and re.search(r"@\w+\s*[{(]", head):
        return "bibtex"
    lead = head.lstrip()
    if lead.startswith("<") and ("<record" in text[:800] or "ref-type" in text[:800]):
        return "endnote_xml"
    if lead[:1] in "[{" and (
        '"issued"' in head
        or '"container-title"' in head
        or '"DOI"' in head
        or ('"type"' in head and '"id"' in head)
    ):
        return "csl_json"
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
    if (v.startswith("{") and v.endswith("}")) or (v.startswith('"') and v.endswith('"')):
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


# ── CSL JSON ───────────────────────────────────────────────────────────
# Citation Style Language JSON — the interchange format used by Zotero,
# Pandoc, and citeproc. An array of item objects; the type vocabulary
# differs from BibTeX (article-journal, paper-conference, …).

_CSL_TYPE_BY_ENTRY: dict[str, str] = {
    "article": "article-journal",
    "book": "book",
    "incollection": "chapter",
    "inproceedings": "paper-conference",
    "techreport": "report",
    "phdthesis": "thesis",
    "misc": "document",
}

_ENTRY_BY_CSL_TYPE: dict[str, str] = {
    "article-journal": "article",
    "article": "article",
    "book": "book",
    "chapter": "incollection",
    "paper-conference": "inproceedings",
    "report": "techreport",
    "thesis": "phdthesis",
}


def _csl_authors(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for a in item.get("author") or []:
        if not isinstance(a, dict):
            continue
        if a.get("literal"):
            names.append(str(a["literal"]))
        else:
            family = str(a.get("family", "")).strip()
            given = str(a.get("given", "")).strip()
            names.append(f"{family}, {given}".strip().strip(",").strip() if given else family)
    return [n for n in names if n]


def _csl_year(item: dict[str, Any]) -> int | None:
    issued = item.get("issued")
    if isinstance(issued, dict):
        parts = issued.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except (ValueError, TypeError):
                return None
    return None


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def parse_csl_json(text: str) -> list[Citation]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("CSL JSON must be an array of citation items.")
    out: list[Citation] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        authors = _csl_authors(raw)
        year = _csl_year(raw)
        anchor = (authors[0].split(",")[0] if authors else "ref").strip()
        cite_key = str(raw.get("id") or f"{anchor}_{year or 'na'}").replace(" ", "_")
        out.append(
            Citation(
                cite_key=cite_key,
                entry_type=_ENTRY_BY_CSL_TYPE.get(str(raw.get("type", "")), "misc"),
                title=_opt_str(raw.get("title")),
                authors=authors,
                year=year,
                journal=_opt_str(raw.get("container-title")),
                volume=_opt_str(raw.get("volume")),
                issue=_opt_str(raw.get("issue")),
                pages=_opt_str(raw.get("page")),
                publisher=_opt_str(raw.get("publisher")),
                doi=_opt_str(raw.get("DOI")),
                pmid=_opt_str(raw.get("PMID")),
                url=_opt_str(raw.get("URL")),
                abstract=_opt_str(raw.get("abstract")),
            )
        )
    return out


def export_csl_json(citations: list[Citation]) -> str:
    items: list[dict[str, Any]] = []
    for cit in citations:
        item: dict[str, Any] = {
            "id": cit.cite_key,
            "type": _CSL_TYPE_BY_ENTRY.get(cit.entry_type, "document"),
        }
        if cit.title:
            item["title"] = cit.title
        authors: list[dict[str, str]] = []
        for name in cit.authors:
            if "," in name:
                family, _, given = name.partition(",")
                authors.append({"family": family.strip(), "given": given.strip()})
            else:
                authors.append({"literal": name})
        if authors:
            item["author"] = authors
        if cit.year is not None:
            item["issued"] = {"date-parts": [[cit.year]]}
        for key, value in (
            ("container-title", cit.journal),
            ("volume", cit.volume),
            ("issue", cit.issue),
            ("page", cit.pages),
            ("publisher", cit.publisher),
            ("DOI", cit.doi),
            ("PMID", cit.pmid),
            ("URL", cit.url),
            ("abstract", cit.abstract),
        ):
            if value:
                item[key] = value
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False)


# ── EndNote XML ────────────────────────────────────────────────────────
# EndNote's export XML: <xml><records><record>…</record></records></xml>.
# Text fields are often wrapped in nested <style> elements, so all reads go
# through itertext(). Parsing uses defusedxml (untrusted input); writing
# uses stdlib ElementTree (safe — no external entities on output).

_ENDNOTE_REFTYPE_BY_ENTRY: dict[str, tuple[str, str]] = {
    "article": ("Journal Article", "17"),
    "book": ("Book", "6"),
    "incollection": ("Book Section", "5"),
    "inproceedings": ("Conference Proceedings", "10"),
    "techreport": ("Report", "27"),
    "phdthesis": ("Thesis", "32"),
    "misc": ("Generic", "13"),
}

_ENTRY_BY_ENDNOTE_REFTYPE: dict[str, str] = {
    "Journal Article": "article",
    "Book": "book",
    "Book Section": "incollection",
    "Conference Proceedings": "inproceedings",
    "Report": "techreport",
    "Thesis": "phdthesis",
}


def _en_text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    text = "".join(el.itertext()).strip()
    return text or None


def _endnote_record_to_citation(rec: ET.Element) -> Citation:
    ref_type_el = rec.find("ref-type")
    entry_type = "misc"
    if ref_type_el is not None:
        name = ref_type_el.get("name", "")
        entry_type = _ENTRY_BY_ENDNOTE_REFTYPE.get(name, "misc")

    authors = [
        t for a in rec.findall("./contributors/authors/author") if (t := _en_text(a)) is not None
    ]
    title = _en_text(rec.find("./titles/title"))
    journal = _en_text(rec.find("./titles/secondary-title"))
    year_raw = _en_text(rec.find("./dates/year"))
    year: int | None = None
    if year_raw:
        digits = re.sub(r"\D", "", year_raw[:4])
        year = int(digits) if digits else None
    anchor = (authors[0].split(",")[0] if authors else "ref").strip()
    cite_key = f"{anchor}_{year or 'na'}".replace(" ", "_")
    return Citation(
        cite_key=cite_key,
        entry_type=entry_type,
        title=title,
        authors=authors,
        year=year,
        journal=journal,
        volume=_en_text(rec.find("volume")),
        issue=_en_text(rec.find("number")),
        pages=_en_text(rec.find("pages")),
        publisher=_en_text(rec.find("publisher")),
        doi=_en_text(rec.find("electronic-resource-num")),
        pmid=_en_text(rec.find("accession-num")),
        url=_en_text(rec.find("./urls/related-urls/url")),
        abstract=_en_text(rec.find("abstract")),
    )


def parse_endnote_xml(text: str) -> list[Citation]:
    root = _xml_fromstring(text)
    return [_endnote_record_to_citation(rec) for rec in root.iter("record")]


def export_endnote_xml(citations: list[Citation]) -> str:
    root = ET.Element("xml")
    records = ET.SubElement(root, "records")
    for cit in citations:
        rec = ET.SubElement(records, "record")
        name, num = _ENDNOTE_REFTYPE_BY_ENTRY.get(cit.entry_type, ("Generic", "13"))
        rt = ET.SubElement(rec, "ref-type")
        rt.set("name", name)
        rt.text = num
        if cit.authors:
            authors_el = ET.SubElement(ET.SubElement(rec, "contributors"), "authors")
            for author in cit.authors:
                ET.SubElement(authors_el, "author").text = author
        titles = ET.SubElement(rec, "titles")
        if cit.title:
            ET.SubElement(titles, "title").text = cit.title
        if cit.journal:
            ET.SubElement(titles, "secondary-title").text = cit.journal
        if cit.year is not None:
            ET.SubElement(ET.SubElement(rec, "dates"), "year").text = str(cit.year)
        for tag, value in (
            ("volume", cit.volume),
            ("number", cit.issue),
            ("pages", cit.pages),
            ("publisher", cit.publisher),
            ("electronic-resource-num", cit.doi),
            ("accession-num", cit.pmid),
            ("abstract", cit.abstract),
        ):
            if value:
                ET.SubElement(rec, tag).text = value
        if cit.url:
            related = ET.SubElement(ET.SubElement(rec, "urls"), "related-urls")
            ET.SubElement(related, "url").text = cit.url
    return ET.tostring(root, encoding="unicode")


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
    if detected == "csl_json":
        return parse_csl_json(text)
    if detected == "endnote_xml":
        return parse_endnote_xml(text)
    return parse_ris(text)


def export(citations: list[Citation], fmt: CitationFormat) -> str:
    if fmt == "bibtex":
        return export_bibtex(citations)
    if fmt == "csl_json":
        return export_csl_json(citations)
    if fmt == "endnote_xml":
        return export_endnote_xml(citations)
    return export_ris(citations)


__all__ = [
    "Citation",
    "CitationFormat",
    "detect_format",
    "export",
    "export_bibtex",
    "export_csl_json",
    "export_endnote_xml",
    "export_ris",
    "parse",
    "parse_bibtex",
    "parse_csl_json",
    "parse_endnote_xml",
    "parse_ris",
]
