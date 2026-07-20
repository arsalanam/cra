"""Embedding backend for RAG (R2).

`Embedder` is the provider-agnostic protocol (mirrors the `PaperSource`
pattern); `BedrockEmbedder` is the Titan Text Embeddings v2 implementation.
Keeping the protocol seam means a local biomedical model (MedCPT/SapBERT)
can swap in later for the closed-network / HIPAA case without touching the
chunking, worker, or retrieval code — Bedrock is an external API and so is
not valid for sensitive corpora.

Titan has no batch API: each text is one `invoke_model` call. The calls are
I/O-bound, so `embed_documents` fans them out with a bounded semaphore and
runs the blocking boto3 client in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Protocol, runtime_checkable

import boto3

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

# Titan v2 accepts up to ~8192 tokens; truncate defensively (~4 chars/token)
# so an over-long passage degrades to a partial embedding instead of an API
# error. Chunks are ~512 tokens, so this only ever bites pathological inputs.
_MAX_INPUT_CHARS = 30_000


@runtime_checkable
class Embedder(Protocol):
    """Provider-agnostic embedding interface."""

    model_id: str
    dimensions: int

    async def embed_query(self, text: str) -> list[float]: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


def embedding_version(settings: Settings | None = None) -> str:
    """Stable `<model_id>@<dims>` tag stamped onto embedded passages.

    Lets the worker detect rows embedded by a different model/dimension and
    re-embed them.
    """
    s = settings or get_settings()
    return f"{s.embedding_model_id}@{s.embedding_dimensions}"


class BedrockEmbedder:
    """Titan Text Embeddings v2 via Bedrock `invoke_model`."""

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self.model_id = s.embedding_model_id
        self.dimensions = s.embedding_dimensions
        self._region = s.aws_region
        self._max_concurrency = max(1, s.embedding_max_concurrency)
        self._client = None  # lazy — created on first use, reused thereafter
        # T1 spend quota: Titan reports inputTextTokenCount per call; the
        # instance accumulates so batch callers (embed_worker) can write one
        # spend-ledger row per drain pass. Lock because _embed_sync runs in
        # worker threads.
        self.total_input_tokens = 0
        self._token_lock = threading.Lock()

    def _client_(self) -> object:
        if self._client is None:
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def _embed_sync(self, text: str) -> list[float]:
        body = json.dumps(
            {
                "inputText": text[:_MAX_INPUT_CHARS],
                "dimensions": self.dimensions,
                "normalize": True,
            }
        )
        resp = self._client_().invoke_model(modelId=self.model_id, body=body)  # type: ignore[attr-defined]
        payload = json.loads(resp["body"].read())
        tokens = int(payload.get("inputTextTokenCount", 0) or 0)
        if tokens:
            with self._token_lock:
                self.total_input_tokens += tokens
        vector: list[float] = payload["embedding"]
        return vector

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        sem = asyncio.Semaphore(self._max_concurrency)

        async def _one(t: str) -> list[float]:
            async with sem:
                return await asyncio.to_thread(self._embed_sync, t)

        return list(await asyncio.gather(*(_one(t) for t in texts)))


def get_embedder() -> Embedder:
    """Factory — returns the configured embedder (Bedrock/Titan v2 today)."""
    return BedrockEmbedder(get_settings())
