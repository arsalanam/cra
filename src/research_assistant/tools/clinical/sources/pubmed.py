"""PubMed source backend (NCBI E-utilities).

Consolidates the previously separate `pubmed_search` / `mesh_lookup` /
`fetch_pmc_fulltext` implementations behind one class. The agent-facing
tools in `tools/clinical/` are thin wrappers around the methods here.

All three operations share the same `RateLimitedClient` instance, so the
retry/backoff budget and (eventual) admin-configurable rate limit applies
uniformly across PubMed-related calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from defusedxml import ElementTree as DET

from ....config import get_settings
from ....config.auth import NoAuth, QueryParamAuth
from ....config.rate_limit import RateLimitConfig, RateLimitedClient
from .schema import StudyRecord

logger = logging.getLogger(__name__)


_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TOOL_NAME = "PydanticAI-Clinical/1.0"
_CONTACT_EMAIL = "research@example.com"

_MAX_PMC_BODY_CHARS = 60_000


def _build_config() -> RateLimitConfig:
    settings = get_settings()
    key = settings.ncbi_api_key
    return RateLimitConfig(
        name="pubmed",
        base_url=_BASE_URL,
        common_params={"tool": _TOOL_NAME, "email": _CONTACT_EMAIL},
        auth=QueryParamAuth("api_key", key) if key else NoAuth(),
    )


class PubmedSource:
    """Concrete PaperSource for PubMed + helpers for MeSH and PMC full text."""

    name = "pubmed"

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or _build_config()

    # ── PaperSource Protocol ────────────────────────────────────────────

    async def search(self, query: str, max_results: int = 20) -> str:
        max_results = min(max(int(max_results), 1), 50)
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                client = RateLimitedClient(http, self._config)
                esearch = await client.get(
                    "esearch.fcgi",
                    {
                        "db": "pubmed",
                        "term": query,
                        "retmax": max_results,
                        "retmode": "json",
                    },
                )
                esearch_data = esearch.json().get("esearchresult", {})
                id_list: list[str] = esearch_data.get("idlist", [])
                try:
                    total = int(esearch_data.get("count", 0))
                except (TypeError, ValueError):
                    total = len(id_list)

                if not id_list:
                    return json.dumps(
                        {
                            "query": query,
                            "total": total,
                            "returned": 0,
                            "studies": [],
                            "message": "No results found.",
                        }
                    )

                efetch = await client.get(
                    "efetch.fcgi",
                    {
                        "db": "pubmed",
                        "id": ",".join(id_list),
                        "retmode": "xml",
                    },
                )
                studies = await asyncio.to_thread(_parse_pubmed_xml, efetch.text)

            logger.info(
                "PubMed search: query=%r → %d studies (total=%d)",
                query[:80],
                len(studies),
                total,
            )
            return json.dumps(
                {"query": query, "total": total, "returned": len(studies), "studies": studies},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            logger.warning("PubMed HTTP %d for query=%r", e.response.status_code, query[:80])
            return json.dumps({"error": f"PubMed HTTP {e.response.status_code}"})
        except Exception as e:
            logger.error("PubMed search failed: %s", e, exc_info=True)
            return json.dumps({"error": f"PubMed search failed: {e}"})

    # ── PubMed-specific helpers ─────────────────────────────────────────

    async def lookup_mesh(self, term: str) -> str:
        """Find canonical MeSH descriptor + entry-term synonyms for `term`."""
        try:
            async with httpx.AsyncClient(timeout=20.0) as http:
                client = RateLimitedClient(http, self._config)
                esearch = await client.get(
                    "esearch.fcgi",
                    {
                        "db": "mesh",
                        "term": term,
                        "retmax": 5,
                        "retmode": "json",
                    },
                )
                ids: list[str] = esearch.json().get("esearchresult", {}).get("idlist", [])

                if not ids:
                    return json.dumps(
                        {
                            "term": term,
                            "results": [],
                            "message": (
                                "No MeSH match. Try a closely related term, or use "
                                "free-text [tiab] field tags in your PubMed query."
                            ),
                        }
                    )

                esummary = await client.get(
                    "esummary.fcgi",
                    {
                        "db": "mesh",
                        "id": ",".join(ids),
                        "retmode": "json",
                    },
                )
                payload = esummary.json().get("result", {})

            results = [
                _parse_mesh_record(uid, payload.get(uid, {})) for uid in payload.get("uids", [])
            ]
            logger.info("MeSH lookup: term=%r → %d descriptor(s)", term, len(results))
            return json.dumps({"term": term, "results": results}, ensure_ascii=False)

        except httpx.HTTPStatusError as e:
            logger.warning("MeSH HTTP %d for term=%r", e.response.status_code, term)
            return json.dumps({"error": f"MeSH lookup HTTP {e.response.status_code}"})
        except Exception as e:
            logger.error("MeSH lookup failed: %s", e, exc_info=True)
            return json.dumps({"error": f"MeSH lookup failed: {e}"})

    async def fetch_pmc_fulltext(self, pmid: str) -> str:
        """Fetch open-access PMC full text for `pmid`, when available."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                client = RateLimitedClient(http, self._config)
                # `linkname=pubmed_pmc` = the SAME article in PMC (open-access).
                # Default `cmd=neighbor` would link via citation — wrong direction.
                elink = await client.get(
                    "elink.fcgi",
                    {
                        "dbfrom": "pubmed",
                        "db": "pmc",
                        "id": pmid,
                        "linkname": "pubmed_pmc",
                        "retmode": "json",
                    },
                )
                pmc_id = _extract_pmc_id(elink.json())

                if not pmc_id:
                    return json.dumps(
                        {
                            "pmid": pmid,
                            "pmc_id": None,
                            "available": False,
                            "message": (
                                "No open-access PMC version found. The user should "
                                "fetch the PDF manually and paste missing values "
                                "into the data extraction table."
                            ),
                        }
                    )

                efetch = await client.get(
                    "efetch.fcgi",
                    {
                        "db": "pmc",
                        "id": pmc_id,
                        "retmode": "xml",
                    },
                )
                body = await asyncio.to_thread(_extract_pmc_text, efetch.text)

            truncated = body[:_MAX_PMC_BODY_CHARS]
            was_truncated = len(body) > _MAX_PMC_BODY_CHARS
            logger.info(
                "PMC full text: pmid=%s pmc=%s body=%d chars%s",
                pmid,
                pmc_id,
                len(body),
                " (truncated)" if was_truncated else "",
            )
            return json.dumps(
                {
                    "pmid": pmid,
                    "pmc_id": pmc_id,
                    "available": True,
                    "body": truncated,
                    "truncated": was_truncated,
                    "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/",
                },
                ensure_ascii=False,
            )

        except httpx.HTTPStatusError as e:
            logger.warning("PMC HTTP %d for pmid=%s", e.response.status_code, pmid)
            return json.dumps({"error": f"PMC fetch HTTP {e.response.status_code}", "pmid": pmid})
        except Exception as e:
            logger.error("PMC fetch failed for pmid=%s: %s", pmid, e, exc_info=True)
            return json.dumps({"error": f"PMC fetch failed: {e}", "pmid": pmid})


