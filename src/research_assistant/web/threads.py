"""Thread-based conversation API endpoints — CRUD only.

Per-turn execution lives in `web/dispatch.py` (POST /api/turn).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ..config import get_settings
from ..persistence.database import get_db_session
from ..persistence.models import Thread
from ..persistence.repository import ThreadRepository
from ..reports import meta_analysis as _ma_report
from ..reports import risk_of_bias as _rob_report
from ..reports import sr_protocol as _proto_report
from ..services.quota import build_quota_payload, get_today_token_totals

logger = logging.getLogger(__name__)


# ── Request / Response schemas ────────────────────────────────────────────


class ThreadCreate(BaseModel):
    title: str = "New conversation"


class ThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    summary: str | None
    workflow: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    thread_id: str
    role: str
    input_text: str | None
    final_answer: str | None
    created_at: datetime


def _thread_out(thread: Thread, message_count: int) -> ThreadOut:
    return ThreadOut.model_validate(thread).model_copy(update={"message_count": message_count})


class EventOut(BaseModel):
    id: str
    event_type: str
    data: dict[str, Any]
    sequence_num: int


# ── Router factory ────────────────────────────────────────────────────────


def create_thread_router() -> APIRouter:
    router = APIRouter(prefix="/threads", tags=["threads"])

    # ── Thread CRUD ───────────────────────────────────────────────────

    @router.post("", response_model=ThreadOut, status_code=201)
    async def create_thread(body: ThreadCreate | None = None) -> ThreadOut:
        title = body.title if body else "New conversation"
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.create_thread(title=title)
            return _thread_out(thread, message_count=0)

    @router.get("", response_model=list[ThreadOut])
    async def list_threads(limit: int = 50, offset: int = 0) -> list[ThreadOut]:
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            threads = await repo.list_threads(limit=limit, offset=offset)
            return [_thread_out(t, await repo.count_messages(t.id)) for t in threads]

    # ── Usage Aggregation (must be before /{thread_id} routes) ──────

    @router.get("/usage/monthly")
    async def get_monthly_usage() -> dict[str, Any]:
        """Aggregate token usage and tool invocations for the current month."""
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        async with get_db_session() as session:
            repo = ThreadRepository(session)
            done_events = await repo.get_done_events_since(month_start)
            event_type_counts = await repo.count_events_by_type_since(month_start)

        total_input = 0
        total_output = 0
        total_requests = 0
        total_tool_calls = 0
        tool_breakdown: Counter[str] = Counter()
        question_count = 0

        for evt in done_events:
            try:
                data = json.loads(evt.data)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Malformed done event data in event %s, skipping", evt.id)
                continue

            usage = data.get("usage")
            if usage:
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)
                total_requests += usage.get("requests", 0)
                total_tool_calls += usage.get("tool_calls", 0)
                question_count += 1

            tool_usage = data.get("tool_usage")
            if tool_usage:
                for name, count in tool_usage.items():
                    tool_breakdown[name] += count

        return {
            "period": f"{month_start.strftime('%b %d')} - {now.strftime('%b %d, %Y')}",
            "questions": question_count,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "model_requests": total_requests,
            "tool_calls": total_tool_calls,
            "tool_breakdown": dict(tool_breakdown),
            "events_by_type": event_type_counts,
        }

    @router.get("/usage/today")
    async def get_today_usage() -> dict[str, Any]:
        """Current UTC-day token consumption vs the configured daily caps.

        Powers the frontend's quota-gauge header strip. Shape is shared
        with `TurnResponse.quota` so the UI uses one component for both.
        """
        settings = get_settings()
        async with get_db_session() as session:
            totals = await get_today_token_totals(session)
        return build_quota_payload(totals, settings)

    # ── Thread by ID ──────────────────────────────────────────────────

    @router.get("/{thread_id}", response_model=ThreadOut)
    async def get_thread(thread_id: str) -> ThreadOut:
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.get_thread(thread_id)
            if thread is None:
                raise HTTPException(404, "Thread not found")
            count = await repo.count_messages(thread_id)
            return _thread_out(thread, count)

    @router.delete("/{thread_id}", status_code=204)
    async def delete_thread(thread_id: str) -> None:
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            if not await repo.delete_thread(thread_id):
                raise HTTPException(404, "Thread not found")

    # ── Messages ──────────────────────────────────────────────────────

    @router.get("/{thread_id}/messages", response_model=list[MessageOut])
    async def get_messages(thread_id: str) -> list[MessageOut]:
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            if await repo.get_thread(thread_id) is None:
                raise HTTPException(404, "Thread not found")
            messages = await repo.get_messages(thread_id)
            return [MessageOut.model_validate(m) for m in messages]

    # ── Downloadable workflow reports ─────────────────────────────────
    _REPORT_FORMATS: dict[str, tuple[str, str]] = {
        # format → (media_type, file extension)
        "pdf": ("application/pdf", "pdf"),
        "docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        ),
    }
    # kind → (assembler, pdf builder, docx builder, filename slug, friendly name)
    _REPORT_BUILDERS: dict[str, tuple[Any, Any, Any, str, str]] = {
        "meta_analysis": (
            _ma_report.assemble_report_data,
            _ma_report.build_pdf,
            _ma_report.build_docx,
            "meta-analysis",
            "meta-analysis",
        ),
        "sr_protocol": (
            _proto_report.assemble_report_data,
            _proto_report.build_pdf,
            _proto_report.build_docx,
            "sr-protocol",
            "SR/MA protocol",
        ),
        "rob": (
            _rob_report.assemble_report_data,
            _rob_report.build_pdf,
            _rob_report.build_docx,
            "rob",
            "risk-of-bias",
        ),
    }

    @router.get("/{thread_id}/report/{kind}/{fmt}")
    async def download_workflow_report(
        thread_id: str, kind: str, fmt: str
    ) -> Response:
        """Generate a downloadable PDF or DOCX report for one of the
        report-producing workflows on this thread.

        kind ∈ {meta_analysis, sr_protocol, rob}. Returns 404 if no
        terminal card of that workflow exists yet on the thread.
        """
        if kind not in _REPORT_BUILDERS:
            raise HTTPException(
                400,
                f"Unsupported report kind {kind!r}. "
                f"Choose one of: {sorted(_REPORT_BUILDERS)}.",
            )
        if fmt not in _REPORT_FORMATS:
            raise HTTPException(
                400,
                f"Unsupported report format {fmt!r}. "
                f"Choose one of: {sorted(_REPORT_FORMATS)}.",
            )
        assembler, build_pdf_fn, build_docx_fn, slug, friendly = _REPORT_BUILDERS[kind]
        media_type, ext = _REPORT_FORMATS[fmt]

        async with get_db_session() as session:
            repo = ThreadRepository(session)
            if await repo.get_thread(thread_id) is None:
                raise HTTPException(404, "Thread not found")
            messages = await repo.get_messages(thread_id)

        data = assembler(thread_id, messages)
        if data is None:
            raise HTTPException(
                404,
                f"No {friendly} result in this thread yet. "
                f"Run the {friendly} workflow to completion first.",
            )

        images_dir = Path(get_settings().images_dir)
        builder = build_pdf_fn if fmt == "pdf" else build_docx_fn
        payload = builder(data, images_dir)

        # Prefix with a short thread tag so the user can tell downloads apart
        # if they grab reports from multiple threads in one session.
        filename = f"{slug}-{thread_id[:8]}.{ext}"
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    # ── Stream Event Replay ───────────────────────────────────────────

    @router.get("/{thread_id}/messages/{message_id}/events", response_model=list[EventOut])
    async def get_events(thread_id: str, message_id: str) -> list[dict[str, Any]]:
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            events = await repo.get_stream_events(message_id)
            return [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "data": json.loads(e.data),
                    "sequence_num": e.sequence_num,
                }
                for e in events
            ]

    return router
