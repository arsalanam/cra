"""JWKS fetch + cache for Cognito's public signing keys.

Cognito publishes its JSON Web Key Set at:
    https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/jwks.json

Keys are stable for the lifetime of the pool but Cognito can rotate them.
We cache the JWKS on first use and re-fetch on a `kid` miss (the only way
key rotation manifests in practice for OIDC clients).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class JwksCache:
    """In-process JWKS cache for one Cognito user pool.

    Thread-safe-ish: the asyncio.Lock prevents the thundering-herd of
    concurrent first-fetches that happens at app startup when many
    requests arrive before the cache is warm.
    """

    def __init__(self, region: str, user_pool_id: str) -> None:
        self._region = region
        self._user_pool_id = user_pool_id
        self._keys_by_kid: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def jwks_url(self) -> str:
        return (
            f"https://cognito-idp.{self._region}.amazonaws.com/"
            f"{self._user_pool_id}/.well-known/jwks.json"
        )

    async def get_key(self, kid: str) -> dict[str, Any]:
        """Return the JWK for `kid`, refreshing on miss.

        Raises KeyError if the kid is still unknown after a refresh.
        """
        if kid in self._keys_by_kid:
            return self._keys_by_kid[kid]
        async with self._lock:
            if kid in self._keys_by_kid:  # double-check after lock
                return self._keys_by_kid[kid]
            await self._refresh()
        if kid not in self._keys_by_kid:
            raise KeyError(f"kid {kid!r} not found in Cognito JWKS even after refresh")
        return self._keys_by_kid[kid]

    async def _refresh(self) -> None:
        logger.info("Refreshing Cognito JWKS from %s", self.jwks_url)
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(self.jwks_url)
            resp.raise_for_status()
            payload = resp.json()
        keys = payload.get("keys") or []
        self._keys_by_kid = {k["kid"]: k for k in keys if "kid" in k}
        logger.info("JWKS refreshed: %d keys cached", len(self._keys_by_kid))

    def seed_for_testing(self, keys: list[dict[str, Any]]) -> None:
        """Test hook — populate the cache without hitting the network."""
        self._keys_by_kid = {k["kid"]: k for k in keys if "kid" in k}


_cache: JwksCache | None = None


def get_jwks_cache(region: str, user_pool_id: str) -> JwksCache:
    """Process-wide singleton (rebuilt if region/pool changes — e.g. in tests)."""
    global _cache
    if _cache is None or (
        _cache._region != region or _cache._user_pool_id != user_pool_id
    ):
        _cache = JwksCache(region=region, user_pool_id=user_pool_id)
    return _cache


def reset_jwks_cache_for_testing() -> None:
    """Drop the cached singleton — tests use this between assertions."""
    global _cache
    _cache = None
