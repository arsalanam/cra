"""Content-addressable raw-blob store for the local publication cache (R0).

Full-text XML and (later) uploaded PDFs are written under
``settings.library_raw_dir``, keyed by the SHA-256 of their bytes and
sharded two levels deep (``<sha[:2]>/<sha>``) to keep any one directory
small. Identical content dedupes to a single file. Abstracts are NOT stored
here — they live in the ``publications.abstract`` column.

Returned paths are RELATIVE to the store root so the blob store can be
remounted at a different absolute path (e.g. a shared k8s volume) without
rewriting ``publications.raw_path``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ...config import get_settings

logger = logging.getLogger(__name__)


def store_root() -> Path:
    """Absolute root of the blob store (read fresh so tests can override)."""
    return Path(get_settings().library_raw_dir)


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def relative_path_for(sha: str) -> str:
    """The store-relative path a blob with this hash lives at."""
    return f"{sha[:2]}/{sha}"


def absolute_path(rel_path: str) -> Path:
    """Resolve a stored relative path back to an absolute filesystem path."""
    return store_root() / rel_path


def store_blob(content: bytes) -> tuple[str, str]:
    """Write ``content`` to the store; return ``(sha256_hex, relative_path)``.

    Content-addressable and idempotent: identical bytes map to the same path
    and are written at most once.
    """
    sha = sha256_hex(content)
    rel = relative_path_for(sha)
    path = store_root() / rel
    if path.exists():
        return sha, rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    logger.debug("Stored blob %s (%d bytes)", sha, len(content))
    return sha, rel
