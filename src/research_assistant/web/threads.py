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

from ..auth import SessionPayload
from ..config import get_settings
from ..persistence.database import get_db_session
from ..persistence.models import DEFAULT_USER_ID, Thread
from ..persistence.repository import ThreadRepository
from ..persistence.user_repository import UserRepository
from ..reports import csr as _csr_report
from ..reports import grade as _grade_report
from ..reports import irb as _irb_report
from ..reports import manuscript as _manuscript_report
from ..reports import meta_analysis as _ma_report
from ..reports import registration as _registration_report
from ..reports import risk_of_bias as _rob_report
from ..reports import sap as _sap_report
from ..reports import sr_protocol as _proto_report
from ..services.quota import build_quota_payload, get_today_token_totals
from .auth import CurrentUser

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


async def resolve_local_user_id(user: SessionPayload) -> str:
    """Translate a session payload to the local `users.id` for ownership checks.

    - When auth is disabled, the placeholder `default-user` sub IS the local
      id — no lookup needed.
    - When auth is enabled, `user.sub` is the Cognito sub; look up the
      matching `User.id`. A missing local row is a bug (resolve_login should
      have created it on first sign-in), so we fall back to default-user
      rather than 500ing — log it and treat the caller as a shared-bucket
      user for the duration of this request.
    """
    if user.sub == DEFAULT_USER_ID:
        return DEFAULT_USER_ID
    async with get_db_session() as session:
        local = await UserRepository(session).get_by_sub(user.sub)
    if local is None:
        logger.warning(
            "No local User row for cognito sub %s — using DEFAULT_USER_ID fallback. "
            "Was resolve_login skipped?",
            user.sub,
        )
        return DEFAULT_USER_ID
    return local.id


