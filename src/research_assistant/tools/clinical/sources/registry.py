"""Active paper-source registry — backed by `SourceConfigService`.

The registry owns the source-id → concrete-class mapping. To add a new
source: implement a `PaperSource` class, add an entry here, add base-URL
+ api-key-param entries to `config.service`, and seed a default
`SourceConfig` row in `persistence.database._SOURCE_CONFIG_DEFAULTS`.
"""

from __future__ import annotations

from collections.abc import Callable

from ....config.rate_limit import RateLimitConfig
from ....config.service import get_source_config_service
from .base import PaperSource
from .europepmc import EuropePMCSource
from .pubmed import PubmedSource

# id → builder that takes a RateLimitConfig and returns a PaperSource.
# Using Callable instead of `type[PaperSource]` because the PaperSource
# Protocol only defines runtime methods; mypy can't see the concrete
# class constructors through it.
_BUILDERS: dict[str, Callable[[RateLimitConfig], PaperSource]] = {
    "pubmed": PubmedSource,
    "europepmc": EuropePMCSource,
}


async def get_enabled_sources() -> list[PaperSource]:
    """Every enabled PaperSource, ordered by source id."""
    svc = get_source_config_service()
    sources: list[PaperSource] = []
    for hs in await svc.list_enabled():
        builder = _BUILDERS.get(hs.config.id)
        if builder is not None:
            sources.append(builder(hs.rate_config))
    return sources


async def get_pubmed_source() -> PubmedSource | None:
    """PubMed-specific accessor for `mesh_lookup` / `fetch_pmc_fulltext`.

    Returns None if PubMed is disabled in the admin config — the caller
    is responsible for surfacing a sensible error to the agent.
    """
    svc = get_source_config_service()
    hs = await svc.get("pubmed")
    if hs is None:
        return None
    return PubmedSource(hs.rate_config)
