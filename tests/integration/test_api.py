"""Integration tests for FastAPI routes (health, threads, usage)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from research_assistant.persistence.database import reset_engine


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every test at a fresh in-memory SQLite database."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    reset_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from research_assistant.web.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Trigger the lifespan startup (init_db)
        yield c
    reset_engine()


# Use a separate fixture that manually triggers lifespan
@pytest.fixture
async def live_client() -> AsyncIterator[AsyncClient]:
    """Client that properly triggers FastAPI lifespan events."""
    from research_assistant.persistence.database import init_db
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_engine()


async def test_health_endpoint(live_client: AsyncClient) -> None:
    resp = await live_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "model" in data
    assert "region" in data


async def test_questions_endpoint(live_client: AsyncClient) -> None:
    resp = await live_client.get("/api/questions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── Thread CRUD via API ──────────────────────────────────────────────────


async def test_create_thread(live_client: AsyncClient) -> None:
    resp = await live_client.post("/api/threads", json={"title": "My thread"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "My thread"
    assert "id" in data


async def test_list_threads(live_client: AsyncClient) -> None:
    await live_client.post("/api/threads", json={"title": "A"})
    await live_client.post("/api/threads", json={"title": "B"})
    resp = await live_client.get("/api/threads")
    assert resp.status_code == 200
    threads = resp.json()
    assert len(threads) >= 2


async def test_get_thread(live_client: AsyncClient) -> None:
    create_resp = await live_client.post("/api/threads", json={"title": "Find me"})
    thread_id = create_resp.json()["id"]
    resp = await live_client.get(f"/api/threads/{thread_id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Find me"


async def test_get_thread_not_found(live_client: AsyncClient) -> None:
    resp = await live_client.get("/api/threads/nonexistent-id")
    assert resp.status_code == 404


async def test_delete_thread(live_client: AsyncClient) -> None:
    create_resp = await live_client.post("/api/threads", json={"title": "Delete me"})
    thread_id = create_resp.json()["id"]
    resp = await live_client.delete(f"/api/threads/{thread_id}")
    assert resp.status_code == 204
    resp = await live_client.get(f"/api/threads/{thread_id}")
    assert resp.status_code == 404


async def test_delete_thread_not_found(live_client: AsyncClient) -> None:
    resp = await live_client.delete("/api/threads/nonexistent-id")
    assert resp.status_code == 404


# ── Messages ──────────────────────────────────────────────────────────────


async def test_get_messages_empty(live_client: AsyncClient) -> None:
    create_resp = await live_client.post("/api/threads", json={"title": "Empty"})
    thread_id = create_resp.json()["id"]
    resp = await live_client.get(f"/api/threads/{thread_id}/messages")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_messages_not_found_thread(live_client: AsyncClient) -> None:
    resp = await live_client.get("/api/threads/nonexistent/messages")
    assert resp.status_code == 404


# ── Usage ─────────────────────────────────────────────────────────────────


async def test_usage_monthly_empty(live_client: AsyncClient) -> None:
    resp = await live_client.get("/api/threads/usage/monthly")
    assert resp.status_code == 200
    data = resp.json()
    assert data["questions"] == 0
    assert data["total_tokens"] == 0
    assert data["tool_breakdown"] == {}
    assert "period" in data


async def test_usage_monthly_aggregates_done_events(live_client: AsyncClient) -> None:
    """Regression: the Usage page reads done StreamEvents written by the dispatcher.
    If dispatch.py ever stops persisting them, the page silently reverts to zeros.
    This seeds a couple of done events directly and asserts the endpoint sums them."""
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.repository import ThreadRepository

    async with get_db_session() as session:
        repo = ThreadRepository(session)
        thread = await repo.create_thread(title="Usage seed thread")
        m1 = await repo.add_message(thread_id=thread.id, role="assistant", final_answer="{}")
        m2 = await repo.add_message(thread_id=thread.id, role="assistant", final_answer="{}")
        await repo.add_stream_event(
            message_id=m1.id, event_type="done", sequence_num=0,
            data={
                "usage": {"input_tokens": 1200, "output_tokens": 400, "requests": 3, "tool_calls": 2},
                "tool_usage": {"mesh_lookup": 1, "search_papers": 1},
            },
        )
        await repo.add_stream_event(
            message_id=m2.id, event_type="done", sequence_num=0,
            data={
                "usage": {"input_tokens": 800, "output_tokens": 200, "requests": 2, "tool_calls": 3},
                "tool_usage": {"search_papers": 2, "fetch_pmc_fulltext": 1},
            },
        )

    resp = await live_client.get("/api/threads/usage/monthly")
    assert resp.status_code == 200
    data = resp.json()
    assert data["questions"] == 2
    assert data["input_tokens"] == 2000
    assert data["output_tokens"] == 600
    assert data["total_tokens"] == 2600
    assert data["model_requests"] == 5
    assert data["tool_calls"] == 5
    assert data["tool_breakdown"] == {
        "mesh_lookup": 1, "search_papers": 3, "fetch_pmc_fulltext": 1,
    }


async def test_usage_today_empty(
    live_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No done events yet → zero usage but quota gauge shape is well-formed."""
    monkeypatch.setenv("MAX_INPUT_TOKENS_PER_DAY", "10000")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS_PER_DAY", "2000")

    resp = await live_client.get("/api/threads/usage/today")
    assert resp.status_code == 200
    data = resp.json()

    assert data["input_tokens"] == {
        "used": 0, "limit": 10_000, "remaining": 10_000, "percent": 0.0,
    }
    assert data["output_tokens"] == {
        "used": 0, "limit": 2_000, "remaining": 2_000, "percent": 0.0,
    }
    assert data["enforcement_enabled"] is True
    assert "day_start_utc" in data
    assert 0.0 <= data["hours_until_reset"] <= 24.0


