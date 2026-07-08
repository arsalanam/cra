"""RateLimitedClient transient-failure retry (Phase-4 audit, item 11).

One upstream blip (5xx, connect/read timeout) must not surface as a tool
error — those count against the per-turn tool-error circuit breaker. The
client retries 429 / 5xx / transport errors with backoff and gives up
after `max_retries`, raising so the source wraps it as `{"error": ...}`.

Backoff is set to 0 in these tests so they run instantly.
"""

from __future__ import annotations

import httpx
import pytest

from research_assistant.config.rate_limit import RateLimitConfig, RateLimitedClient


def _cfg(max_retries: int = 2) -> RateLimitConfig:
    return RateLimitConfig(
        name="test-source",
        base_url="https://api.test/v1",
        max_retries=max_retries,
        backoff_base_sec=0.0,
        backoff_cap_sec=0.0,
    )


class _ScriptedTransport:
    """Return (or raise) each scripted item in order; repeat the last one."""

    def __init__(self, *script: httpx.Response | Exception) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


async def _get(handler: _ScriptedTransport, max_retries: int = 2) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = RateLimitedClient(http, _cfg(max_retries))
        return await client.get("search", {"q": "statins"})


async def test_transient_5xx_is_retried_then_succeeds() -> None:
    handler = _ScriptedTransport(
        httpx.Response(502),
        httpx.Response(502),
        httpx.Response(200, json={"ok": True}),
    )
    resp = await _get(handler)
    assert resp.status_code == 200
    assert handler.calls == 3


async def test_persistent_5xx_raises_after_budget() -> None:
    handler = _ScriptedTransport(httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await _get(handler, max_retries=2)
    assert handler.calls == 3  # initial + 2 retries


async def test_transport_error_is_retried_then_succeeds() -> None:
    handler = _ScriptedTransport(
        httpx.ConnectError("connection refused"),
        httpx.Response(200, json={"ok": True}),
    )
    resp = await _get(handler)
    assert resp.status_code == 200
    assert handler.calls == 2


async def test_persistent_transport_error_reraises() -> None:
    handler = _ScriptedTransport(httpx.ReadTimeout("read timed out"))
    with pytest.raises(httpx.ReadTimeout):
        await _get(handler, max_retries=2)
    assert handler.calls == 3


async def test_non_retryable_4xx_raises_immediately() -> None:
    handler = _ScriptedTransport(httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await _get(handler)
    assert handler.calls == 1  # no retries for a stable client error


async def test_429_still_retried_with_retry_after() -> None:
    handler = _ScriptedTransport(
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"ok": True}),
    )
    resp = await _get(handler)
    assert resp.status_code == 200
    assert handler.calls == 2