# ── Pure XML/JSON parsers (module-level so unit tests can call them) ─────


def _parse_pubmed_xml(xml_text: str) -> list[dict[str, Any]]:
    """Pull the fields we care about out of a PubmedArticleSet XML payload."""
    root = DET.fromstring(xml_text)
    studies: list[dict[str, Any]] = []

    for art in root.iter("PubmedArticle"):
        pmid = art.findtext(".//PMID", default="").strip()
        title = (art.findtext(".//ArticleTitle") or "").strip()
        journal = (
            art.findtext(".//Journal/Title") or art.findtext(".//Journal/ISOAbbreviation") or ""
        ).strip() or None

        year_text = (
            art.findtext(".//Journal/JournalIssue/PubDate/Year")
            or art.findtext(".//Journal/JournalIssue/PubDate/MedlineDate")
            or ""
        )
        year: int | None
        try:
            year = int(year_text[:4]) if year_text else None
        except ValueError:
            year = None

        authors: list[str] = []
        for au in art.iter("Author"):
            last = (au.findtext("LastName") or "").strip()
            initials = (au.findtext("Initials") or "").strip()
            if last:
                authors.append(f"{last} {initials}".strip() if initials else last)

        abstract_parts = [(t.text or "").strip() for t in art.iter("AbstractText")]
        abstract = " ".join(p for p in abstract_parts if p) or None

        pub_types = [
            (t.text or "").strip() for t in art.iter("PublicationType") if (t.text or "").strip()
        ]
        mesh = [
            (d.text or "").strip() for d in art.iter("DescriptorName") if (d.text or "").strip()
        ]

        # DOI: prefer ArticleIdList (consistent), fall back to ELocationID.
        doi: str | None = None
        for aid in art.iter("ArticleId"):
            text = (aid.text or "").strip()
            if aid.attrib.get("IdType") == "doi" and text:
                doi = text
                break
        if doi is None:
            for el in art.iter("ELocationID"):
                text = (el.text or "").strip()
                if el.attrib.get("EIdType") == "doi" and text:
                    doi = text
                    break

        studies.append(
            StudyRecord(
                source="pubmed",
                source_id=pmid,
                pmid=pmid,
                title=title,
                journal=journal,
                year=year,
                authors=authors[:10],
                abstract=abstract,
                publication_types=pub_types,
                mesh_headings=mesh,
                doi=doi,
            ).model_dump()
        )

    return studies


