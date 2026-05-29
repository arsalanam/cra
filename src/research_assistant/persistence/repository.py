"""Data access layer for threads, messages, and stream events."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    DEFAULT_USER_ID,
    LiteratureWatch,
    Message,
    Notification,
    StreamEvent,
    Thread,
    WatchRun,
)


class ThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Threads ───────────────────────────────────────────────────────────

    async def create_thread(
        self,
        title: str = "New conversation",
        user_id: str | None = None,
        workflow: str | None = None,
    ) -> Thread:
        from .models import DEFAULT_USER_ID

        thread = Thread(
            title=title[:120],
            user_id=user_id or DEFAULT_USER_ID,
            workflow=workflow,
        )
        self._s.add(thread)
        await self._s.flush()
        return thread

    async def get_thread(self, thread_id: str) -> Thread | None:
        return await self._s.get(Thread, thread_id)

    async def list_threads(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        user_id: str | None = None,
    ) -> list[Thread]:
        stmt = select(Thread)
        if user_id is not None:
            stmt = stmt.where(Thread.user_id == user_id)
        stmt = stmt.order_by(Thread.updated_at.desc()).offset(offset).limit(limit)
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def delete_thread(self, thread_id: str) -> bool:
        thread = await self._s.get(Thread, thread_id)
        if thread is None:
            return False
        await self._s.delete(thread)
        await self._s.flush()
        return True

    async def update_thread(self, thread_id: str, **kwargs: object) -> Thread | None:
        thread = await self._s.get(Thread, thread_id)
        if thread is None:
            return None
        for key, value in kwargs.items():
            if hasattr(thread, key):
                setattr(thread, key, value)
        await self._s.flush()
        return thread

    # ── Messages ──────────────────────────────────────────────────────────

    async def add_message(
        self,
        thread_id: str,
        role: str,
        input_text: str | None = None,
        final_answer: str | None = None,
    ) -> Message:
        msg = Message(
            thread_id=thread_id,
            role=role,
            input_text=input_text,
            final_answer=final_answer,
        )
        self._s.add(msg)
        await self._s.flush()
        return msg

    async def get_messages(self, thread_id: str, limit: int | None = None) -> list[Message]:
        """Return messages for a thread, ordered chronologically.

        If *limit* is set, returns the N most recent messages (still in
        chronological order).
        """
        stmt = select(Message).where(Message.thread_id == thread_id)
        if limit is not None:
            inner = stmt.order_by(Message.created_at.desc()).limit(limit).subquery()
            stmt = select(Message).join(inner, Message.id == inner.c.id)
        stmt = stmt.order_by(Message.created_at.asc())
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def set_final_answer(self, message_id: str, final_answer: str) -> None:
        msg = await self._s.get(Message, message_id)
        if msg is not None:
            msg.final_answer = final_answer
            await self._s.flush()

    async def count_messages(self, thread_id: str) -> int:
        stmt = select(func.count(Message.id)).where(Message.thread_id == thread_id)
        result = await self._s.execute(stmt)
        return result.scalar_one()

    # ── Stream Events ─────────────────────────────────────────────────────

    async def add_stream_event(
        self,
        message_id: str,
        event_type: str,
        data: dict[str, object],
        sequence_num: int,
    ) -> StreamEvent:
        evt = StreamEvent(
            message_id=message_id,
            event_type=event_type,
            data=json.dumps(data, default=str),
            sequence_num=sequence_num,
        )
        self._s.add(evt)
        await self._s.flush()
        return evt

    async def get_stream_events(self, message_id: str) -> list[StreamEvent]:
        stmt = (
            select(StreamEvent)
            .where(StreamEvent.message_id == message_id)
            .order_by(StreamEvent.sequence_num)
        )
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    # ── Eager-loading helpers ─────────────────────────────────────────────

    async def get_done_events_since(
        self,
        since: datetime,
        *,
        user_id: str | None = None,
    ) -> list[StreamEvent]:
        """Fetch all `done` stream events since a timestamp.

        Filtering by `user_id` walks StreamEvent → Message → Thread.user_id
        so per-user daily token totals (RBAC-3) can be computed without
        touching the event payload itself.
        """
        stmt = select(StreamEvent).where(
            StreamEvent.event_type == "done", StreamEvent.created_at >= since
        )
        if user_id is not None:
            stmt = (
                stmt.join(Message, Message.id == StreamEvent.message_id)
                .join(Thread, Thread.id == Message.thread_id)
                .where(Thread.user_id == user_id)
            )
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def count_events_by_type_since(self, since: datetime) -> dict[str, int]:
        """Count stream events grouped by event_type since a given date."""
        stmt = (
            select(StreamEvent.event_type, func.count(StreamEvent.id))
            .where(StreamEvent.created_at >= since)
            .group_by(StreamEvent.event_type)
        )
        result = await self._s.execute(stmt)
        return {event_type: count for event_type, count in result.all()}

    async def get_thread_with_messages(self, thread_id: str) -> Thread | None:
        stmt = select(Thread).options(selectinload(Thread.messages)).where(Thread.id == thread_id)
        result = await self._s.execute(stmt)
        return result.scalar_one_or_none()


class WatchRepository:
    """CRUD for LiteratureWatch + WatchRun + Notification."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Watches ───────────────────────────────────────────────────────────

    async def create_watch(
        self,
        *,
        name: str,
        pico_json: str,
        search_query: str,
        sources_json: str,
        schedule_cron: str,
        triage_threshold: float = 0.6,
        baseline_pmids_json: str = "[]",
        user_id: str | None = None,
    ) -> LiteratureWatch:
        watch = LiteratureWatch(
            user_id=user_id or DEFAULT_USER_ID,
            name=name[:200],
            pico_json=pico_json,
            search_query=search_query,
            sources_json=sources_json,
            schedule_cron=schedule_cron,
            triage_threshold=triage_threshold,
            baseline_pmids_json=baseline_pmids_json,
            status="active",
        )
        self._s.add(watch)
        await self._s.flush()
        return watch

    async def get_watch(self, watch_id: str) -> LiteratureWatch | None:
        return await self._s.get(LiteratureWatch, watch_id)

    async def list_watches(self, user_id: str | None = None) -> list[LiteratureWatch]:
        stmt = select(LiteratureWatch).order_by(LiteratureWatch.created_at.desc())
        if user_id is not None:
            stmt = stmt.where(LiteratureWatch.user_id == user_id)
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def list_active_watches(self) -> list[LiteratureWatch]:
        """Return all watches with status='active'. Used by the scheduler at startup."""
        stmt = select(LiteratureWatch).where(LiteratureWatch.status == "active")
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def update_watch(self, watch_id: str, **kwargs: object) -> LiteratureWatch | None:
        watch = await self._s.get(LiteratureWatch, watch_id)
        if watch is None:
            return None
        for key, value in kwargs.items():
            if hasattr(watch, key):
                setattr(watch, key, value)
        await self._s.flush()
        return watch

    async def delete_watch(self, watch_id: str) -> bool:
        watch = await self._s.get(LiteratureWatch, watch_id)
        if watch is None:
            return False
        await self._s.delete(watch)
        await self._s.flush()
        return True

    # ── Watch runs ────────────────────────────────────────────────────────

    async def add_run(self, watch_id: str) -> WatchRun:
        run = WatchRun(watch_id=watch_id, status="running")
        self._s.add(run)
        await self._s.flush()
        return run

    async def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        total_hits: int = 0,
        new_pmids_json: str = "[]",
        removed_pmids_json: str = "[]",
        triage_results_json: str | None = None,
        significance_summary: str | None = None,
        error_message: str | None = None,
    ) -> WatchRun | None:
        from .models import _utcnow

        run = await self._s.get(WatchRun, run_id)
        if run is None:
            return None
        run.status = status
        run.total_hits = total_hits
        run.new_pmids_json = new_pmids_json
        run.removed_pmids_json = removed_pmids_json
        run.triage_results_json = triage_results_json
        run.significance_summary = significance_summary
        run.error_message = error_message
        run.finished_at = _utcnow()
        await self._s.flush()
        return run

    async def list_runs(self, watch_id: str, limit: int = 50) -> list[WatchRun]:
        stmt = (
            select(WatchRun)
            .where(WatchRun.watch_id == watch_id)
            .order_by(WatchRun.started_at.desc())
            .limit(limit)
        )
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    # ── Notifications ─────────────────────────────────────────────────────

    async def add_notification(
        self,
        *,
        watch_id: str,
        run_id: str,
        title: str,
        summary: str,
        new_paper_count: int,
        user_id: str | None = None,
    ) -> Notification:
        notif = Notification(
            user_id=user_id or DEFAULT_USER_ID,
            watch_id=watch_id,
            run_id=run_id,
            title=title[:300],
            summary=summary,
            new_paper_count=new_paper_count,
        )
        self._s.add(notif)
        await self._s.flush()
        return notif

    async def list_notifications(
        self,
        user_id: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Notification]:
        stmt = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def count_unread(self, user_id: str | None = None) -> int:
        stmt = select(func.count(Notification.id)).where(Notification.read_at.is_(None))
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)
        result = await self._s.execute(stmt)
        return result.scalar_one()

    async def mark_notification_read(self, notification_id: str) -> bool:
        from .models import _utcnow

        notif = await self._s.get(Notification, notification_id)
        if notif is None:
            return False
        notif.read_at = _utcnow()
        await self._s.flush()
        return True