def _owns(thread: Thread, local_user_id: str) -> bool:
    """Ownership check. Threads without a user_id (legacy rows) are treated
    as owned by DEFAULT_USER_ID — preserves the pre-RBAC-3 shared-bucket
    behaviour for any unscoped row that survived the migration.
    """
    owner = thread.user_id or DEFAULT_USER_ID
    return owner == local_user_id


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
    async def create_thread(
        user: CurrentUser,
        body: ThreadCreate | None = None,
    ) -> ThreadOut:
        title = body.title if body else "New conversation"
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.create_thread(title=title, user_id=owner)
            return _thread_out(thread, message_count=0)

    @router.get("", response_model=list[ThreadOut])
    async def list_threads(user: CurrentUser, limit: int = 50, offset: int = 0) -> list[ThreadOut]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            threads = await repo.list_threads(limit=limit, offset=offset, user_id=owner)
            return [_thread_out(t, await repo.count_messages(t.id)) for t in threads]

    # ── Usage Aggregation (must be before /{thread_id} routes) ──────

    @router.get("/usage/monthly")
    async def get_monthly_usage(user: CurrentUser) -> dict[str, Any]:
        """Aggregate THIS USER'S token usage and tool invocations for the
        current month (RBAC-3: per-user partition).

        Event-type counts stay global — they're a system-level surface
        (e.g. `error` event rate) rather than a per-user one.
        """
        owner = await resolve_local_user_id(user)
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        async with get_db_session() as session:
            repo = ThreadRepository(session)
            done_events = await repo.get_done_events_since(month_start, user_id=owner)
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
    async def get_today_usage(user: CurrentUser) -> dict[str, Any]:
        """Current UTC-day token consumption vs the configured daily caps.

        Powers the frontend's quota-gauge header strip. Shape is shared
        with `TurnResponse.quota` so the UI uses one component for both.

        RBAC-3: the dashboard surface scopes to the calling user's own
        consumption. The enforcement gauge in `TurnResponse.quota` keeps
        showing the *global* total against the cap (matches the
        global-quota semantics that `enforce_daily_token_quota` enforces).
        """
        owner = await resolve_local_user_id(user)
        settings = get_settings()
        async with get_db_session() as session:
            totals = await get_today_token_totals(session, user_id=owner)
        return build_quota_payload(totals, settings)

    # ── Thread by ID ──────────────────────────────────────────────────

    @router.get("/{thread_id}", response_model=ThreadOut)
    async def get_thread(thread_id: str, user: CurrentUser) -> ThreadOut:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.get_thread(thread_id)
            if thread is None or not _owns(thread, owner):
                # Returning 404 (not 403) for a thread owned by someone else
                # is intentional — it avoids leaking that a thread with that
                # id exists. Same shape as a real not-found.
                raise HTTPException(404, "Thread not found")
            count = await repo.count_messages(thread_id)
            return _thread_out(thread, count)

    @router.delete("/{thread_id}", status_code=204)
    async def delete_thread(thread_id: str, user: CurrentUser) -> None:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.get_thread(thread_id)
            if thread is None or not _owns(thread, owner):
                raise HTTPException(404, "Thread not found")
            if not await repo.delete_thread(thread_id):
                raise HTTPException(404, "Thread not found")

    # ── Messages ──────────────────────────────────────────────────────

    @router.get("/{thread_id}/messages", response_model=list[MessageOut])
    async def get_messages(thread_id: str, user: CurrentUser) -> list[MessageOut]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.get_thread(thread_id)
            if thread is None or not _owns(thread, owner):
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
        "sap": (
            _sap_report.assemble_report_data,
            _sap_report.build_pdf,
            _sap_report.build_docx,
            "sap",
            "Statistical Analysis Plan",
        ),
        "manuscript": (
            _manuscript_report.assemble_report_data,
            _manuscript_report.build_pdf,
            _manuscript_report.build_docx,
            "manuscript",
            "manuscript",
        ),
        "registration": (
            _registration_report.assemble_report_data,
            _registration_report.build_pdf,
            _registration_report.build_docx,
            "registration",
            "trial registration",
        ),
        "irb": (
            _irb_report.assemble_report_data,
            _irb_report.build_pdf,
            _irb_report.build_docx,
            "irb-packet",
            "IRB submission packet",
        ),
        "csr": (
            _csr_report.assemble_report_data,
            _csr_report.build_pdf,
            _csr_report.build_docx,
            "csr",
            "Clinical Study Report (ICH E3)",
        ),
        "grade": (
            _grade_report.assemble_report_data,
            _grade_report.build_pdf,
            _grade_report.build_docx,
            "grade-prisma",
            "GRADE + PRISMA 2020 checklist",
        ),
    }

    @router.get("/{thread_id}/report/{kind}/{fmt}")
    async def download_workflow_report(
        thread_id: str, kind: str, fmt: str, user: CurrentUser
    ) -> Response:
        """Generate a downloadable PDF or DOCX report for one of the
        report-producing workflows on this thread.

        kind ∈ {meta_analysis, sr_protocol, rob}. Returns 404 if no
        terminal card of that workflow exists yet on the thread.
        """
        if kind not in _REPORT_BUILDERS:
            raise HTTPException(
                400,
                f"Unsupported report kind {kind!r}. Choose one of: {sorted(_REPORT_BUILDERS)}.",
            )
        if fmt not in _REPORT_FORMATS:
            raise HTTPException(
                400,
                f"Unsupported report format {fmt!r}. Choose one of: {sorted(_REPORT_FORMATS)}.",
            )
        assembler, build_pdf_fn, build_docx_fn, slug, friendly = _REPORT_BUILDERS[kind]
        media_type, ext = _REPORT_FORMATS[fmt]

        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.get_thread(thread_id)
            if thread is None or not _owns(thread, owner):
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
    async def get_events(
        thread_id: str, message_id: str, user: CurrentUser
    ) -> list[dict[str, Any]]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.get_thread(thread_id)
            if thread is None or not _owns(thread, owner):
                raise HTTPException(404, "Thread not found")
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
