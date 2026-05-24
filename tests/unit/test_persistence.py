"""Unit tests for the persistence layer (repository, models, context)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.context import messages_to_history
from research_assistant.persistence.models import Message
from research_assistant.persistence.repository import ThreadRepository

# ── Thread CRUD ───────────────────────────────────────────────────────────


async def test_create_thread(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread(title="Test thread")
    assert thread.id is not None
    assert thread.title == "Test thread"
    assert thread.summary is None


async def test_list_threads_empty(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    threads = await repo.list_threads()
    assert threads == []


async def test_list_threads_ordered(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    await repo.create_thread(title="First")
    t2 = await repo.create_thread(title="Second")
    threads = await repo.list_threads()
    assert len(threads) == 2
    assert threads[0].id == t2.id


async def test_get_thread(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    created = await repo.create_thread(title="Find me")
    found = await repo.get_thread(created.id)
    assert found is not None
    assert found.title == "Find me"


async def test_get_thread_not_found(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    assert await repo.get_thread("nonexistent") is None


async def test_delete_thread(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread(title="Delete me")
    assert await repo.delete_thread(thread.id) is True
    assert await repo.get_thread(thread.id) is None


async def test_delete_nonexistent(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    assert await repo.delete_thread("nope") is False


async def test_update_thread(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread(title="Old title")
    updated = await repo.update_thread(thread.id, title="New title", summary="A summary")
    assert updated is not None
    assert updated.title == "New title"
    assert updated.summary == "A summary"


# ── Messages ──────────────────────────────────────────────────────────────


async def test_add_and_get_messages(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    await repo.add_message(thread.id, role="user", input_text="Hello")
    await repo.add_message(thread.id, role="assistant", final_answer="Hi there")

    messages = await repo.get_messages(thread.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].input_text == "Hello"
    assert messages[1].role == "assistant"
    assert messages[1].final_answer == "Hi there"


async def test_get_messages_with_limit(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    for i in range(10):
        await repo.add_message(thread.id, role="user", input_text=f"msg {i}")

    recent = await repo.get_messages(thread.id, limit=3)
    assert len(recent) == 3
    assert recent[0].input_text == "msg 7"
    assert recent[2].input_text == "msg 9"


async def test_count_messages(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    assert await repo.count_messages(thread.id) == 0
    await repo.add_message(thread.id, role="user", input_text="one")
    await repo.add_message(thread.id, role="user", input_text="two")
    assert await repo.count_messages(thread.id) == 2


async def test_set_final_answer(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    msg = await repo.add_message(thread.id, role="assistant")
    assert msg.final_answer is None
    await repo.set_final_answer(msg.id, "The answer is 42")
    refreshed = await repo.get_messages(thread.id)
    assert refreshed[0].final_answer == "The answer is 42"


# ── Stream Events ─────────────────────────────────────────────────────────


async def test_add_and_get_stream_events(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    msg = await repo.add_message(thread.id, role="assistant")
    await repo.add_stream_event(msg.id, "text_delta", {"content": "Hello"}, 0)
    await repo.add_stream_event(msg.id, "done", {"usage": {}}, 1)

    events = await repo.get_stream_events(msg.id)
    assert len(events) == 2
    assert events[0].event_type == "text_delta"
    assert events[0].sequence_num == 0
    assert json.loads(events[0].data)["content"] == "Hello"
    assert events[1].event_type == "done"


async def test_get_done_events_since(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    msg = await repo.add_message(thread.id, role="assistant")
    await repo.add_stream_event(msg.id, "text_delta", {"content": "x"}, 0)
    await repo.add_stream_event(
        msg.id, "done",
        {"usage": {"input_tokens": 100, "output_tokens": 50}},
        1,
    )

    since = datetime(2000, 1, 1, tzinfo=UTC)
    done_events = await repo.get_done_events_since(since)
    assert len(done_events) == 1
    data = json.loads(done_events[0].data)
    assert data["usage"]["input_tokens"] == 100


async def test_count_events_by_type_since(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    msg = await repo.add_message(thread.id, role="assistant")
    await repo.add_stream_event(msg.id, "text_delta", {}, 0)
    await repo.add_stream_event(msg.id, "text_delta", {}, 1)
    await repo.add_stream_event(msg.id, "tool_start", {}, 2)
    await repo.add_stream_event(msg.id, "done", {}, 3)

    since = datetime(2000, 1, 1, tzinfo=UTC)
    counts = await repo.count_events_by_type_since(since)
    assert counts["text_delta"] == 2
    assert counts["tool_start"] == 1
    assert counts["done"] == 1


# ── Cascade Delete ────────────────────────────────────────────────────────


async def test_delete_thread_cascades(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    msg = await repo.add_message(thread.id, role="user", input_text="test")
    await repo.add_stream_event(msg.id, "text_delta", {"content": "x"}, 0)

    await repo.delete_thread(thread.id)
    assert await repo.get_messages(thread.id) == []
    assert await repo.get_stream_events(msg.id) == []


# ── Context Conversion ───────────────────────────────────────────────────


def test_messages_to_history_basic() -> None:
    now = datetime.now(UTC)
    messages = [
        Message(id="1", thread_id="t", role="user", input_text="What is 2+2?", created_at=now),
        Message(id="2", thread_id="t", role="assistant", final_answer="4", created_at=now),
    ]
    history = messages_to_history(messages)
    assert len(history) == 2
    assert history[0].parts[0].content == "What is 2+2?"
    assert history[1].parts[0].content == "4"


def test_messages_to_history_with_summary() -> None:
    history = messages_to_history([], thread_summary="Previously discussed weather.")
    assert len(history) == 1
    assert "Previously discussed weather" in history[0].parts[0].content


def test_messages_to_history_skips_empty() -> None:
    now = datetime.now(UTC)
    messages = [
        Message(id="1", thread_id="t", role="assistant", final_answer=None, created_at=now),
    ]
    history = messages_to_history(messages)
    assert len(history) == 0
