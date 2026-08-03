"""Unit tests for the Bedrock/Titan embedder — no network (boto3 mocked)."""

from __future__ import annotations

import json

import pytest

from research_assistant.rag import embedder as embmod
from research_assistant.rag.embedder import BedrockEmbedder, embedding_version


class _FakeBody:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


class _FakeClient:
    """Records requests; returns a vector whose first element encodes the
    input length so call order can be asserted."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def invoke_model(self, *, modelId: str, body: str) -> dict:
        req = json.loads(body)
        self.requests.append(req)
        dims = req["dimensions"]
        vec = [float(len(req["inputText"]))] + [0.0] * (dims - 1)
        return {"body": _FakeBody({"embedding": vec, "inputTextTokenCount": 3})}


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(embmod.boto3, "client", lambda *a, **k: client)
    return client


def test_embedding_version_tag() -> None:
    assert embedding_version() == "amazon.titan-embed-text-v2:0@1024"


@pytest.mark.asyncio
async def test_embed_query_request_shape_and_dims(fake_client: _FakeClient) -> None:
    emb = BedrockEmbedder()
    vec = await emb.embed_query("empagliflozin")
    assert len(vec) == emb.dimensions == 1024
    req = fake_client.requests[0]
    assert req["inputText"] == "empagliflozin"
    assert req["dimensions"] == 1024
    assert req["normalize"] is True


@pytest.mark.asyncio
async def test_embed_documents_preserves_order(fake_client: _FakeClient) -> None:
    emb = BedrockEmbedder()
    texts = ["a", "bbb", "cc"]
    vecs = await emb.embed_documents(texts)
    assert len(vecs) == 3
    # First element encodes input length → confirms order is preserved
    # despite concurrent fan-out.
    assert [v[0] for v in vecs] == [1.0, 3.0, 2.0]


@pytest.mark.asyncio
async def test_long_input_is_truncated(fake_client: _FakeClient) -> None:
    emb = BedrockEmbedder()
    await emb.embed_query("x" * 50_000)
    assert len(fake_client.requests[0]["inputText"]) == embmod._MAX_INPUT_CHARS
