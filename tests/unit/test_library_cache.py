"""Unit tests for the R0 local publication cache.

Covers the content-addressable identity, idempotent write-through, the
`cached`-flag membership snapshot, and the SHA-256 blob store.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.library import blobstore
from research_assistant.persistence.library.repository import (
    filter_cached_ids,
    publication_id,
    store_abstract_passage,
    upsert_publication,
)
from research_assistant.persistence.models import Passage, Publication


def _study(**overrides: object) -> dict:
    base: dict = {
        "source": "pubmed",
        "source_id": "12345",
        "pmid": "12345",
        "doi": "10.1/AbC",
        "title": "Empagliflozin in heart failure",
        "journal": "NEJM",
        "year": 2020,
        "authors": ["Doe J", "Roe R"],
        "abstract": "Background: ... Results: HR 0.75.",
        "publication_types": ["Randomized Controlled Trial"],
        "mesh_headings": ["Heart Failure", "SGLT2 Inhibitors"],
    }
    base.update(overrides)
    return base


# ── identity ────────────────────────────────────────────────────────────────


def test_publication_id_prefers_pmid_then_doi_then_hash() -> None:
    assert publication_id(_study()) == "pmid:12345"
    assert publication_id(_study(pmid=None)) == "doi:10.1/abc"  # lowercased
    sid = publication_id(_study(pmid=None, doi=None))
    assert sid.startswith("sha256:")
    # Stable across calls for the same source:source_id.
    assert sid == publication_id(_study(pmid=None, doi=None))


# ── upsert + passages ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_persists_metadata_and_abstract_passage(db_session: AsyncSession) -> None:
    study = _study()
    pid = await upsert_publication(db_session, study)
    await store_abstract_passage(db_session, pid, study["abstract"])

    pub = await db_session.get(Publication, pid)
    assert pub is not None
    assert pub.pmid == "12345"
    assert pub.doi == "10.1/abc"  # normalised to lowercase
    assert pub.year == 2020
    assert "SGLT2 Inhibitors" in pub.mesh_terms_json

    passages = (
        await db_session.scalars(select(Passage).where(Passage.publication_id == pid))
    ).all()
    assert len(passages) == 1
    assert passages[0].section == "abstract"
    assert passages[0].token_count
    assert passages[0].token_count > 0


@pytest.mark.asyncio
async def test_upsert_is_idempotent_and_backfills(db_session: AsyncSession) -> None:
    # First insert from a source lacking an abstract.
    pid = await upsert_publication(db_session, _study(abstract=None, mesh_headings=[]))
    await store_abstract_passage(db_session, pid, None)
    first = await db_session.get(Publication, pid)
    assert first is not None
    assert first.abstract is None
    cached_at = first.first_cached_at

    # Second hit (same PMID) now carries an abstract + MeSH → back-filled.
    pid2 = await upsert_publication(db_session, _study())
    await store_abstract_passage(db_session, pid2, _study()["abstract"])
    assert pid2 == pid

    # Still exactly one publication row, and one abstract passage.
    n_pubs = await db_session.scalar(select(func.count()).select_from(Publication))
    n_pass = await db_session.scalar(select(func.count()).select_from(Passage))
    assert n_pubs == 1
    assert n_pass == 1

    refreshed = await db_session.get(Publication, pid)
    assert refreshed is not None
    assert refreshed.abstract is not None  # back-filled
    assert "Heart Failure" in refreshed.mesh_terms_json  # back-filled
    assert refreshed.first_cached_at == cached_at  # preserved


@pytest.mark.asyncio
async def test_filter_cached_ids_returns_only_known(db_session: AsyncSession) -> None:
    pid = await upsert_publication(db_session, _study())
    known = await filter_cached_ids(db_session, [pid, "pmid:99999", "doi:nope"])
    assert known == {pid}
    assert await filter_cached_ids(db_session, []) == set()


# ── blob store ────────────────────────────────────────────────────────────────


def test_store_blob_is_content_addressable_and_dedupes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["LIBRARY_RAW_DIR"] = str(tmp_path)
    try:
        sha1, rel1 = blobstore.store_blob(b"<article>full text</article>")
        sha2, rel2 = blobstore.store_blob(b"<article>full text</article>")
        # Same content → same hash + path, written once (idempotent).
        assert sha1 == sha2
        assert rel1 == rel2
        assert rel1 == f"{sha1[:2]}/{sha1}"
        assert blobstore.absolute_path(rel1).read_bytes() == b"<article>full text</article>"

        sha3, _ = blobstore.store_blob(b"different bytes")
        assert sha3 != sha1
    finally:
        del os.environ["LIBRARY_RAW_DIR"]


# ── write-through wrapper + cached flag (real get_db_session path) ────────────


@pytest.mark.asyncio
async def test_cache_search_results_flag_reflects_pre_search_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """First search → cached:false for all; repeat search → cached:true.

    Uses a file-backed SQLite DB so writes persist across the two separate
    sessions cache_search_results opens (an in-memory DB would not).
    """
    from research_assistant.persistence import database
    from research_assistant.persistence.library import (
        cache_search_results,
        publication_id,
    )

    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}"
    os.environ["LIBRARY_RAW_DIR"] = str(tmp_path / "lib")
    database.reset_engine()
    try:
        await database.init_db()
        studies = [
            _study(),
            _study(pmid="222", source_id="222", doi=None, title="Other trial"),
        ]
        ids = {publication_id(s) for s in studies}

        first = await cache_search_results(studies)
        assert first == set()  # nothing was cached before the first search

        second = await cache_search_results(studies)
        assert second == ids  # both are now in the library
    finally:
        database.reset_engine()
        os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
        del os.environ["LIBRARY_RAW_DIR"]
        database.reset_engine()
