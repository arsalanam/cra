"""Data access layer for threads, messages, and stream events."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    DEFAULT_USER_ID,
    Account,
    AccountMember,
    AccountSite,
    ClinicalTrial,
    EcrfStudy,
    LiteratureWatch,
    LiteratureWatchSubscription,
    Message,
    Notification,
    RunVote,
    StreamEvent,
    SubscriptionMember,
    Thread,
    TrialSite,
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
        trial_id: str | None = None,
    ) -> Thread:
        from .models import DEFAULT_USER_ID

        thread = Thread(
            title=title[:120],
            user_id=user_id or DEFAULT_USER_ID,
            workflow=workflow,
            trial_id=trial_id,
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


class SubscriptionError(Exception):
    """Raised on invalid subscription state changes — invalid vote
    value, watch missing, member not found, etc. Translated to 4xx by
    the endpoint layer."""


class SubscriptionRepository:
    """CRUD for LiteratureWatchSubscription + SubscriptionMember +
    RunVote, plus the quorum tally that drives group notifications.

    Quorum rule: yes-votes ≥ max(min_votes, ceil(min_fraction × n_voters))
    where n_voters is the count of members with role='voter'.
    """

    _ALLOWED_VOTES = ("yes", "no", "abstain")
    _ALLOWED_MEMBER_ROLES = ("voter", "observer")

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Subscriptions ─────────────────────────────────────────────────────

    async def create_subscription(
        self,
        *,
        name: str,
        watch_id: str,
        owner_user_id: str | None = None,
        description: str = "",
        min_votes: int = 2,
        min_fraction: float = 0.5,
    ) -> LiteratureWatchSubscription:
        if not 0.0 <= min_fraction <= 1.0:
            raise SubscriptionError("min_fraction must be between 0 and 1.")
        if min_votes < 1:
            raise SubscriptionError("min_votes must be at least 1.")
        watch = await self._s.get(LiteratureWatch, watch_id)
        if watch is None:
            raise SubscriptionError(f"Watch {watch_id!r} not found.")
        sub = LiteratureWatchSubscription(
            name=name[:200],
            description=description,
            watch_id=watch_id,
            owner_user_id=owner_user_id,
            min_votes=min_votes,
            min_fraction=min_fraction,
        )
        self._s.add(sub)
        await self._s.flush()
        # Auto-add the owner as a voting member so the creator can vote
        # without an explicit invite step.
        if owner_user_id is not None:
            await self.add_member(
                subscription_id=sub.id,
                user_id=owner_user_id,
                role="voter",
                invited_by_user_id=owner_user_id,
            )
        return sub

    async def get_subscription(self, subscription_id: str) -> LiteratureWatchSubscription | None:
        return await self._s.get(LiteratureWatchSubscription, subscription_id)

    async def list_subscriptions_for_user(self, user_id: str) -> list[LiteratureWatchSubscription]:
        """Subscriptions where the user is either the owner OR a member."""
        member_sub_ids = select(SubscriptionMember.subscription_id).where(
            SubscriptionMember.user_id == user_id
        )
        stmt = (
            select(LiteratureWatchSubscription)
            .where(
                (LiteratureWatchSubscription.owner_user_id == user_id)
                | (LiteratureWatchSubscription.id.in_(member_sub_ids))
            )
            .order_by(LiteratureWatchSubscription.created_at.desc())
        )
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def list_subscriptions_for_watch(
        self, watch_id: str
    ) -> list[LiteratureWatchSubscription]:
        stmt = (
            select(LiteratureWatchSubscription)
            .where(LiteratureWatchSubscription.watch_id == watch_id)
            .order_by(LiteratureWatchSubscription.created_at.desc())
        )
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def update_subscription(
        self,
        subscription_id: str,
        **kwargs: object,
    ) -> LiteratureWatchSubscription | None:
        sub = await self._s.get(LiteratureWatchSubscription, subscription_id)
        if sub is None:
            return None
        if "min_fraction" in kwargs:
            mf = kwargs["min_fraction"]
            if not isinstance(mf, (int, float)) or not 0.0 <= float(mf) <= 1.0:
                raise SubscriptionError("min_fraction must be between 0 and 1.")
        if "min_votes" in kwargs:
            mv = kwargs["min_votes"]
            if not isinstance(mv, int) or mv < 1:
                raise SubscriptionError("min_votes must be at least 1.")
        for key, value in kwargs.items():
            if hasattr(sub, key):
                setattr(sub, key, value)
        await self._s.flush()
        return sub

    async def delete_subscription(self, subscription_id: str) -> bool:
        sub = await self._s.get(LiteratureWatchSubscription, subscription_id)
        if sub is None:
            return False
        await self._s.delete(sub)
        await self._s.flush()
        return True

    # ── Members ───────────────────────────────────────────────────────────

    async def add_member(
        self,
        *,
        subscription_id: str,
        user_id: str,
        role: str = "voter",
        invited_by_user_id: str | None = None,
    ) -> SubscriptionMember:
        if role not in self._ALLOWED_MEMBER_ROLES:
            raise SubscriptionError(f"Invalid member role {role!r}; choose voter | observer.")
        sub = await self._s.get(LiteratureWatchSubscription, subscription_id)
        if sub is None:
            raise SubscriptionError(f"Subscription {subscription_id!r} not found.")
        # Idempotent: re-adding the same (sub, user) updates role + invited_by
        # rather than raising on the UNIQUE constraint.
        existing = await self._s.scalar(
            select(SubscriptionMember).where(
                SubscriptionMember.subscription_id == subscription_id,
                SubscriptionMember.user_id == user_id,
            )
        )
        if existing is not None:
            existing.role = role
            existing.invited_by_user_id = invited_by_user_id
            await self._s.flush()
            return existing
        member = SubscriptionMember(
            subscription_id=subscription_id,
            user_id=user_id,
            role=role,
            invited_by_user_id=invited_by_user_id,
        )
        self._s.add(member)
        await self._s.flush()
        return member

    async def remove_member(self, *, subscription_id: str, user_id: str) -> bool:
        member = await self._s.scalar(
            select(SubscriptionMember).where(
                SubscriptionMember.subscription_id == subscription_id,
                SubscriptionMember.user_id == user_id,
            )
        )
        if member is None:
            return False
        await self._s.delete(member)
        await self._s.flush()
        return True

    async def list_members(self, subscription_id: str) -> list[SubscriptionMember]:
        stmt = (
            select(SubscriptionMember)
            .where(SubscriptionMember.subscription_id == subscription_id)
            .order_by(SubscriptionMember.joined_at)
        )
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def is_member(self, *, subscription_id: str, user_id: str) -> SubscriptionMember | None:
        result: SubscriptionMember | None = await self._s.scalar(
            select(SubscriptionMember).where(
                SubscriptionMember.subscription_id == subscription_id,
                SubscriptionMember.user_id == user_id,
            )
        )
        return result

    # ── Votes ─────────────────────────────────────────────────────────────

    async def record_vote(
        self,
        *,
        subscription_id: str,
        run_id: str,
        voter_user_id: str,
        vote: str,
        rationale: str = "",
    ) -> RunVote:
        if vote not in self._ALLOWED_VOTES:
            raise SubscriptionError(f"Invalid vote {vote!r}; choose yes | no | abstain.")
        # Voter must be a member with role='voter'. Observers can't vote.
        member = await self.is_member(subscription_id=subscription_id, user_id=voter_user_id)
        if member is None or member.role != "voter":
            raise SubscriptionError("Only voting members may cast votes on this subscription.")
        # Run must belong to the subscription's watch.
        sub = await self._s.get(LiteratureWatchSubscription, subscription_id)
        if sub is None:
            raise SubscriptionError(f"Subscription {subscription_id!r} not found.")
        run = await self._s.get(WatchRun, run_id)
        if run is None or run.watch_id != sub.watch_id:
            raise SubscriptionError(f"Run {run_id!r} not found on this subscription's watch.")
        # Idempotent upsert keyed on (sub, run, voter).
        existing = await self._s.scalar(
            select(RunVote).where(
                RunVote.subscription_id == subscription_id,
                RunVote.run_id == run_id,
                RunVote.voter_user_id == voter_user_id,
            )
        )
        if existing is not None:
            existing.vote = vote
            existing.rationale = rationale
            await self._s.flush()
            return existing
        rv = RunVote(
            subscription_id=subscription_id,
            run_id=run_id,
            voter_user_id=voter_user_id,
            vote=vote,
            rationale=rationale,
        )
        self._s.add(rv)
        await self._s.flush()
        return rv

    async def list_votes(self, *, subscription_id: str, run_id: str) -> list[RunVote]:
        stmt = (
            select(RunVote)
            .where(
                RunVote.subscription_id == subscription_id,
                RunVote.run_id == run_id,
            )
            .order_by(RunVote.voted_at)
        )
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def tally(self, *, subscription_id: str, run_id: str) -> dict[str, object]:
        """Return the vote breakdown + quorum status for a (sub, run).

        Schema: {yes, no, abstain, n_voters, threshold,
        quorum_cleared}. `n_voters` is the count of members with
        role='voter'. `threshold` is `max(min_votes,
        ceil(min_fraction × n_voters))`. `quorum_cleared` is
        `yes >= threshold` AND `n_voters > 0`.
        """
        import math

        sub = await self._s.get(LiteratureWatchSubscription, subscription_id)
        if sub is None:
            raise SubscriptionError(f"Subscription {subscription_id!r} not found.")
        votes = await self.list_votes(subscription_id=subscription_id, run_id=run_id)
        yes = sum(1 for v in votes if v.vote == "yes")
        no = sum(1 for v in votes if v.vote == "no")
        abstain = sum(1 for v in votes if v.vote == "abstain")
        n_voters = (
            await self._s.scalar(
                select(func.count(SubscriptionMember.id)).where(
                    SubscriptionMember.subscription_id == subscription_id,
                    SubscriptionMember.role == "voter",
                )
            )
            or 0
        )
        n_voters = int(n_voters)
        fraction_floor = math.ceil(sub.min_fraction * n_voters)
        threshold = max(sub.min_votes, fraction_floor)
        quorum_cleared = n_voters > 0 and yes >= threshold
        return {
            "subscription_id": subscription_id,
            "run_id": run_id,
            "yes": yes,
            "no": no,
            "abstain": abstain,
            "n_voters": n_voters,
            "threshold": threshold,
            "quorum_cleared": quorum_cleared,
        }

    async def fanout_group_notification(
        self,
        *,
        subscription_id: str,
        run_id: str,
        title: str,
        summary: str,
        new_paper_count: int,
    ) -> list[Notification]:
        """Create one Notification per member when quorum clears.

        Idempotent per (subscription, run, recipient): if a row already
        exists for that triple, it is left alone so a stray double-tally
        doesn't double-notify. Returns the freshly-created rows (may be
        empty if every member already had one).
        """
        sub = await self._s.get(LiteratureWatchSubscription, subscription_id)
        if sub is None:
            raise SubscriptionError(f"Subscription {subscription_id!r} not found.")
        members = await self.list_members(subscription_id)
        if not members:
            return []
        existing_user_ids = set(
            (
                await self._s.scalars(
                    select(Notification.user_id).where(
                        Notification.subscription_id == subscription_id,
                        Notification.run_id == run_id,
                    )
                )
            ).all()
        )
        created: list[Notification] = []
        for member in members:
            if member.user_id in existing_user_ids:
                continue
            notif = Notification(
                user_id=member.user_id,
                watch_id=sub.watch_id,
                run_id=run_id,
                subscription_id=subscription_id,
                title=title[:300],
                summary=summary,
                new_paper_count=new_paper_count,
            )
            self._s.add(notif)
            created.append(notif)
        await self._s.flush()
        return created


# ── Account layer (Sprint A1) ────────────────────────────────────────────


class AccountError(Exception):
    """Raised on invalid account-layer state changes — invalid status
    transition, duplicate site code, missing parent account, etc.
    Translated to 4xx by the endpoint layer."""


class AccountRepository:
    """CRUD for the Account / ClinicalTrial / AccountSite + their joins.

    Membership semantics:
      • owner_user_id is the implicit primary owner (one per Account).
      • AccountMember rows give explicit per-user role grants. The
        owner_user_id row is also represented as an AccountMember with
        role='owner' for consistency.
    """

    _ALLOWED_ACCOUNT_STATUS = ("active", "archived")
    _ALLOWED_MEMBER_ROLES = ("owner", "admin", "member", "observer")
    _ALLOWED_SITE_STATUS = ("active", "inactive")
    _ALLOWED_TRIAL_STATUS = ("design", "draft", "deployed", "locked", "archived")
    _ALLOWED_TRIAL_PHASES = (
        "phase_1",
        "phase_2",
        "phase_3",
        "phase_4",
        "observational",
        "feasibility",
    )

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Accounts ──────────────────────────────────────────────────────

    async def create_account(
        self,
        *,
        name: str,
        owner_user_id: str | None = None,
        description: str = "",
    ) -> Account:
        if not name.strip():
            raise AccountError("Account name must be non-empty.")
        existing = await self._s.scalar(select(Account).where(Account.name == name))
        if existing is not None:
            raise AccountError(f"Account named {name!r} already exists.")
        account = Account(
            name=name.strip(),
            description=description,
            owner_user_id=owner_user_id,
            status="active",
        )
        self._s.add(account)
        await self._s.flush()
        if owner_user_id is not None:
            self._s.add(
                AccountMember(
                    account_id=account.id,
                    user_id=owner_user_id,
                    role="owner",
                    invited_by_user_id=owner_user_id,
                )
            )
            await self._s.flush()
        return account

    async def get_account(self, account_id: str) -> Account | None:
        return await self._s.get(Account, account_id)

    async def list_accounts_for_user(self, user_id: str) -> list[Account]:
        """Return every account where the user owns OR is a member."""
        member_ids = select(AccountMember.account_id).where(AccountMember.user_id == user_id)
        stmt = (
            select(Account)
            .where((Account.owner_user_id == user_id) | (Account.id.in_(member_ids)))
            .order_by(Account.created_at.desc())
        )
        return list((await self._s.scalars(stmt)).all())

    async def update_account(self, account_id: str, **kwargs: object) -> Account | None:
        account = await self._s.get(Account, account_id)
        if account is None:
            return None
        if "status" in kwargs:
            status = kwargs["status"]
            if status not in self._ALLOWED_ACCOUNT_STATUS:
                raise AccountError(
                    f"Invalid status {status!r}; choose {', '.join(self._ALLOWED_ACCOUNT_STATUS)}."
                )
        for k, v in kwargs.items():
            if hasattr(account, k):
                setattr(account, k, v)
        await self._s.flush()
        return account

    async def archive_account(self, account_id: str) -> bool:
        account = await self._s.get(Account, account_id)
        if account is None:
            return False
        account.status = "archived"
        await self._s.flush()
        return True

    # ── Members ───────────────────────────────────────────────────────

    async def add_member(
        self,
        *,
        account_id: str,
        user_id: str,
        role: str = "member",
        invited_by_user_id: str | None = None,
    ) -> AccountMember:
        if role not in self._ALLOWED_MEMBER_ROLES:
            raise AccountError(
                f"Invalid member role {role!r}; choose {', '.join(self._ALLOWED_MEMBER_ROLES)}."
            )
        account = await self._s.get(Account, account_id)
        if account is None:
            raise AccountError(f"Account {account_id!r} not found.")
        # Idempotent upsert keyed on (account, user).
        existing = await self._s.scalar(
            select(AccountMember).where(
                AccountMember.account_id == account_id,
                AccountMember.user_id == user_id,
            )
        )
        if existing is not None:
            existing.role = role
            existing.invited_by_user_id = invited_by_user_id
            await self._s.flush()
            return existing
        member = AccountMember(
            account_id=account_id,
            user_id=user_id,
            role=role,
            invited_by_user_id=invited_by_user_id,
        )
        self._s.add(member)
        await self._s.flush()
        return member

    async def remove_member(self, *, account_id: str, user_id: str) -> bool:
        member = await self._s.scalar(
            select(AccountMember).where(
                AccountMember.account_id == account_id,
                AccountMember.user_id == user_id,
            )
        )
        if member is None:
            return False
        # Refuse to remove the owner-member if they're still the
        # owner_user_id; force the operator to transfer ownership first.
        account = await self._s.get(Account, account_id)
        if account is not None and account.owner_user_id == user_id:
            raise AccountError("Cannot remove the account owner; transfer ownership first.")
        await self._s.delete(member)
        await self._s.flush()
        return True

    async def list_members(self, account_id: str) -> list[AccountMember]:
        stmt = (
            select(AccountMember)
            .where(AccountMember.account_id == account_id)
            .order_by(AccountMember.joined_at)
        )
        return list((await self._s.scalars(stmt)).all())

    async def is_member(self, *, account_id: str, user_id: str) -> AccountMember | None:
        result: AccountMember | None = await self._s.scalar(
            select(AccountMember).where(
                AccountMember.account_id == account_id,
                AccountMember.user_id == user_id,
            )
        )
        return result

    # ── Account sites ─────────────────────────────────────────────────

    async def create_site(
        self,
        account_id: str,
        *,
        name: str,
        code: str | None = None,
        address: str = "",
        contact_email: str | None = None,
        pi_name: str | None = None,
    ) -> AccountSite:
        account = await self._s.get(Account, account_id)
        if account is None:
            raise AccountError(f"Account {account_id!r} not found.")
        if code is not None:
            existing = await self._s.scalar(
                select(AccountSite).where(
                    AccountSite.account_id == account_id,
                    AccountSite.code == code,
                )
            )
            if existing is not None:
                raise AccountError(f"Site code {code!r} already used in this account.")
        site = AccountSite(
            account_id=account_id,
            name=name,
            code=code,
            address=address,
            contact_email=contact_email,
            pi_name=pi_name,
            status="active",
        )
        self._s.add(site)
        await self._s.flush()
        return site

    async def list_sites(self, account_id: str) -> list[AccountSite]:
        stmt = (
            select(AccountSite)
            .where(AccountSite.account_id == account_id)
            .order_by(AccountSite.created_at)
        )
        return list((await self._s.scalars(stmt)).all())

    async def get_site(self, site_id: str) -> AccountSite | None:
        return await self._s.get(AccountSite, site_id)

    async def update_site(self, site_id: str, **kwargs: object) -> AccountSite | None:
        site = await self._s.get(AccountSite, site_id)
        if site is None:
            return None
        if "status" in kwargs:
            status = kwargs["status"]
            if status not in self._ALLOWED_SITE_STATUS:
                raise AccountError(
                    f"Invalid status {status!r}; choose {', '.join(self._ALLOWED_SITE_STATUS)}."
                )
        for k, v in kwargs.items():
            if hasattr(site, k):
                setattr(site, k, v)
        await self._s.flush()
        return site

    # ── Trials ────────────────────────────────────────────────────────

    async def create_trial(
        self,
        account_id: str,
        *,
        title: str,
        sponsor: str = "",
        indication: str = "",
        phase: str | None = None,
        protocol_id: str | None = None,
        created_by_user_id: str | None = None,
    ) -> ClinicalTrial:
        account = await self._s.get(Account, account_id)
        if account is None:
            raise AccountError(f"Account {account_id!r} not found.")
        if phase is not None and phase not in self._ALLOWED_TRIAL_PHASES:
            raise AccountError(
                f"Invalid phase {phase!r}; choose {', '.join(self._ALLOWED_TRIAL_PHASES)}."
            )
        trial = ClinicalTrial(
            account_id=account_id,
            title=title,
            sponsor=sponsor,
            indication=indication,
            phase=phase,
            protocol_id=protocol_id,
            status="design",
            created_by_user_id=created_by_user_id,
        )
        self._s.add(trial)
        await self._s.flush()
        return trial

    async def get_trial(self, trial_id: str) -> ClinicalTrial | None:
        return await self._s.get(ClinicalTrial, trial_id)

    async def list_trials_for_account(
        self,
        account_id: str,
        *,
        status: str | None = None,
    ) -> list[ClinicalTrial]:
        stmt = (
            select(ClinicalTrial)
            .where(ClinicalTrial.account_id == account_id)
            .order_by(ClinicalTrial.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(ClinicalTrial.status == status)
        return list((await self._s.scalars(stmt)).all())

    async def update_trial(self, trial_id: str, **kwargs: object) -> ClinicalTrial | None:
        trial = await self._s.get(ClinicalTrial, trial_id)
        if trial is None:
            return None
        if "status" in kwargs:
            status = kwargs["status"]
            if status not in self._ALLOWED_TRIAL_STATUS:
                raise AccountError(
                    f"Invalid status {status!r}; choose {', '.join(self._ALLOWED_TRIAL_STATUS)}."
                )
        if "phase" in kwargs:
            phase = kwargs["phase"]
            if phase is not None and phase not in self._ALLOWED_TRIAL_PHASES:
                raise AccountError(
                    f"Invalid phase {phase!r}; choose {', '.join(self._ALLOWED_TRIAL_PHASES)}."
                )
        for k, v in kwargs.items():
            if hasattr(trial, k):
                setattr(trial, k, v)
        await self._s.flush()
        return trial

    # ── Trial ↔ Site assignment ──────────────────────────────────────

    async def assign_site_to_trial(
        self,
        *,
        trial_id: str,
        account_site_id: str,
        notes: str = "",
    ) -> TrialSite:
        trial = await self._s.get(ClinicalTrial, trial_id)
        if trial is None:
            raise AccountError(f"Trial {trial_id!r} not found.")
        site = await self._s.get(AccountSite, account_site_id)
        if site is None:
            raise AccountError(f"Account site {account_site_id!r} not found.")
        if site.account_id != trial.account_id:
            raise AccountError("Site and trial must belong to the same account.")
        if site.status != "active":
            raise AccountError(f"Cannot assign inactive site {site.name!r} to a trial.")
        # Idempotent upsert.
        existing = await self._s.scalar(
            select(TrialSite).where(
                TrialSite.trial_id == trial_id,
                TrialSite.account_site_id == account_site_id,
            )
        )
        if existing is not None:
            existing.status = "active"
            existing.notes = notes
            await self._s.flush()
            return existing
        ts = TrialSite(
            trial_id=trial_id,
            account_site_id=account_site_id,
            notes=notes,
            status="active",
        )
        self._s.add(ts)
        await self._s.flush()
        return ts

    async def unassign_site_from_trial(self, *, trial_id: str, account_site_id: str) -> bool:
        row = await self._s.scalar(
            select(TrialSite).where(
                TrialSite.trial_id == trial_id,
                TrialSite.account_site_id == account_site_id,
            )
        )
        if row is None:
            return False
        await self._s.delete(row)
        await self._s.flush()
        return True

    async def list_trial_sites(self, trial_id: str) -> list[TrialSite]:
        stmt = (
            select(TrialSite).where(TrialSite.trial_id == trial_id).order_by(TrialSite.activated_at)
        )
        return list((await self._s.scalars(stmt)).all())

    async def transfer_ownership(
        self,
        *,
        account_id: str,
        new_owner_user_id: str,
    ) -> Account:
        """Transfer Account.owner_user_id to a new user.

        New owner must already be a member. Previous owner downgrades to
        'admin' (kept inside the account, not removed). New owner gets
        promoted to 'owner'. Idempotent when no-op.
        """
        account = await self._s.get(Account, account_id)
        if account is None:
            raise AccountError(f"Account {account_id!r} not found.")
        if account.owner_user_id == new_owner_user_id:
            return account
        new_owner_membership = await self._s.scalar(
            select(AccountMember).where(
                AccountMember.account_id == account_id,
                AccountMember.user_id == new_owner_user_id,
            )
        )
        if new_owner_membership is None:
            raise AccountError("New owner must already be a member of the account.")
        old_owner_id = account.owner_user_id
        if old_owner_id is not None:
            old_membership = await self._s.scalar(
                select(AccountMember).where(
                    AccountMember.account_id == account_id,
                    AccountMember.user_id == old_owner_id,
                )
            )
            if old_membership is not None:
                old_membership.role = "admin"
        new_owner_membership.role = "owner"
        account.owner_user_id = new_owner_user_id
        await self._s.flush()
        return account

    async def bind_trial_artefact(
        self,
        *,
        trial_id: str,
        kind: str,
        thread_id: str | None,
    ) -> ClinicalTrial:
        """Bind a Thread to one of the Trial's artefact slots.

        kind ∈ {registration, irb, sap, csr, manuscript}. Pass
        thread_id=None to unbind. Returns the updated Trial.
        """
        _ALLOWED_KINDS = {
            "registration": "registration_thread_id",
            "irb": "irb_thread_id",
            "sap": "sap_thread_id",
            "csr": "csr_thread_id",
            "manuscript": "manuscript_thread_id",
        }
        column = _ALLOWED_KINDS.get(kind)
        if column is None:
            raise AccountError(
                f"Invalid artefact kind {kind!r}; choose {', '.join(_ALLOWED_KINDS)}."
            )
        trial = await self._s.get(ClinicalTrial, trial_id)
        if trial is None:
            raise AccountError(f"Trial {trial_id!r} not found.")
        if thread_id is not None:
            thread = await self._s.get(Thread, thread_id)
            if thread is None:
                raise AccountError(f"Thread {thread_id!r} not found.")
        setattr(trial, column, thread_id)
        await self._s.flush()
        return trial

    # ── Cross-store resolution ───────────────────────────────────────

    async def resolve_trial_for_ecrf_study(self, ecrf_study_id: str) -> ClinicalTrial | None:
        """Walk EcrfStudy.trial_id → ClinicalTrial. Returns None for
        legacy studies that pre-date the account layer."""
        study = await self._s.get(EcrfStudy, ecrf_study_id)
        if study is None or study.trial_id is None:
            return None
        return await self._s.get(ClinicalTrial, study.trial_id)
