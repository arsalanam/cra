"""Unit tests for `config.service.SourceConfigService`."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.config.auth import NoAuth, QueryParamAuth
from research_assistant.config.service import (
    SourceConfigService,
    _rate_config_for,
)
from research_assistant.persistence.models import SourceConfig


async def _seed(session: AsyncSession, **overrides: object) -> SourceConfig:
    defaults = {
        "id": "pubmed",
        "display_name": "PubMed",
        "enabled": True,
        "api_key": None,
        "contact_email": None,
        "max_retries": 3,
        "backoff_base_sec": 1.0,
        "backoff_cap_sec": 10.0,
    }
    defaults.update(overrides)
    row = SourceConfig(**defaults)
    session.add(row)
    await session.flush()
    return row


async def test_list_configs_returns_all(db_session: AsyncSession) -> None:
    await _seed(db_session, id="pubmed")
    await _seed(db_session, id="europepmc", display_name="Europe PMC")

    svc = SourceConfigService()
    rows = await svc.list_configs(db_session)
    assert [r.id for r in rows] == ["europepmc", "pubmed"]  # ordered by id


async def test_list_enabled_filters_disabled(db_session: AsyncSession) -> None:
    await _seed(db_session, id="pubmed", enabled=True)
    await _seed(db_session, id="europepmc", enabled=False)

    svc = SourceConfigService()
    enabled = await svc.list_enabled(db_session)
    assert [h.config.id for h in enabled] == ["pubmed"]


async def test_get_returns_none_when_disabled(db_session: AsyncSession) -> None:
    await _seed(db_session, id="pubmed", enabled=False)
    svc = SourceConfigService()
    assert await svc.get("pubmed", db_session) is None


async def test_rate_config_for_pubmed_uses_env_when_db_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NCBI_API_KEY", "env-key-123")
    cfg = SourceConfig(
        id="pubmed",
        display_name="PubMed",
        enabled=True,
        api_key=None,
        contact_email=None,
    )
    rate = _rate_config_for(cfg)
    assert isinstance(rate.auth, QueryParamAuth)
    assert rate.auth.param == "api_key"
    assert rate.auth.key == "env-key-123"
    assert rate.auth.has_credentials
    assert rate.common_params.get("tool") == "PydanticAI-Clinical/1.0"
    assert rate.common_params.get("email")  # default email applied


async def test_rate_config_for_pubmed_db_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NCBI_API_KEY", "env-key")
    cfg = SourceConfig(
        id="pubmed",
        display_name="PubMed",
        enabled=True,
        api_key="db-key-override",
        contact_email="admin@example.org",
    )
    rate = _rate_config_for(cfg)
    assert isinstance(rate.auth, QueryParamAuth)
    assert rate.auth.key == "db-key-override"
    assert rate.common_params["email"] == "admin@example.org"


async def test_rate_config_for_europepmc_has_no_auth() -> None:
    cfg = SourceConfig(
        id="europepmc",
        display_name="Europe PMC",
        enabled=True,
    )
    rate = _rate_config_for(cfg)
    assert isinstance(rate.auth, NoAuth)
    assert not rate.auth.has_credentials
    assert rate.common_params == {}


async def test_rate_config_carries_retry_knobs() -> None:
    cfg = SourceConfig(
        id="pubmed",
        display_name="PubMed",
        enabled=True,
        max_retries=5,
        backoff_base_sec=2.5,
        backoff_cap_sec=30.0,
    )
    rate = _rate_config_for(cfg)
    assert rate.max_retries == 5
    assert rate.backoff_base_sec == 2.5
    assert rate.backoff_cap_sec == 30.0