async def test_usage_today_aggregates_today_only(
    live_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seeds one done event today; the endpoint reports it against the cap."""
    monkeypatch.setenv("MAX_INPUT_TOKENS_PER_DAY", "10000")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS_PER_DAY", "2000")

    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.repository import ThreadRepository

    async with get_db_session() as session:
        repo = ThreadRepository(session)
        thread = await repo.create_thread(title="Quota seed")
        msg = await repo.add_message(
            thread_id=thread.id, role="assistant", final_answer="{}"
        )
        await repo.add_stream_event(
            message_id=msg.id, event_type="done", sequence_num=0,
            data={"usage": {"input_tokens": 2500, "output_tokens": 500}},
        )

    resp = await live_client.get("/api/threads/usage/today")
    assert resp.status_code == 200
    data = resp.json()
    assert data["input_tokens"]["used"] == 2500
    assert data["input_tokens"]["remaining"] == 7500
    assert data["input_tokens"]["percent"] == 25.0
    assert data["output_tokens"]["used"] == 500
    assert data["output_tokens"]["percent"] == 25.0


async def test_errored_turn_persists_done_event(
    live_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a turn whose dispatch() raises must still produce a
    `done` stream event and a placeholder assistant message — otherwise
    failed turns are invisible to the usage page and quota counter.
    """
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.repository import ThreadRepository

    # Make the dispatcher blow up the moment it's called.
    async def _raise(*_: object, **__: object) -> None:
        raise RuntimeError("simulated agent failure")

    monkeypatch.setattr(
        "research_assistant.web.dispatch.dispatch", _raise
    )

    # Seed a thread and fire a turn against it.
    create = await live_client.post("/api/threads", json={"title": "errored-turn"})
    thread_id = create.json()["id"]

    turn = await live_client.post(
        "/api/turn",
        json={"thread_id": thread_id, "user_message": "make it fail"},
    )
    assert turn.status_code == 500
    assert "simulated agent failure" in turn.json()["detail"]

    # The endpoint should have written a placeholder assistant message AND
    # a `done` event so the failed turn is visible to /usage/monthly.
    async with get_db_session() as session:
        repo = ThreadRepository(session)
        events = await repo.get_done_events_since(
            (await repo.get_thread(thread_id)).created_at  # type: ignore[union-attr]
        )

    assert len(events) == 1, "exactly one done event should have been written"
    import json as _json
    data = _json.loads(events[0].data)
    assert data["workflow"] == "errored"
    assert "simulated agent failure" in data["error"]
    assert data["usage"]["input_tokens"] == 0  # unknown on failure

    # Usage page must surface the failed turn in its question count.
    monthly = await live_client.get("/api/threads/usage/monthly")
    assert monthly.json()["questions"] == 1


# ── Admin: paper-source config ───────────────────────────────────────────


async def test_list_sources_seeded(live_client: AsyncClient) -> None:
    """init_db seeds pubmed + europepmc; admin endpoint returns both."""
    resp = await live_client.get("/api/admin/sources")
    assert resp.status_code == 200
    sources = resp.json()
    ids = {s["id"] for s in sources}
    assert ids == {"pubmed", "europepmc"}
    pubmed = next(s for s in sources if s["id"] == "pubmed")
    assert pubmed["enabled"] is True
    assert pubmed["display_name"] == "PubMed (NCBI E-utilities)"


async def test_update_source_changes_fields(live_client: AsyncClient) -> None:
    resp = await live_client.put(
        "/api/admin/sources/pubmed",
        json={"api_key": "test-key-123", "enabled": False, "max_retries": 7},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["api_key"] == "test-key-123"
    assert updated["enabled"] is False
    assert updated["max_retries"] == 7

    # Round-trip — GET reflects the saved row.
    listing = (await live_client.get("/api/admin/sources")).json()
    pubmed = next(s for s in listing if s["id"] == "pubmed")
    assert pubmed["api_key"] == "test-key-123"
    assert pubmed["enabled"] is False


async def test_update_source_partial_fields_preserved(
    live_client: AsyncClient,
) -> None:
    """Omitted fields stay unchanged (exclude_unset semantics)."""
    before = (await live_client.get("/api/admin/sources")).json()
    europepmc_before = next(s for s in before if s["id"] == "europepmc")
    original_retries = europepmc_before["max_retries"]

    resp = await live_client.put(
        "/api/admin/sources/europepmc",
        json={"contact_email": "me@example.com"},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["contact_email"] == "me@example.com"
    # max_retries was not in the body — must be unchanged.
    assert updated["max_retries"] == original_retries


async def test_update_source_clear_via_null(live_client: AsyncClient) -> None:
    """Explicit null clears a nullable field."""
    await live_client.put(
        "/api/admin/sources/pubmed",
        json={"api_key": "to-be-cleared"},
    )
    resp = await live_client.put(
        "/api/admin/sources/pubmed",
        json={"api_key": None},
    )
    assert resp.status_code == 200
    assert resp.json()["api_key"] is None


async def test_update_source_not_found(live_client: AsyncClient) -> None:
    resp = await live_client.put(
        "/api/admin/sources/does-not-exist",
        json={"enabled": False},
    )
    assert resp.status_code == 404


async def test_update_source_rejects_unknown_fields(
    live_client: AsyncClient,
) -> None:
    """`extra='forbid'` on the update schema."""
    resp = await live_client.put(
        "/api/admin/sources/pubmed",
        json={"made_up_field": "x"},
    )
    assert resp.status_code == 422
