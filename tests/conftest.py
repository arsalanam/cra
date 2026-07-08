"""Shared pytest fixtures and configuration."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from research_assistant.persistence.clinical.models import ClinicalBase
from research_assistant.persistence.models import Base

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("CLINICAL_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("TAVILY_API_KEY", "test-key")
# Keep dispatch() hermetic in tests — the LLM fallback classifier would
# otherwise attempt a live Bedrock call on unmatched messages. Tests that
# exercise the fallback stub `classify_with_llm` and flip the setting.
os.environ.setdefault("DISPATCHER_LLM_FALLBACK_ENABLED", "false")


@pytest.fixture
def sample_csv() -> str:
    return "name,age\nalice,30\nbob,25\n"


@pytest.fixture
def sample_json() -> str:
    return '{"hello": "world", "n": 42}'


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Provide a fresh in-memory SQLite session per test (research-app schema)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def clinical_session() -> AsyncIterator[AsyncSession]:
    """Provide a fresh in-memory SQLite session per test (clinical-data schema)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(ClinicalBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
