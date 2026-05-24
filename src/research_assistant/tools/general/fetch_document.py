"""
fetch_document tool — download and extract text from PDFs, DOCX, and PPTX URLs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from io import BytesIO
from typing import Any

import httpx
from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from .._emit import emit_run

logger = logging.getLogger(__name__)

_SUPPORTED_TYPES: dict[str, list[str]] = {
    "pdf": ["application/pdf", ".pdf"],
    "docx": [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ],
    "pptx": [
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ],
}

_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _detect_type(url: str, content_type: str) -> str | None:
    ct = content_type.lower().split(";")[0].strip()
    url_lower = url.lower().split("?")[0]
    for doc_type, markers in _SUPPORTED_TYPES.items():
        if ct in markers or any(url_lower.endswith(m) for m in markers if m.startswith(".")):
            return doc_type
    return None


def _extract_pdf(data: bytes) -> dict[str, Any]:
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        if text:
            pages.append({"page": i + 1, "text": text})
    doc.close()
    return {
        "type": "pdf",
        "page_count": len(pages),
        "pages": pages[:50],
    }


def _extract_docx(data: bytes) -> dict[str, Any]:
    from docx import Document

    doc = Document(BytesIO(data))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return {
        "type": "docx",
        "paragraph_count": len(paragraphs),
        "paragraphs": paragraphs[:200],
    }


def _extract_pptx(data: bytes) -> dict[str, Any]:
    from pptx import Presentation

    prs = Presentation(BytesIO(data))
    slides = []
    for i, slide in enumerate(prs.slides):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        texts.append(text)
        if texts:
            slides.append({"slide": i + 1, "text": texts})
    return {
        "type": "pptx",
        "slide_count": len(prs.slides),
        "slides": slides[:100],
    }


_EXTRACTORS = {
    "pdf": _extract_pdf,
    "docx": _extract_docx,
    "pptx": _extract_pptx,
}


async def _impl(url: str) -> str:
    """Download a document URL and extract structured text."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url, headers={"User-Agent": "ResearchAssistant/1.0"})
            resp.raise_for_status()

            if len(resp.content) > _MAX_DOWNLOAD_BYTES:
                msg = f"File too large ({len(resp.content)} bytes, max {_MAX_DOWNLOAD_BYTES})"
                return json.dumps({"error": msg})

            content_type = resp.headers.get("content-type", "")
            doc_type = _detect_type(url, content_type)

            if doc_type is None:
                return json.dumps({
                    "error": f"Unsupported file type. Content-Type: {content_type}. "
                    "Supported: PDF, DOCX, PPTX.",
                })

            extractor = _EXTRACTORS[doc_type]
            result = await asyncio.to_thread(extractor, resp.content)
            result["source_url"] = url
            logger.info("Extracted %s from %s (%d bytes)", doc_type, url[:80], len(resp.content))

            output = json.dumps(result, ensure_ascii=False, default=str)
            if len(output) > 15000:
                output = output[:15000] + '..."}'
            return output

    except httpx.HTTPStatusError as e:
        logger.warning("HTTP %d fetching document %s", e.response.status_code, url[:80])
        return json.dumps({"error": f"HTTP {e.response.status_code} fetching {url}"})
    except Exception as e:
        logger.error("Document extraction failed for %s: %s", url[:80], e, exc_info=True)
        return json.dumps({"error": f"Document extraction failed: {e}"})


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def fetch_document(ctx: RunContext[AgentDeps], url: str) -> str:
        """
        Download and extract text from a document URL. Supports PDF, DOCX (Word),
        and PPTX (PowerPoint) files. Returns structured JSON with extracted text
        organized by page/slide/paragraph. Use when a search result links to a
        document file rather than a web page.
        """
        return await emit_run(
            ctx,
            tool="fetch_document",
            icon="D",
            args={"url": url},
            description=f"Downloading document: {url[:80]}...",
            impl=lambda: _impl(url),
        )
