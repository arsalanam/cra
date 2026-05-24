"""
web_search tool — Tavily (optimised for AI agents).
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...config import get_settings
from .._emit import emit_run

logger = logging.getLogger(__name__)


async def _impl(query: str, max_results: int = 5) -> str:
    """Search the web via Tavily and return formatted results with content."""
    try:
        from tavily import AsyncTavilyClient

        settings = get_settings()
        client = AsyncTavilyClient(api_key=settings.tavily_api_key)
        response = await client.search(
            query=query,
            max_results=max_results,
            include_answer=True,
        )

        parts: list[str] = []

        answer = response.get("answer")
        if answer:
            parts.append(f"AI Summary: {answer}\n")

        results = response.get("results", [])
        if not results and not answer:
            return f"No results found for query: {query!r}"

        for i, hit in enumerate(results, 1):
            content = hit.get("content", "")[:500]
            parts.append(
                f"[{i}] {hit.get('title', 'No title')}\n"
                f"    URL: {hit.get('url', '')}\n"
                f"    {content}"
            )
        return "\n\n".join(parts)

    except Exception as e:
        logger.error("Web search failed for query=%r: %s", query, e, exc_info=True)
        return f"Search error: {e}. Try rephrasing your query."


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def web_search(ctx: RunContext[AgentDeps], query: str) -> str:
        """
        Search the web using Tavily. Returns the top 5 results with titles,
        URLs, and extracted page content. Use for: current events, recent data,
        specific facts not on Wikipedia, company info, news.
        """
        return await emit_run(
            ctx,
            tool="web_search",
            icon="🔍",
            args={"query": query},
            description=f'Searching: "{query}"',
            impl=lambda: _impl(query),
        )
