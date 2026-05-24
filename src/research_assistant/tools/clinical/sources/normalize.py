"""Cross-source dedupe for fan-out search results.

When `search_papers` runs PubMed and Europe PMC in parallel, the same
article often appears in both — Europe PMC re-indexes the Medline corpus,
so an article will share PMID and DOI across the two sources.

`dedupe_studies` keeps the first occurrence of each identity key. Callers
must order their input list by preferred source (PubMed first) so that the
richer Medline metadata wins on ties.

Identity-key priority per study:
  1. `pmid`              (if present)
  2. lowercased `doi`    (if present)
  3. `source:source_id`  (always)

Any one match counts as a duplicate.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _study_keys(study: dict[str, Any]) -> list[str]:
    """Compute the identity keys used for cross-source dedup."""
    keys: list[str] = []
    pmid = study.get("pmid")
    if isinstance(pmid, str) and pmid.strip():
        keys.append(f"pmid:{pmid.strip()}")
    doi = study.get("doi")
    if isinstance(doi, str) and doi.strip():
        keys.append(f"doi:{doi.strip().lower()}")
    source = study.get("source", "?")
    sid = study.get("source_id", "?")
    keys.append(f"src:{source}:{sid}")
    return keys


def dedupe_studies(studies: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe a list of study dicts across sources, preserving input order."""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for study in studies:
        keys = _study_keys(study)
        if any(k in seen for k in keys):
            continue
        seen.update(keys)
        result.append(study)
    return result
