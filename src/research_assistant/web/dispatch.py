"""Dispatcher endpoint — single entry point for every user turn.

The endpoint:
  1. Reads the thread (workflow + summary + recent messages).
  2. Computes `last_turn_kind` from the most recent assistant message.
  3. Asks `agent.dispatcher` which specialist to route to.
  4. Runs the chosen specialist.
  5. Persists the structured response as JSON in `Message.final_answer`,
     and the chosen workflow back onto `Thread.workflow`.

Two URLs serve the same handler for backward compatibility:
  • POST /api/turn           (canonical, dispatcher-aware)
  • POST /api/clinical/turn  (alias kept while the frontend transitions)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pydantic_ai.exceptions import UsageLimitExceeded

from ..agent.deps import ToolErrorBudgetExceeded
from ..agent.dispatcher import SkillNotAuthorizedError, classify_route, dispatch
from ..config import get_settings
from ..persistence.context import messages_to_history
from ..persistence.database import get_db_session
from ..persistence.models import DEFAULT_USER_ID, ClinicalTrial, Message
from ..persistence.repository import AccountError, AccountRepository, ThreadRepository
from ..persistence.summarizer import StubThreadSummarizer
from ..persistence.user_repository import UserRepository
from ..services.quota import (
    DailyTokenQuotaExceeded,
    build_quota_payload,
    enforce_daily_token_quota,
    get_today_token_totals,
)
from .auth import CurrentUser
from .threads import resolve_local_user_id

logger = logging.getLogger(__name__)


def _classify_agent_error(exc: Exception) -> tuple[int, str]:
    """Map an agent/Bedrock exception to (status_code, user-facing message).

    Distinguishes the cases the user actually needs to act on differently:
      - Wall-clock timeout (specialist exceeded `agent_timeout_seconds`)
      - Bedrock daily-token quota (retry won't help; wait for 00:00 UTC reset
        or request a quota increase)
      - Generic Bedrock throttling (per-minute; just retry shortly)
      - Everything else (surface raw error for debugging)
    """
    settings = get_settings()

    # Pre-flight daily-token quota refusal (not a Bedrock error — raised
    # by services.quota before any Bedrock call is made).
    if isinstance(exc, DailyTokenQuotaExceeded):
        return (
            429,
            (
                f"Daily {exc.dimension}-token quota exceeded "
                f"({exc.current:,} / {exc.limit:,}). "
                f"The quota resets at 00:00 UTC (in ~{exc.hours_until_reset():.1f} hours). "
                f"To raise the cap, increase MAX_{exc.dimension.upper()}_TOKENS_PER_DAY."
            ),
        )

    # Wall-clock timeout from asyncio.wait_for in a specialist's run_turn.
    if isinstance(exc, TimeoutError):
        return (
            504,
            (
                f"Agent turn exceeded the {settings.agent_timeout_seconds:.0f}s "
                f"wall-clock limit and was cancelled. Common causes and fixes: "
                f"(1) For math-heavy or combinatorial questions (subset-sum, "
                f"iterative search, large summations), hint the agent to use "
                f"`python_repl` or `sandbox_exec` — one Python call beats "
                f"dozens of `calculator` calls. "
                f"(2) Narrow the question's scope. "
                f"(3) If the question is legitimately deep, raise "
                f"AGENT_TIMEOUT_SECONDS. "
                f"(4) If it keeps happening for the same question, check the "
                f"server logs for a misbehaving tool stuck in a retry loop."
            ),
        )

    # Per-turn tool-call cap tripped — a specialist exhausted its
    # tool_calls_limit before producing a final answer (often the model
    # chasing full-text it can't reach). Retry won't help; the user must
    # narrow scope or supply the data directly.
    if isinstance(exc, UsageLimitExceeded):
        return (
            400,
            (
                "This turn ran out of research steps before it could finish. "
                "That usually means the model spent too many steps searching "
                "or chasing full text it couldn't reach. Try: (1) narrow the "
                "question, (2) paste the studies / extraction data directly so "
                "it doesn't need to search, or (3) if the question is "
                "legitimately deep, raise the specialist's tool-call limit."
            ),
        )

    # Per-turn tool-error circuit breaker backstop — the error budget was
    # exhausted (all tools soft-disabled, model told to answer with what it
    # had) and the model STILL kept attempting tool calls instead of
    # producing an answer. Usually a transient upstream failure underneath.
    if isinstance(exc, ToolErrorBudgetExceeded):
        return (
            502,
            (
                "The assistant stopped because several tool calls failed in a "
                "row — typically a paper source or document fetch that was "
                "unreachable or erroring — and it could not produce a partial "
                "answer from what it had. This is usually a transient upstream "
                "issue: retry in a moment. If it persists, check the paper-"
                "source settings and the server logs."
            ),
        )

    msg = str(exc)

    # Bedrock daily token quota — distinct from per-second throttling because
    # retry/backoff cannot recover. boto3 has already exhausted retries by the
    # time this exception bubbles up here.
    if "Too many tokens per day" in msg:
        now = datetime.now(UTC)
        reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        hours = (reset - now).total_seconds() / 3600
        return (
            429,
            (
                f"AWS Bedrock daily token quota exceeded for this account. "
                f"The quota resets at 00:00 UTC (in ~{hours:.1f} hours). "
                f"For a permanent fix, request an increase via the AWS "
                f"Service Quotas console for 'Cross-region model invocation "
                f"tokens per day' on the active model."
            ),
        )

    # Generic Bedrock per-minute / per-second throttle — retry helps here.
    if "ThrottlingException" in msg or "status_code: 429" in msg:
        return (
            429,
            (
                "AWS Bedrock rate-limited this request (per-minute throttle). "
                "Wait ~30 seconds and retry. If this happens often, request a "
                "quota increase via AWS Service Quotas."
            ),
        )

    # Fallback — surface the raw error so the user can debug. No "Agent error:"
    # prefix here; the frontend adds its own framing.
    return (500, str(exc))


# Sprint A2.5: terminal-card `kind` → ClinicalTrial artefact slot. Only
# triggers auto-bind when the kind hits the final document (not an
# intermediate intake / draft step). Lay summaries deliberately aren't
# here — Trial currently has no lay_summary_thread_id column (deferred).
_KIND_TO_ARTEFACT_SLOT: dict[str, str] = {
    "registration_document": "registration",
    "irb_document": "irb",
    "sap_document": "sap",
    "csr_document": "csr",
    "manuscript_draft": "manuscript",
}


async def _maybe_autobind_artefact(
    session: Any,
    *,
    thread_id: str,
    trial_id: str | None,
    output_kind: str | None,
) -> None:
    """Bind Thread → Trial.artefact_slot when the turn produced a terminal
    artefact AND the Thread is trial-bound AND the slot is still empty.

    Idempotent — never overwrites a populated slot, so a Trial whose
    manuscript was already drafted in another thread stays untouched. Any
    failure (bad mapping, deleted Trial, FK issue) is logged and dropped
    so the dispatcher's happy path is never blocked by a binding hiccup.
    """
    if trial_id is None or output_kind is None:
        return
    slot = _KIND_TO_ARTEFACT_SLOT.get(output_kind)
    if slot is None:
        return
    trial = await session.get(ClinicalTrial, trial_id)
    if trial is None:
        return
    slot_column = f"{slot}_thread_id"
    if getattr(trial, slot_column, None):
        # Slot already taken — preserve operator's earlier binding.
        return
    try:
        await AccountRepository(session).bind_trial_artefact(
            trial_id=trial_id, kind=slot, thread_id=thread_id
        )
        logger.info(
            "Auto-bound thread=%s to trial=%s slot=%s on kind=%s",
            thread_id,
            trial_id,
            slot,
            output_kind,
        )
    except AccountError:
        logger.exception(
            "Auto-bind failed for thread=%s trial=%s slot=%s",
            thread_id,
            trial_id,
            slot,
        )


def _last_assistant_kind(messages: list[Message], workflow: str | None = None) -> str | None:
    """Find the `kind` of the most recent assistant turn, if any.

    When `workflow` is given, only assistant turns produced by THAT
    workflow count — a general_qa detour mid-meta-analysis must not feed
    its "answer" kind into the meta-analysis stage gate. Legacy rows
    (written before Message.workflow existed) have NULL and still count,
    preserving pre-column behaviour for old threads.
    """
    for msg in reversed(messages):
        if msg.role != "assistant" or not msg.final_answer:
            continue
        if workflow is not None and msg.workflow is not None and msg.workflow != workflow:
            continue
        try:
            payload = json.loads(msg.final_answer)
        except (json.JSONDecodeError, TypeError):
            continue
        kind = payload.get("kind") if isinstance(payload, dict) else None
        if isinstance(kind, str):
            return kind
    return None


_ERRORED_TURN_USAGE_PLACEHOLDER = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "requests": 0,
    "tool_calls": 0,
}


async def _persist_errored_turn(
    *,
    thread_id: str,
    user_message: str,
    error: Exception,
    fallback_workflow: str | None,
) -> None:
    """Write a placeholder assistant message + `done` event for a failed turn.

    Without this, a turn that raises (Bedrock throttle, tool-call cap,
    timeout, validator retry exhaustion, …) never gets recorded — the
    usage page and daily-quota counter both go blind to errored work.
    Token usage is unrecoverable (pydantic-ai discards partial Usage on
    exception), so we record zeros; but the event itself increments the
    visible question count and surfaces the error message in the thread.
    """
    error_payload = {
        "kind": "error",
        "text": f"Agent error: {error}",
    }
    async with get_db_session() as session:
        repo = ThreadRepository(session)
        try:
            assistant_msg = await repo.add_message(
                thread_id=thread_id,
                role="assistant",
                final_answer=json.dumps(error_payload),
                workflow=fallback_workflow,
            )
            await repo.add_stream_event(
                message_id=assistant_msg.id,
                event_type="done",
                data={
                    "usage": dict(_ERRORED_TURN_USAGE_PLACEHOLDER),
                    "tool_usage": {},
                    "workflow": fallback_workflow or "errored",
                    "error": str(error)[:500],
                },
                sequence_num=0,
            )
        except Exception:
            # Recording the error must never itself mask the original
            # error — log and move on so HTTPException still surfaces.
            logger.exception(
                "Failed to persist errored turn for thread=%s; original "
                "error will still be returned to the client.",
                thread_id,
            )


class TurnRequest(BaseModel):
    thread_id: str
    user_message: str


class TurnResponse(BaseModel):
    user_message_id: str
    assistant_message_id: str
    workflow: str
    output: dict[str, Any]
    usage: dict[str, Any]  # per-turn token + tool usage
    quota: dict[str, Any]  # cumulative-today vs daily cap (see _build_quota_payload)


def create_dispatch_router() -> APIRouter:
    router = APIRouter(tags=["turn"])

    @router.post("/turn", response_model=TurnResponse)
    @router.post("/clinical/turn", response_model=TurnResponse)
    async def turn(body: TurnRequest, user: CurrentUser) -> TurnResponse:
        settings = get_settings()
        logger.info(
            "Turn requested — thread=%s, msg=%r",
            body.thread_id,
            body.user_message[:80],
        )

        owner_id = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            thread = await repo.get_thread(body.thread_id)
            # RBAC-3: refuse to run a turn against another user's thread.
            # Return 404 (not 403) to avoid leaking thread-id existence.
            if thread is None or (thread.user_id or DEFAULT_USER_ID) != owner_id:
                raise HTTPException(404, "Thread not found")

            # Pre-flight daily-token quota — refuse before persisting the
            # user message so a quota-blocked turn doesn't leave a dangling
            # unanswered message in the thread.
            try:
                await enforce_daily_token_quota(session)
            except DailyTokenQuotaExceeded as quota_exc:
                status, detail = _classify_agent_error(quota_exc)
                raise HTTPException(status, detail) from quota_exc

            if thread.title == "New conversation":
                await repo.update_thread(body.thread_id, title=body.user_message[:120])

            user_msg = await repo.add_message(
                thread_id=body.thread_id,
                role="user",
                input_text=body.user_message,
            )
            user_msg_id = user_msg.id

            context_msgs = await repo.get_messages(
                body.thread_id, limit=settings.context_window_messages
            )
            history = messages_to_history(context_msgs, thread_summary=thread.summary)
            current_workflow = thread.workflow
            # Classify up front (pure + deterministic; dispatch() repeats it)
            # so the stage-gate input is scoped to the workflow that will
            # actually run — not whatever specialist answered last.
            route_preview = classify_route(body.user_message, current_workflow)
            last_kind = _last_assistant_kind(context_msgs, workflow=route_preview.workflow)

            summarizer = StubThreadSummarizer(threshold=settings.summarize_after_messages)
            if await summarizer.should_summarize(body.thread_id, session):
                await summarizer.summarize_and_truncate(body.thread_id, session)

            # RBAC-1 skill gating: resolve the caller's global-scope
            # effective permissions and pass them down to the dispatcher.
            # When auth is disabled, settings.auth_enabled is False and we
            # pass None to bypass the gate (default-user is meant to be
            # all-powerful in test/dev). Same session so the lookup joins
            # the user's already-loaded thread context.
            if settings.auth_enabled:
                effective_perms = await UserRepository(session).effective_permissions_for_sub(
                    user.sub
                )
            else:
                effective_perms = None

        try:
            output, meta, route = await dispatch(
                body.user_message,
                current_workflow=current_workflow,
                message_history=history,
                last_turn_kind=last_kind,
                effective_permissions=effective_perms,
            )
            chosen_workflow = route.workflow
        except SkillNotAuthorizedError as skill_exc:
            # 403 with an actionable message — tells the user which workflow
            # was attempted and which permission they'd need. The frontend
            # can hide the entry points for skills the caller lacks (using
            # /auth/me's `permissions` list) so this branch only fires when
            # someone bypasses the UI.
            logger.info(
                "Skill gate blocked thread=%s sub=%s workflow=%s",
                body.thread_id,
                user.sub,
                skill_exc.workflow,
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Your account isn't authorized for the "
                    f"'{skill_exc.workflow}' workflow. "
                    f"Required permission: {skill_exc.required.value}. "
                    f"Ask an administrator to grant a role that includes it, "
                    f"or use a workflow you do have access to."
                ),
            ) from skill_exc
        except Exception as e:
            logger.exception("Dispatcher / specialist failed for thread=%s", body.thread_id)
            # Record the errored turn so it shows up in the usage page +
            # quota counter — otherwise failed turns are invisible to all
            # tracking and the user can't see they happened.
            await _persist_errored_turn(
                thread_id=body.thread_id,
                user_message=body.user_message,
                error=e,
                fallback_workflow=current_workflow,
            )
            status, detail = _classify_agent_error(e)
            raise HTTPException(status, detail) from e

        # Persist structured output + pin the thread to the chosen workflow.
        # The `done` StreamEvent fuels the Usage page (/api/threads/usage/monthly)
        # — it aggregates `usage` and `tool_usage` across all done events in
        # the current month. Without this write the page reports zeros.
        output_json = output.model_dump_json()
        async with get_db_session() as session:
            repo = ThreadRepository(session)
            assistant_msg = await repo.add_message(
                thread_id=body.thread_id,
                role="assistant",
                final_answer=output_json,
                workflow=chosen_workflow,
            )
            assistant_msg_id = assistant_msg.id
            # Inject the bedrock model_id into the done event so the
            # cost-rollup service (P2 #6) can attribute USD per-model.
            # Falls back gracefully when model_id is absent.
            usage_with_model = dict(meta.get("usage", {}))
            usage_with_model.setdefault("model_id", get_settings().bedrock_model_id)
            await repo.add_stream_event(
                message_id=assistant_msg_id,
                event_type="done",
                data={
                    "usage": usage_with_model,
                    "tool_usage": meta.get("tool_usage", {}),
                    "workflow": chosen_workflow,
                    # Routing observability: which classification rule fired.
                    "route_rule": route.rule,
                },
                sequence_num=0,
            )
            # Pin the thread only for sticky routes — a definitional detour
            # to general_qa answers the question without stealing the thread
            # from its workflow.
            if route.sticky and thread.workflow != chosen_workflow:
                await repo.update_thread(body.thread_id, workflow=chosen_workflow)

            # Sprint A2.5: when the turn produced a terminal artefact and
            # the Thread is trial-bound, auto-bind into the Trial's slot.
            # Idempotent + best-effort — runs in the same session as the
            # message write so a roll-back undoes both, but a binding
            # failure never blocks the response.
            await _maybe_autobind_artefact(
                session,
                thread_id=body.thread_id,
                trial_id=thread.trial_id,
                output_kind=getattr(output, "kind", None),
            )

            # Compute the post-turn quota snapshot in the same session so
            # this turn's just-written `done` event is included in the totals
            # the client receives.
            updated_totals = await get_today_token_totals(session)
            quota_payload = build_quota_payload(updated_totals, settings)

        return TurnResponse(
            user_message_id=user_msg_id,
            assistant_message_id=assistant_msg_id,
            workflow=chosen_workflow,
            output=output.model_dump(),
            usage=meta["usage"],
            quota=quota_payload,
        )

    return router
