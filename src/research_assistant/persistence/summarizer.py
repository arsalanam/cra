"""Stub thread summarizer — replace with an LLM-based implementation later."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from .repository import ThreadRepository


class ThreadSummarizer(Protocol):
    """Interface for thread summarisation strategies."""

    async def should_summarize(self, thread_id: str, session: AsyncSession) -> bool: ...
    async def summarize_and_truncate(self, thread_id: str, session: AsyncSession) -> str: ...


class StubThreadSummarizer:
    """Counts messages and produces a placeholder summary.

    A real implementation would call the LLM to distil the conversation
    before deleting the oldest messages.
    """

    def __init__(self, threshold: int = 500) -> None:
        self.threshold = threshold

    async def should_summarize(self, thread_id: str, session: AsyncSession) -> bool:
        repo = ThreadRepository(session)
        count = await repo.count_messages(thread_id)
        return count >= self.threshold

    async def summarize_and_truncate(self, thread_id: str, session: AsyncSession) -> str:
        repo = ThreadRepository(session)
        count = await repo.count_messages(thread_id)

        summary = (
            f"[Stub summary] This thread contained {count} messages. "
            "A production summarizer would distil the key topics, decisions, "
            "and unresolved questions using an LLM call, then delete older "
            "messages to free context space."
        )

        await repo.update_thread(thread_id, summary=summary)
        return summary
