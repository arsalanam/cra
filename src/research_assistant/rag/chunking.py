"""Section-aware text chunking for RAG (R2, doc §4.3 "lite").

Splits a publication body into token-bounded, overlapping passages while
tracking a best-effort IMRaD section label. Abstracts are already
chunk-sized, so they bypass this and are stored as a single passage in R0.

The token count is a cheap ~4-chars/token estimate — good enough for sizing
windows; the real count from the embedder is irrelevant to retrieval quality
at this granularity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Headings we recognise as section boundaries. Matched case-insensitively
# against short standalone lines.
_SECTION_PATTERN = re.compile(
    r"^\s*(?:\d+\.?\s*)?"
    r"(abstract|background|introduction|methods?|materials?(?:\s+and\s+methods)?|"
    r"results?|findings?|discussion|conclusions?|references?|acknowledge?ments?)"
    r"\b[:.]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Chunk:
    section: str
    ordinal: int
    text: str
    token_count: int


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _normalise_section(heading: str) -> str:
    h = heading.strip().lower()
    if h.startswith("material"):
        return "methods"
    if h.startswith("method"):
        return "methods"
    if h.startswith("finding"):
        return "results"
    if h.startswith("result"):
        return "results"
    if h.startswith("conclusion"):
        return "conclusion"
    if h.startswith("introduction") or h.startswith("background"):
        return "introduction"
    return h


def chunk_text(text: str, *, target_tokens: int = 512, overlap_tokens: int = 50) -> list[Chunk]:
    """Split ``text`` into overlapping, section-labelled chunks.

    Paragraphs are packed greedily up to ``target_tokens``; consecutive
    chunks share a ``overlap_tokens`` tail so a passage split across a
    boundary is still retrievable from either side. Short inputs collapse to
    a single chunk.
    """
    if not text or not text.strip():
        return []

    target_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    chunks: list[Chunk] = []
    section = "body"
    buf = ""
    buf_section = section
    ordinal = 0

    def flush() -> None:
        nonlocal buf, ordinal
        if buf.strip():
            chunks.append(
                Chunk(
                    section=buf_section,
                    ordinal=ordinal,
                    text=buf.strip(),
                    token_count=_approx_tokens(buf),
                )
            )
            ordinal += 1

    for para in paragraphs:
        heading = _SECTION_PATTERN.match(para)
        if heading:
            # Section header line — switch section; headers carry no content.
            flush()
            buf = ""
            section = _normalise_section(heading.group(1))
            buf_section = section
            continue

        if not buf:
            buf = para
            buf_section = section
        elif len(buf) + len(para) + 2 <= target_chars:
            buf += "\n\n" + para
        else:
            flush()
            tail = buf[-overlap_chars:] if overlap_chars else ""
            buf = (tail + "\n\n" + para).strip() if tail else para
            buf_section = section

    flush()
    return chunks
