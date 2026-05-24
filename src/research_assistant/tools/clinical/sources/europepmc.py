"""Europe PMC source backend.

Calls the Europe PMC REST API (`/search`) and normalises the response into
the shared study shape used by `tools/clinical/sources.normalize`. No API
key is required; rate limiting comes from the same `RateLimitedClient` as
PubMed.

Field mapping (Europe PMC → shared shape):
  id              → source_id
  pmid            → pmid (may be missing for non-Medline records)
  doi             → doi
  title           → title
  journalTitle    → journal
  pubYear         → year
  authorString    → authors (comma-split, capped to 10)
  abstractText    → abstract
  pubTypeList     → publication_types
  meshHeadingList → mesh_headings
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ....config.rate_limit import RateLimitConfig, RateLimitedClient
from .schema import StudyRecord

logger = logging.getLogger(__name__)


_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _default_config() -> RateLimitConfig:
    """Fallback config when no DB-backed config is supplied."""
    return RateLimitConfig(name="europepmc", base_url=_BASE_URL)


class EuropePMCSource:
    """Concrete PaperSource for Europe PMC."""

    name = "europepmc"

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or _default_config()

    async def search(self, query: str, max_results: int = 20) -> str:
        max_results = min(max(int(max_results), 1), 50)
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                client = RateLimitedClient(http, self._config)
                resp = await client.get(
                    "search",
                    {
                        "query": query,
                        "format": "json",
                        "resultType": "core",
                        "pageSize": max_results,
                    },
                )
                payload = resp.json()

            try:
                total = int(payload.get("hitCount", 0))
            except (TypeError, ValueError):
                total = 0
            raw = payload.get("resultList", {}).get("result", []) or []
            studies = [_parse_europepmc_record(r) for r in raw]

            logger.info(
                "Europe PMC search: query=%r → %d studies (total=%d)",
                query[:80],
                len(studies),
                total,
            )
            return json.dumps(
                {"query": query, "total": total, "returned": len(studies), "studies": studies},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            logger.warning("Europe PMC HTTP %d for query=%r", e.response.status_code, query[:80])
            return json.dumps({"error": f"Europe PMC HTTP {e.response.status_code}"})
        except Exception as e:
            logger.error("Europe PMC search failed: %s", e, exc_info=True)
            return json.dumps({"error": f"Europe PMC search failed: {e}"})


# ── Pure parser (module-level so unit tests can call it) ─────────────────


def _parse_europepmc_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Map one Europe PMC result record to the shared study shape."""
    source_id = (rec.get("id") or "").strip()
    pmid = (rec.get("pmid") or "").strip() or None
    doi = (rec.get("doi") or "").strip() or None
    title = (rec.get("title") or "").strip()
    journal = (rec.get("journalTitle") or "").strip() or None

    year_text = rec.get("pubYear")
    year: int | None
    try:
        year = int(year_text) if year_text else None
    except (TypeError, ValueError):
        year = None

    author_string = (rec.get("authorString") or "").strip()
    authors = [a.strip() for a in author_string.split(",") if a.strip()][:10]

    abstract = (rec.get("abstractText") or "").strip() or None

    pub_types = _flatten_list(
        rec.get("pubTypeList", {}).get("pubType")
        if isinstance(rec.get("pubTypeList"), dict)
        else None
    )

    mesh_block = rec.get("meshHeadingList", {})
    mesh_items = mesh_block.get("meshHeading") if isinstance(mesh_block, dict) else None
    if isinstance(mesh_items, dict):
        mesh_items = [mesh_items]
    mesh: list[str] = []
    for m in mesh_items or []:
        if isinstance(m, dict):
            d = (m.get("descriptorName") or "").strip()
            if d:
                mesh.append(d)

    return StudyRecord(
        source="europepmc",
        source_id=source_id,
        pmid=pmid,
        title=title,
        journal=journal,
        year=year,
        authors=authors,
        abstract=abstract,
        publication_types=pub_types,
        mesh_headings=mesh,
        doi=doi,
    ).model_dump()


def _flatten_list(value: Any) -> list[str]:
    """Europe PMC sometimes returns a singleton string instead of a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []
