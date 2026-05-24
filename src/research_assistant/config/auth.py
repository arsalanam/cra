"""Pluggable auth strategies for paper-source HTTP calls.

Each `PaperSource` issues requests through `RateLimitedClient`. Different
sources need different credential injection schemes:

  • NCBI/PubMed     — query-param  (?api_key=...)
  • Europe PMC      — none
  • Embase (Elsevier) — header     (X-ELS-APIKey)
  • Scopus / WoS / Cochrane (some tiers) — Bearer token

Rather than bake one of these into `RateLimitConfig`, the config carries an
`AuthStrategy` and `RateLimitedClient` delegates to it. Adding a new auth
scheme is one new dataclass here plus one wiring entry in `config/service.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class AuthStrategy(Protocol):
    """Pluggable credential injection for a paper-source HTTP call."""

    def apply(self, params: dict[str, str], headers: dict[str, str]) -> None:
        """Mutate `params` / `headers` in place to add credentials.

        Called once per outgoing request, after common-params merge but
        before the request fires. No-op for strategies without credentials.
        """
        ...

    @property
    def has_credentials(self) -> bool:
        """True iff this strategy is currently carrying live credentials.

        Used by the rate-limit client to tune 429 warnings ("consider
        configuring an API key" only makes sense when there isn't one).
        """
        ...


@dataclass(frozen=True)
class NoAuth:
    """No credentials sent. Default for sources like Europe PMC."""

    def apply(self, params: dict[str, str], headers: dict[str, str]) -> None:
        return

    @property
    def has_credentials(self) -> bool:
        return False


@dataclass(frozen=True)
class QueryParamAuth:
    """Key injected as a query parameter (e.g. NCBI's `?api_key=...`)."""

    param: str
    key: str

    def apply(self, params: dict[str, str], headers: dict[str, str]) -> None:
        if self.key:
            params[self.param] = self.key

    @property
    def has_credentials(self) -> bool:
        return bool(self.key)


@dataclass(frozen=True)
class HeaderAuth:
    """Key injected as a custom header (e.g. Elsevier's `X-ELS-APIKey`)."""

    header: str
    key: str

    def apply(self, params: dict[str, str], headers: dict[str, str]) -> None:
        if self.key:
            headers[self.header] = self.key

    @property
    def has_credentials(self) -> bool:
        return bool(self.key)


@dataclass(frozen=True)
class BearerAuth:
    """OAuth-style bearer token in the Authorization header."""

    token: str

    def apply(self, params: dict[str, str], headers: dict[str, str]) -> None:
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

    @property
    def has_credentials(self) -> bool:
        return bool(self.token)
