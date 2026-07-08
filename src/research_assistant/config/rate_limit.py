"""Generic rate-limited HTTP client.

Wraps `httpx.AsyncClient.get` with:
  • Per-source credential injection via a pluggable `AuthStrategy`
    (query-param, header, bearer token, …) — see `config/auth.py`.
  • Per-source common-param injection (NCBI's tool/email etiquette, etc.)
  • Automatic retry with exponential backoff on transient failures:
    429 (Retry-After honoured), 5xx, and transport errors (connect /
    read timeouts, resets). One upstream blip must not surface as a tool
    error — those count against the per-turn tool-error circuit breaker.

Per-source backends in `tools/clinical/sources/` instantiate one of these
with their own `RateLimitConfig`. The DB-backed `SourceConfig` row +
`config.service._rate_config_for` produce the config at runtime so an
admin can edit credentials without redeploying.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from .auth import AuthStrategy, NoAuth

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitConfig:
    """Per-source HTTP throttle + credential strategy."""

    name: str  # human-readable label for log lines
    base_url: str  # e.g. "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    common_params: dict[str, str] = field(default_factory=dict)
    auth: AuthStrategy = field(default_factory=NoAuth)
    max_retries: int = 3
    backoff_base_sec: float = 1.0
    backoff_cap_sec: float = 10.0


# Statuses worth retrying: rate limiting plus transient upstream failures.
# 4xx other than 429 (bad query, auth, not-found) will not improve on retry.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class RateLimitedClient:
    """Wraps httpx.AsyncClient with transient-failure retry for one paper source."""

    def __init__(self, client: httpx.AsyncClient, config: RateLimitConfig) -> None:
        self._client = client
        self._cfg = config

    def _prepare(self, extra: dict[str, str | int]) -> tuple[dict[str, str], dict[str, str]]:
        """Build (params, headers) for an outgoing request after auth injection."""
        params: dict[str, str] = {**self._cfg.common_params}
        params.update({k: str(v) for k, v in extra.items()})
        headers: dict[str, str] = {}
        self._cfg.auth.apply(params, headers)
        return params, headers

    async def get(
        self,
        endpoint: str,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        """GET `<base_url>/<endpoint>` with retry-on-transient-failure backoff.

        Retries 429 (Retry-After honoured), 5xx, and transport errors
        (connect/read timeouts, resets) up to `max_retries` times. Raises
        `httpx.HTTPStatusError` on non-retryable statuses immediately and on
        retryable statuses once the budget is exhausted; re-raises the final
        transport error likewise.
        """
        full_params, headers = self._prepare(params or {})
        url = f"{self._cfg.base_url}/{endpoint}"

        for attempt in range(self._cfg.max_retries + 1):
            last_attempt = attempt == self._cfg.max_retries
            try:
                resp = await self._client.get(url, params=full_params, headers=headers)
            except httpx.TransportError as e:
                # Connect/read timeout, reset, DNS blip — transient by nature.
                if last_attempt:
                    logger.warning(
                        "%s %s transport error after %d retries — giving up: %s",
                        self._cfg.name,
                        endpoint,
                        attempt,
                        e,
                    )
                    raise
                wait = _compute_backoff(
                    None, self._cfg.backoff_base_sec, attempt, self._cfg.backoff_cap_sec
                )
                logger.info(
                    "%s %s transport error (attempt %d) — backing off %.1fs: %s",
                    self._cfg.name,
                    endpoint,
                    attempt + 1,
                    wait,
                    e,
                )
                await asyncio.sleep(wait)
                continue

            if resp.status_code not in _RETRYABLE_STATUSES:
                resp.raise_for_status()
                return resp

            if last_attempt:
                logger.warning(
                    "%s %s HTTP %d after %d retries — giving up%s",
                    self._cfg.name,
                    endpoint,
                    resp.status_code,
                    attempt,
                    ""
                    if resp.status_code != 429 or self._cfg.auth.has_credentials
                    else " (consider configuring credentials)",
                )
                resp.raise_for_status()  # raises HTTPStatusError

            wait = _compute_backoff(
                resp.headers.get("Retry-After"),
                self._cfg.backoff_base_sec,
                attempt,
                self._cfg.backoff_cap_sec,
            )
            logger.info(
                "%s %s HTTP %d (attempt %d) — backing off %.1fs",
                self._cfg.name,
                endpoint,
                resp.status_code,
                attempt + 1,
                wait,
            )
            await asyncio.sleep(wait)

        raise AssertionError("unreachable — the loop always returns or raises")


def _compute_backoff(
    retry_after_header: str | None,
    base_sec: float,
    attempt: int,
    cap_sec: float,
) -> float:
    if retry_after_header:
        try:
            return min(float(retry_after_header), cap_sec)
        except ValueError:
            pass
    return float(min(base_sec * (2**attempt), cap_sec))
