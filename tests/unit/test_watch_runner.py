"""Unit tests for the watch_runner — mocks search_papers fanout + triage agent.

Uses a temp-file SQLite shared between test setup and the runner's
self-managed DB sessions. The default `db_session` fixture creates a
separate in-memory engine the runner doesn't see, so we override
DATABASE_URL and call init_db here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from research_assistant.domain.living_review import PaperTriage, WatchRunSummary
from research_assistant.persistence.repository import WatchRepository
from research_assistant.services import watch_runner

PICO_JSON = json.dumps(
    {
        "population": "Adults with T2DM",
        "intervention": "SGLT2 inhibitor",
        "comparison": "Placebo",
        "outcomes": ["HF hospitalization"],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "study_types": ["Randomized Controlled Trial"],
        "age_range": "≥18 years",
        "notes": None,
    }
)


@pytest.fixture
async def runner_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Set up a file-based SQLite both the test and the runner can see."""
    db_path = tmp_path / "watch_test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    # Reset the cached global engine so init_db / get_db_session pick up the new URL.
    from research_assistant.persistence import database as db_mod

    db_mod.reset_engine()

    from research_assistant.persistence.database import init_db

    await init_db()

    # Hand back a session factory pointing at the same DB so the test can seed.
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()
    db_mod.reset_engine()


def _make_envelope(studies: Iterable[dict[str, Any]]) -> str:
    studies_list = list(studies)
    return json.dumps(
        {
            "query": "x",
            "sources_used": ["pubmed"],
            "totals_by_source": {"pubmed": len(studies_list)},
            "returned": len(studies_list),
            "studies": studies_list,
        }
    )


def _study(pmid: str, title: str = "T", source: str = "pubmed") -> dict[str, Any]:
    return {
        "source": source,
        "source_id": pmid,
        "pmid": pmid,
        "title": title,
        "abstract": "Sample abstract.",
        "journal": "J",
        "year": 2026,
    }


async def _create_watch(
    factory: async_sessionmaker[AsyncSession],
    *,
    baseline: list[str],
    threshold: float = 0.6,
) -> str:
    async with factory() as session:
        repo = WatchRepository(session)
        watch = await repo.create_watch(
            name="W",
            pico_json=PICO_JSON,
            search_query="q",
            sources_json='["pubmed"]',
            schedule_cron="0 9 * * 1",
            triage_threshold=threshold,
            baseline_pmids_json=json.dumps(baseline),
        )
        await session.commit()
        return watch.id


@pytest.mark.asyncio
async def test_no_change_when_no_new_pmids(
    runner_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = await _create_watch(runner_db, baseline=["111", "222"])

    async def _fake_fanout(query: str, max_results: int) -> str:
        return _make_envelope([_study("111"), _study("222")])

    monkeypatch.setattr(watch_runner, "_fan_out", _fake_fanout)

    triage_called = False

    async def _fake_triage(**kwargs: Any) -> tuple[WatchRunSummary, dict[str, Any]]:
        nonlocal triage_called
        triage_called = True
        return WatchRunSummary(triages=[], significance_summary="x", notify=False), {}

    monkeypatch.setattr(watch_runner, "triage_run", _fake_triage)

    await watch_runner.run_watch(watch_id)

    assert triage_called is False
    async with runner_db() as session:
        repo = WatchRepository(session)
        runs = await repo.list_runs(watch_id)
        assert len(runs) == 1
        assert runs[0].status == "no_change"
        assert runs[0].total_hits == 2
        fresh = await repo.get_watch(watch_id)
        assert fresh is not None
        assert fresh.last_run_status == "no_change"


@pytest.mark.asyncio
async def test_new_pmids_triaged_below_threshold_no_notification(
    runner_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = await _create_watch(runner_db, baseline=["111"], threshold=0.6)

    async def _fake_fanout(query: str, max_results: int) -> str:
        return _make_envelope([_study("111"), _study("999", title="New paper")])

    monkeypatch.setattr(watch_runner, "_fan_out", _fake_fanout)

    async def _fake_triage(**kwargs: Any) -> tuple[WatchRunSummary, dict[str, Any]]:
        return (
            WatchRunSummary(
                triages=[
                    PaperTriage(
                        pmid="999",
                        title="New paper",
                        relevance="moderate",
                        design_fit="partial",
                        materiality=0.3,
                        note="Underpowered.",
                    )
                ],
                significance_summary="One new paper, low materiality.",
                notify=False,
            ),
            {},
        )

    monkeypatch.setattr(watch_runner, "triage_run", _fake_triage)

    await watch_runner.run_watch(watch_id)

    async with runner_db() as session:
        repo = WatchRepository(session)
        runs = await repo.list_runs(watch_id)
        assert len(runs) == 1
        assert runs[0].status == "success"
        assert runs[0].total_hits == 2
        assert json.loads(runs[0].new_pmids_json) == ["999"]
        notifs = await repo.list_notifications()
        assert notifs == []
        fresh = await repo.get_watch(watch_id)
        assert fresh is not None
        assert sorted(json.loads(fresh.baseline_pmids_json)) == ["111", "999"]


@pytest.mark.asyncio
async def test_material_new_paper_creates_notification(
    runner_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = await _create_watch(runner_db, baseline=["111"], threshold=0.6)

    async def _fake_fanout(query: str, max_results: int) -> str:
        return _make_envelope([_study("111"), _study("999", title="Big RCT")])

    monkeypatch.setattr(watch_runner, "_fan_out", _fake_fanout)

    async def _fake_triage(**kwargs: Any) -> tuple[WatchRunSummary, dict[str, Any]]:
        return (
            WatchRunSummary(
                triages=[
                    PaperTriage(
                        pmid="999",
                        title="Big RCT",
                        relevance="high",
                        design_fit="matches",
                        materiality=0.85,
                        note="Multicenter RCT n=10000 in scope.",
                    )
                ],
                significance_summary="One material new RCT (n=10k).",
                notify=True,
            ),
            {},
        )

    monkeypatch.setattr(watch_runner, "triage_run", _fake_triage)

    await watch_runner.run_watch(watch_id)

    async with runner_db() as session:
        repo = WatchRepository(session)
        notifs = await repo.list_notifications()
        assert len(notifs) == 1
        assert "1 material new paper" in notifs[0].title
        assert notifs[0].new_paper_count == 1
        assert notifs[0].read_at is None


@pytest.mark.asyncio
async def test_paused_watch_skips_run(
    runner_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = await _create_watch(runner_db, baseline=[])
    async with runner_db() as session:
        repo = WatchRepository(session)
        await repo.update_watch(watch_id, status="paused")
        await session.commit()

    fan_out_called = False

    async def _fake_fanout(query: str, max_results: int) -> str:
        nonlocal fan_out_called
        fan_out_called = True
        return _make_envelope([])

    monkeypatch.setattr(watch_runner, "_fan_out", _fake_fanout)

    await watch_runner.run_watch(watch_id)
    assert fan_out_called is False
    async with runner_db() as session:
        repo = WatchRepository(session)
        assert await repo.list_runs(watch_id) == []
