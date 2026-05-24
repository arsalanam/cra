"""Unit tests for hybrid retrieval — RRF fusion + graceful SQLite fallback."""

from __future__ import annotations

import pytest

from research_assistant.rag.retrieval import hybrid_search, rrf_fuse


def _row(pid: str, section: str = "body") -> dict:
    return {"passage_id": pid, "section": section, "text": f"text-{pid}"}


def test_rrf_rewards_appearing_in_both_channels() -> None:
    dense = [_row("A"), _row("B", "abstract"), _row("C")]
    sparse = [_row("B", "abstract"), _row("D")]

    fused = rrf_fuse(dense, sparse, top_n=4)
    ids = [row["passage_id"] for row, _ in fused]

    # B is in both channels AND is an abstract (section-weighted) → clear top.
    assert ids[0] == "B"
    # Every unique passage across both channels is represented.
    assert set(ids) == {"A", "B", "C", "D"}
    # Scores are sorted descending.
    scores = [s for _, s in fused]
    assert scores == sorted(scores, reverse=True)


def test_rrf_respects_top_n_and_empty() -> None:
    dense = [_row("A"), _row("B"), _row("C")]
    assert len(rrf_fuse(dense, [], top_n=2)) == 2
    assert rrf_fuse([], [], top_n=5) == []


def test_section_weight_breaks_ties_toward_abstract() -> None:
    # Same single rank in one channel; abstract should outrank body.
    fused = rrf_fuse([_row("body1", "body"), _row("abs1", "abstract")], [], top_n=2)
    # abs1 is rank 1 (lower base RRF) but ×1.10; body1 is rank 0 ×1.0.
    # Base: body1=1/61=.01639, abs1=1/62*1.1=.01775 → abs1 wins.
    assert fused[0][0]["passage_id"] == "abs1"


@pytest.mark.asyncio
async def test_hybrid_search_empty_on_sqlite() -> None:
    """No Postgres → no vectors/FTS → empty result, no Bedrock call."""
    assert await hybrid_search("empagliflozin heart failure") == []
    assert await hybrid_search("   ") == []
