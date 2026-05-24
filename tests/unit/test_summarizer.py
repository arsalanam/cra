"""Unit tests for the stub thread summarizer."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.repository import ThreadRepository
from research_assistant.persistence.summarizer import StubThreadSummarizer


async def test_should_not_summarize_below_threshold(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    for i in range(5):
        await repo.add_message(thread.id, role="user", input_text=f"msg {i}")

    summarizer = StubThreadSummarizer(threshold=10)
    assert await summarizer.should_summarize(thread.id, db_session) is False


async def test_should_summarize_at_threshold(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    for i in range(10):
        await repo.add_message(thread.id, role="user", input_text=f"msg {i}")

    summarizer = StubThreadSummarizer(threshold=10)
    assert await summarizer.should_summarize(thread.id, db_session) is True


async def test_summarize_and_truncate_sets_summary(db_session: AsyncSession) -> None:
    repo = ThreadRepository(db_session)
    thread = await repo.create_thread()
    for i in range(5):
        await repo.add_message(thread.id, role="user", input_text=f"msg {i}")

    summarizer = StubThreadSummarizer(threshold=3)
    summary = await summarizer.summarize_and_truncate(thread.id, db_session)
    assert "5 messages" in summary

    refreshed = await repo.get_thread(thread.id)
    assert refreshed is not None
    assert refreshed.summary is not None
    assert "5 messages" in refreshed.summary
