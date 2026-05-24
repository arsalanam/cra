"""Library endpoints — PDF upload/ingestion (R1) + browse/search/stats (R4).

The library is the local publication cache (see persistence/library). These
endpoints let a researcher upload PDFs of papers they can't reach via the
API sources (Embase/Cochrane behind paywalls — they download the PDF and
drop it here) and browse/search everything that's been cached.

Gated by `current_user` at the app level (authenticated researchers), same
as threads and watches. Publications are a shared corpus, not per-user, in
v1.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..persistence.database import get_db_session
from ..persistence.library import (
    cache_uploaded_document,
    delete_publication,
    get_publication_detail,
    library_stats,
    list_publications,
)
from ..persistence.models import Passage, Publication

logger = logging.getLogger(__name__)

# PDFs only in v1. Cap matches fetch_document's download limit.
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Below this much extracted text we treat the PDF as scanned/image-only.
# OCR is out of scope for v1 — we reject rather than store an empty shell.
_MIN_TEXT_CHARS = 200


# ── DTOs ──────────────────────────────────────────────────────────────────────


class StatsOut(BaseModel):
    publications: int
    passages: int
    embedded_passages: int
    fulltext_publications: int
    by_source: dict[str, int]


class PublicationOut(BaseModel):
    id: str
    title: str
    source: str
    pmid: str | None
    doi: str | None
    journal: str | None
    year: int | None
    authors: list[str]
    has_fulltext: bool
    passage_count: int
    embedded_count: int
    first_cached_at: datetime


class PassageOut(BaseModel):
    id: str
    section: str
    ordinal: int
    snippet: str
    token_count: int | None
    embedded: bool


class PublicationDetailOut(PublicationOut):
    abstract: str | None
    mesh_terms: list[str]
    passages: list[PassageOut]


class UploadOut(BaseModel):
    publication_id: str
    title: str
    doi: str | None
    pages: int
    chars: int
    passages_created: int
    already_cached: bool


def _authors(pub: Publication) -> list[str]:
    try:
        return list(json.loads(pub.authors_json or "[]"))
    except (json.JSONDecodeError, TypeError):
        return []


def _pub_out(pub: Publication, passage_count: int, embedded_count: int) -> PublicationOut:
    return PublicationOut(
        id=pub.id,
        title=pub.title,
        source=pub.source,
        pmid=pub.pmid,
        doi=pub.doi,
        journal=pub.journal,
        year=pub.year,
        authors=_authors(pub),
        has_fulltext=pub.raw_path is not None,
        passage_count=passage_count,
        embedded_count=embedded_count,
        first_cached_at=pub.first_cached_at,
    )


def _extract_pdf_text(data: bytes) -> tuple[str, int, str | None]:
    """Extract full text + page count + embedded title from a PDF (sync)."""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        parts = [t for page in doc if (t := page.get_text("text").strip())]
        meta_title = (doc.metadata or {}).get("title") or None
        page_count = doc.page_count
    finally:
        doc.close()
    return "\n\n".join(parts), page_count, (meta_title.strip() if meta_title else None)


def create_library_router() -> APIRouter:
    """Build the `/library/*` router."""
    router = APIRouter(prefix="/library", tags=["library"])

    @router.get("/stats", response_model=StatsOut)
    async def stats() -> StatsOut:
        async with get_db_session() as session:
            return StatsOut(**await library_stats(session))

    @router.get("", response_model=list[PublicationOut])
    async def list_pubs(
        q: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[PublicationOut]:
        limit = max(1, min(limit, 200))
        async with get_db_session() as session:
            rows = await list_publications(session, q=q, limit=limit, offset=offset)
            return [_pub_out(pub, n, emb) for pub, n, emb in rows]

    @router.get("/{publication_id}", response_model=PublicationDetailOut)
    async def get_pub(publication_id: str) -> PublicationDetailOut:
        async with get_db_session() as session:
            found = await get_publication_detail(session, publication_id)
            if found is None:
                raise HTTPException(404, "Publication not found")
            pub, passages = found
            base = _pub_out(pub, len(passages), sum(1 for p in passages if p.embedding is not None))
            mesh: list[str] = []
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                mesh = list(json.loads(pub.mesh_terms_json or "[]"))
            return PublicationDetailOut(
                **base.model_dump(),
                abstract=pub.abstract,
                mesh_terms=mesh,
                passages=[_passage_out(p) for p in passages],
            )

    @router.delete("/{publication_id}", status_code=204)
    async def delete_pub(publication_id: str) -> None:
        async with get_db_session() as session:
            if not await delete_publication(session, publication_id):
                raise HTTPException(404, "Publication not found")

    @router.post("/upload", response_model=UploadOut, status_code=201)
    async def upload(
        file: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form()] = None,
        doi: Annotated[str | None, Form()] = None,
    ) -> UploadOut:
        data = await file.read()
        if not data:
            raise HTTPException(422, "Empty file.")
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
        if not data[:5] == b"%PDF-":
            raise HTTPException(422, "Only PDF uploads are supported in v1.")

        text, pages, meta_title = await run_in_threadpool(_extract_pdf_text, data)
        if len(text) < _MIN_TEXT_CHARS:
            raise HTTPException(
                422,
                "Could not extract text — the PDF looks scanned/image-only. "
                "OCR is not supported yet; please upload a text-based PDF.",
            )

        final_title = (title or meta_title or file.filename or "Uploaded document").strip()
        result = await cache_uploaded_document(
            pdf_bytes=data,
            text=text,
            title=final_title,
            doi=(doi.strip() or None) if doi else None,
        )
        logger.info(
            "Library upload: %r (%d pages, %d chars) -> %s",
            final_title,
            pages,
            len(text),
            result["publication_id"],
        )
        return UploadOut(pages=pages, chars=len(text), **result)

    return router


def _passage_out(p: Passage) -> PassageOut:
    body = p.text or ""
    snippet = body if len(body) <= 280 else body[:280] + "…"
    return PassageOut(
        id=p.id,
        section=p.section,
        ordinal=p.ordinal,
        snippet=snippet,
        token_count=p.token_count,
        embedded=p.embedding is not None,
    )