def _parse_mesh_record(uid: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Extract canonical descriptor + entry-term synonyms from one esummary record.

    Two record shapes matter:
      • `pharmacological-action` records (drug classes like 'Proton Pump
        Inhibitors') — canonical name is in ds_meshterms; the useful
        synonyms (drug class members) live in ds_palist with a category
        prefix (D=drug, C=compound). We surface the D-prefixed entries.
      • Standard descriptor records (diseases, anatomy, …) — canonical name
        + synonyms are all in ds_meshterms.
    """
    record_type = (rec.get("ds_recordtype") or "").strip()
    mesh_terms = rec.get("ds_meshterms") or []
    canonical = mesh_terms[0] if mesh_terms else (rec.get("title") or "").strip()

    entry_terms: list[str] = []
    if record_type == "pharmacological-action":
        for entry in rec.get("ds_palist") or []:
            if entry and entry[0] == "D" and len(entry) > 1:
                entry_terms.append(entry[1:])
        if not entry_terms:
            entry_terms = [
                (e[1:] if e and e[0] in "CD" else e) for e in (rec.get("ds_palist") or [])
            ]
    else:
        entry_terms = mesh_terms[1:] if len(mesh_terms) > 1 else []

    return {
        "uid": uid,
        "descriptor": canonical,
        "entry_terms": entry_terms,
        "record_type": record_type or None,
        "scope_note": (rec.get("ds_scopenote") or "").strip() or None,
    }


def _extract_pmc_id(elink_json: dict[str, Any]) -> str | None:
    """Pull the first PMC UID out of an elink.fcgi response."""
    for linkset in elink_json.get("linksets") or []:
        for db in linkset.get("linksetdbs") or []:
            if db.get("dbto") == "pmc" and db.get("links"):
                return str(db["links"][0])
    return None


def _extract_pmc_text(xml_text: str) -> str:
    """Walk JATS XML body and concatenate paragraph text + section headings."""
    try:
        root = DET.fromstring(xml_text)
    except ET.ParseError as e:
        return f"[Could not parse PMC XML: {e}]"

    parts: list[str] = []
    for body in root.iter("body"):
        _walk_body(body, parts)
        break
    return "\n\n".join(p for p in parts if p)


def _walk_body(node: ET.Element, parts: list[str]) -> None:
    for child in node:
        tag = child.tag.lower()
        if tag in ("sec", "section"):
            title_el = child.find("title")
            title_text = (title_el.text or "").strip() if title_el is not None else ""
            if title_text:
                parts.append(f"## {title_text}")
            _walk_body(child, parts)
        elif tag == "p":
            text = "".join(child.itertext()).strip()
            if text:
                parts.append(text)
        elif tag in ("table-wrap",):
            caption_el = child.find(".//caption")
            cap = "".join(caption_el.itertext()).strip() if caption_el is not None else ""
            parts.append(f"[TABLE: {cap or '(no caption)'}]")
