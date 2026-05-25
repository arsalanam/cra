"""DB-backed runtime config service for paper sources.

Each `SourceConfig` row in the database is an admin-editable overlay on
top of the static `.env` defaults. This service hydrates rows into the
`RateLimitConfig` shape that paper-source backends consume.

The concrete `PaperSource` classes (PubmedSource, EuropePMCSource) are
NOT imported here on purpose — `tools/clinical/sources/registry.py` owns
the id → builder mapping. That keeps `config/` free of cross-package
imports and avoids a circular dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..persistence.database import get_db_session
from ..persistence.models import SourceConfig
from .auth import AuthStrategy, NoAuth, QueryParamAuth
from .rate_limit import RateLimitConfig
from .settings import get_settings


@dataclass(frozen=True)
class HydratedSource:
    """A DB SourceConfig row paired with its computed RateLimitConfig."""

    config: SourceConfig
    rate_config: RateLimitConfig


_BASE_URLS: dict[str, str] = {
    "pubmed": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
    "europepmc": "https://www.ebi.ac.uk/europepmc/webservices/rest",
}


# Per-source auth-strategy builders. Each callable receives the resolved
# credential string (DB row → env fallback) and returns the right strategy.
# Adding a new source = add one entry here (plus base URL + DB seed).
def _pubmed_auth(key: str) -> AuthStrategy:
    return QueryParamAuth("api_key", key) if key else NoAuth()


def _no_auth(_key: str) -> AuthStrategy:
    return NoAuth()


_AUTH_BUILDERS: dict[str, Callable[[str], AuthStrategy]] = {
    "pubmed": _pubmed_auth,
    "europepmc": _no_auth,
    # Examples for future paid sources:
    # "embase":  lambda key: HeaderAuth("X-ELS-APIKey", key) if key else NoAuth(),
    # "scopus":  lambda token: BearerAuth(token) if token else NoAuth(),
}

# Static per-source tool/contact-email etiquette parameters that travel
# on every request. NCBI asks for `tool=` + `email=`; Europe PMC has no
# such convention.
_TOOL_NAME = "PydanticAI-Clinical/1.0"
_DEFAULT_EMAIL = "research@example.com"


def _rate_config_for(cfg: SourceConfig) -> RateLimitConfig:
    """Merge DB row + env defaults into a RateLimitConfig."""
    settings = get_settings()
    base_url = _BASE_URLS.get(cfg.id, "")

    credential = cfg.api_key or ""
    if cfg.id == "pubmed" and not credential:
        credential = settings.ncbi_api_key

    common: dict[str, str] = {}
    if cfg.id == "pubmed":
        common["tool"] = _TOOL_NAME
        common["email"] = cfg.contact_email or _DEFAULT_EMAIL

    auth = _AUTH_BUILDERS.get(cfg.id, _no_auth)(credential)

    return RateLimitConfig(
        name=cfg.id,
        base_url=base_url,
        common_params=common,
        auth=auth,
        max_retries=cfg.max_retries,
        backoff_base_sec=cfg.backoff_base_sec,
        backoff_cap_sec=cfg.backoff_cap_sec,
    )


class SourceConfigService:
    """Read paper-source configs from the DB and hydrate them with env defaults."""

    async def list_configs(self, session: AsyncSession | None = None) -> list[SourceConfig]:
        """All source rows, ordered by id. Pass `session` to reuse one."""
        if session is not None:
            stmt = select(SourceConfig).order_by(SourceConfig.id)
            result = await session.execute(stmt)
            return list(result.scalars().all())
        async with get_db_session() as s:
            stmt = select(SourceConfig).order_by(SourceConfig.id)
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def list_enabled(self, session: AsyncSession | None = None) -> list[HydratedSource]:
        """Enabled rows only, hydrated with a built RateLimitConfig."""
        rows = await self.list_configs(session)
        return [
            HydratedSource(config=r, rate_config=_rate_config_for(r))
            for r in rows
            if r.enabled and r.id in _BASE_URLS
        ]

    async def get(
        self, source_id: str, session: AsyncSession | None = None
    ) -> HydratedSource | None:
        """Single enabled source by id (None if disabled or missing)."""
        for hs in await self.list_enabled(session):
            if hs.config.id == source_id:
                return hs
        return None


_service: SourceConfigService | None = None


def get_source_config_service() -> SourceConfigService:
    """Module-level singleton accessor."""
    global _service
    if _service is None:
        _service = SourceConfigService()
    return _service
