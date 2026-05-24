"""Generic rate-limited HTTP client.

Wraps `httpx.AsyncClient.get` with:
  • Per-source credential injection via a pluggable `AuthStrategy`
    (query-param, header, bearer token, …) — see `config/auth.py`.
  • Per-source common-param injection (NCBI's tool/email etiquette, etc.)
  • Automatic retry on 429 with exponential backoff (Retry-After honoured)

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


class RateLimitedClient:
    """Wraps httpx.AsyncClient with retry-on-429 for one paper source."""

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
        """GET `<base_url>/<endpoint>` with retry-on-429 backoff.

        Raises `httpx.HTTPStatusError` on non-429 errors and after the retry
        budget is exhausted.
        """
        full_params, headers = self._prepare(params or {})
        url = f"{self._cfg.base_url}/{endpoint}"

        last_response: httpx.Response | None = None
        for attempt in range(self._cfg.max_retries + 1):
            resp = await self._client.get(url, params=full_params, headers=headers)
            last_response = resp
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp

            if attempt == self._cfg.max_retries:
                logger.warning(
                    "%s %s 429 after %d retries — giving up%s",
                    self._cfg.name,
                    endpoint,
                    attempt,
                    "" if self._cfg.auth.has_credentials else " (consider configuring credentials)",
                )
                resp.raise_for_status()  # raises HTTPStatusError

            wait = _compute_backoff(
                resp.headers.get("Retry-After"),
                self._cfg.backoff_base_sec,
                attempt,
                self._cfg.backoff_cap_sec,
            )
            logger.info(
                "%s %s 429 (attempt %d) — backing off %.1fs",
                self._cfg.name,
                endpoint,
                attempt + 1,
                wait,
            )
            await asyncio.sleep(wait)

        # Unreachable — raise_for_status() inside the loop will have fired.
        assert last_response is not None
        last_response.raise_for_status()
        return last_response


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
