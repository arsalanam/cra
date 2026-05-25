"""
wikipedia tool — fetch encyclopedic summaries.
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from .._emit import emit_run


async def _impl(topic: str, sentences: int = 5) -> str:
    """Fetch a Wikipedia article summary for the given topic."""
    try:
        import wikipediaapi

        def _sync_wiki() -> dict[str, str] | None:
            wiki = wikipediaapi.Wikipedia(
                language="en",
                user_agent="PydanticAI-ReACT-Tutorial/1.0",
            )
            page = wiki.page(topic)
            if not page.exists():
                return None
            summary = page.summary
            sent_list = summary.split(". ")
            truncated = ". ".join(sent_list[:sentences])
            if not truncated.endswith("."):
                truncated += "."
            return {
                "title": page.title,
                "url": page.fullurl,
                "summary": truncated,
            }

        result = await asyncio.to_thread(_sync_wiki)

        if result is None:
            return (
                f"No Wikipedia article found for {topic!r}. "
                "Try a different spelling or use web_search instead."
            )

        return f"Wikipedia: {result['title']}\nURL: {result['url']}\n\n{result['summary']}"

    except Exception as e:
        return f"Wikipedia error: {e}"


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def wikipedia(ctx: RunContext[AgentDeps], topic: str) -> str:
        """
        Look up a topic on Wikipedia. Returns a factual summary (first 5 sentences)
        plus the article URL. Use for: encyclopedic facts, historical events,
        biographies, scientific concepts, geographic data.
        """
        return await emit_run(
            ctx,
            tool="wikipedia",
            icon="📖",
            args={"topic": topic},
            description=f'Looking up Wikipedia: "{topic}"',
            impl=lambda: _impl(topic),
        )
