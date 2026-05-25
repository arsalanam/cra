"""Unit tests for the WatchRepository CRUD layer."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.models import (
    DEFAULT_USER_ID,
    LiteratureWatch,
    User,
)
from research_assistant.persistence.repository import WatchRepository


async def _seed_default_user(session: AsyncSession) -> None:
    user = await session.get(User, DEFAULT_USER_ID)
    if user is None:
        session.add(User(id=DEFAULT_USER_ID, name="Test User"))
        await session.flush()


@pytest.mark.asyncio
async def test_create_and_get_watch(db_session: AsyncSession) -> None:
    await _seed_default_user(db_session)
    repo = WatchRepository(db_session)
    watch = await repo.create_watch(
        name="SGLT2 + HF watch",
        pico_json='{"population":"adults T2DM","intervention":"SGLT2i"}',
        search_query='("SGLT2 inhibitor"[MeSH]) AND ("Heart Failure"[MeSH])',
        sources_json='["pubmed","europepmc"]',
        schedule_cron="0 9 * * 1",
        triage_threshold=0.7,
        baseline_pmids_json='["12345","67890"]',
    )
    assert watch.id
    assert watch.status == "active"
    assert watch.user_id == DEFAULT_USER_ID

    fetched = await repo.get_watch(watch.id)
    assert fetched is not None
    assert fetched.name == "SGLT2 + HF watch"
    assert fetched.triage_threshold == 0.7


@pytest.mark.asyncio
async def test_pause_and_list_active_only(db_session: AsyncSession) -> None:
    await _seed_default_user(db_session)
    repo = WatchRepository(db_session)
    a = await repo.create_watch(
        name="A",
        pico_json="{}",
        search_query="x",
        sources_json="[]",
        schedule_cron="0 9 * * 1",
    )
    b = await repo.create_watch(
        name="B",
        pico_json="{}",
        search_query="y",
        sources_json="[]",
        schedule_cron="0 10 * * 1",
    )
    await repo.update_watch(b.id, status="paused")

    active = await repo.list_active_watches()
    active_ids = {w.id for w in active}
    assert a.id in active_ids
    assert b.id not in active_ids


@pytest.mark.asyncio
async def test_run_lifecycle(db_session: AsyncSession) -> None:
    await _seed_default_user(db_session)
    repo = WatchRepository(db_session)
    watch = await repo.create_watch(
        name="W",
        pico_json="{}",
        search_query="x",
        sources_json="[]",
        schedule_cron="0 9 * * 1",
    )
    run = await repo.add_run(watch.id)
    assert run.status == "running"
    assert run.finished_at is None

    finished = await repo.finish_run(
        run.id,
        status="success",
        total_hits=42,
        new_pmids_json=json.dumps(["new1", "new2"]),
        triage_results_json=json.dumps([{"pmid": "new1", "materiality": 0.8}]),
        significance_summary="Two new RCTs.",
    )
    assert finished is not None
    assert finished.status == "success"
    assert finished.total_hits == 42
    assert finished.finished_at is not None

    runs = await repo.list_runs(watch.id)
    assert len(runs) == 1
    assert runs[0].id == run.id


@pytest.mark.asyncio
async def test_notification_lifecycle(db_session: AsyncSession) -> None:
    await _seed_default_user(db_session)
    repo = WatchRepository(db_session)
    watch = await repo.create_watch(
        name="W",
        pico_json="{}",
        search_query="x",
        sources_json="[]",
        schedule_cron="0 9 * * 1",
    )
    run = await repo.add_run(watch.id)
    notif = await repo.add_notification(
        watch_id=watch.id,
        run_id=run.id,
        title="2 new RCTs in your watch",
        summary="Two new RCTs found this week — both look material.",
        new_paper_count=2,
    )
    assert notif.read_at is None

    unread = await repo.list_notifications(unread_only=True)
    assert len(unread) == 1
    assert await repo.count_unread() == 1

    assert await repo.mark_notification_read(notif.id) is True
    assert await repo.count_unread() == 0


@pytest.mark.asyncio
async def test_delete_watch_cascades_runs_and_notifications(
    db_session: AsyncSession,
) -> None:
    await _seed_default_user(db_session)
    repo = WatchRepository(db_session)
    watch = await repo.create_watch(
        name="W",
        pico_json="{}",
        search_query="x",
        sources_json="[]",
        schedule_cron="0 9 * * 1",
    )
    run = await repo.add_run(watch.id)
    await repo.add_notification(
        watch_id=watch.id,
        run_id=run.id,
        title="t",
        summary="s",
        new_paper_count=1,
    )

    assert await repo.delete_watch(watch.id) is True
    # After cascade, fetching by id returns nothing
    assert await repo.get_watch(watch.id) is None
    # Notifications and runs gone too
    assert (await db_session.get(LiteratureWatch, watch.id)) is None
