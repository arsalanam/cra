"""Helpers for FastAPI file uploads."""

from __future__ import annotations

from fastapi import UploadFile

_DEFAULT_FILENAME = "upload.txt"


async def read_upload(file: UploadFile | None) -> tuple[str | None, str]:
    """Decode an uploaded file as UTF-8 (fallback latin-1).

    Returns `(content, filename)`. When no file is attached, returns
    `(None, "upload.txt")` so callers can use a stable default.
    """
    if not file or not file.filename:
        return None, _DEFAULT_FILENAME
    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("latin-1", errors="replace")
    return content, file.filename
