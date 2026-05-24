"""RAG service (in-process) — embeddings + retrieval for the local library.

R2 lands the embedding half: an `Embedder` protocol (`embedder.py`),
section-aware chunking (`chunking.py`), and a scheduler-driven background
drain (`embed_worker.py`). R3 will add semantic `rag_search` retrieval here.
"""

from __future__ import annotations

from .embed_worker import (
    drain_all,
    embed_pending_passages,
    nudge_embedding,
    register_embedding_drain,
)
from .embedder import Embedder, embedding_version, get_embedder
from .retrieval import RetrievedPassage, hybrid_search, rrf_fuse

__all__ = [
    "Embedder",
    "RetrievedPassage",
    "drain_all",
    "embed_pending_passages",
    "embedding_version",
    "get_embedder",
    "hybrid_search",
    "nudge_embedding",
    "register_embedding_drain",
    "rrf_fuse",
]
