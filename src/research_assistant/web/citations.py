"""Citation-manager import / export endpoints (P1 #8).

Two endpoints under /api/citations:

  POST /api/citations/parse   — multipart .bib or .ris upload → list[Citation]
  POST /api/citations/export  — JSON {format, citations}      → file download

No new RBAC permission this slice — the citation primitives are
researcher-tier (both manuscript_drafter and sr_protocol specialists
that consume them are already gated). The endpoints inherit the
top-level auth dependency.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ..services.citations import (
    Citation,
    CitationFormat,
    detect_format,
    export,
    parse,
)

logger = logging.getLogger(__name__)


_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
_VALID_FORMATS: tuple[CitationFormat, ...] = ("bibtex", "ris")


class CitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    cite_key: str
    entry_type: str
    title: str | None = None
    authors: list[str] = []
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


class CitationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cite_key: str
    entry_type: str = "article"
    title: str | None = None
    authors: list[str] = []
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


class ParseResultOut(BaseModel):
    format_detected: str
    count: int
    citations: list[CitationOut]


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: str  # "bibtex" | "ris"
    citations: list[CitationIn]


def create_citations_router() -> APIRouter:
    router = APIRouter(prefix="/citations", tags=["citations"])

    @router.post("/parse", response_model=ParseResultOut)
    async def parse_upload(
        file: UploadFile = File(...),  # noqa: B008
        format: str | None = Form(default=None),
    ) -> ParseResultOut:
        """Parse a BibTeX or RIS file. When `format` is omitted, the
        endpoint sniffs the format from the file content."""
        data = await file.read()
        if not data:
            raise HTTPException(422, "Empty file.")
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            )
        text = data.decode("utf-8", errors="replace")
        sniffed: CitationFormat | None
        if format is not None:
            if format not in _VALID_FORMATS:
                raise HTTPException(
                    422,
                    f"Invalid format {format!r}; choose 'bibtex' or 'ris'.",
                )
            sniffed = format
        else:
            sniffed = detect_format(text)
            if sniffed is None:
                raise HTTPException(
                    422,
                    "Could not detect citation format. Supply format=bibtex|ris.",
                )
        try:
            citations = parse(text, fmt=sniffed)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return ParseResultOut(
            format_detected=sniffed,
            count=len(citations),
            citations=[CitationOut(**c.to_dict()) for c in citations],  # type: ignore[arg-type]
        )

    @router.post("/export")
    async def export_citations(body: ExportIn) -> Response:
        """Export a list of citations back to BibTeX or RIS."""
        if body.format not in _VALID_FORMATS:
            raise HTTPException(
                422,
                f"Invalid format {body.format!r}; choose 'bibtex' or 'ris'.",
            )
        citations = [
            Citation(
                cite_key=c.cite_key,
                entry_type=c.entry_type,
                title=c.title,
                authors=list(c.authors),
                year=c.year,
                journal=c.journal,
                volume=c.volume,
                issue=c.issue,
                pages=c.pages,
                publisher=c.publisher,
                doi=c.doi,
                pmid=c.pmid,
                url=c.url,
                abstract=c.abstract,
            )
            for c in body.citations
        ]
        text = export(citations, body.format)
        ext = "bib" if body.format == "bibtex" else "ris"
        media_type = (
            "application/x-bibtex"
            if body.format == "bibtex"
            else "application/x-research-info-systems"
        )
        return Response(
            content=text,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="references.{ext}"',
                "Cache-Control": "no-store",
            },
        )

    return router


__all__ = ["create_citations_router"]
