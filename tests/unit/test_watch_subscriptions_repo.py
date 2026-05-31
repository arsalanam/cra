"""Group-level living-review subscription repository (P2 #4) — CRUD,
member management, vote recording, quorum tally, and group-notification
fan-out.

The quorum rule is `yes ≥ max(min_votes, ceil(min_fraction × n_voters))`
where `n_voters` counts members with role='voter'. Observers don't
count toward the denominator and can't vote.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.models import (
    LiteratureWatch,
    Notification,
    User,
    WatchRun,
)
from research_assistant.persistence.repository import (
    SubscriptionError,
    SubscriptionRepository,
    WatchRepository,
)
from research_assistant.services.watch_subscriptions import record_vote_and_fanout


async def _seed_user(db: AsyncSession, *, sub: str, email: str | None = None) -> User:
    user = User(cognito_sub=sub, email=email or f"{sub}@example.com")
    db.add(user)
    await db.flush()
    return user


async def _seed_watch(db: AsyncSession, *, name: str = "watch-1") -> LiteratureWatch:
    watch = await WatchRepository(db).create_watch(
        name=name,
        pico_json='{"P":"x","I":"y","C":"z","O":"w"}',
        search_query="cancer[ti]",
        sources_json='["pubmed"]',
        schedule_cron="0 9 * * 1",
    )
    return watch


async def _seed_run(db: AsyncSession, watch_id: str) -> WatchRun:
    run = await WatchRepository(db).add_run(watch_id)
    return run


# ── Subscription CRUD ────────────────────────────────────────────────────


async def test_create_subscription_auto_adds_owner_as_voter(
    db_session: AsyncSession,
) -> None:
    user = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(
        name="Cardio guideline", watch_id=watch.id, owner_user_id=user.id
    )
    members = await repo.list_members(sub.id)
    assert len(members) == 1
    assert members[0].user_id == user.id
    assert members[0].role == "voter"


async def test_create_subscription_rejects_invalid_fraction(
    db_session: AsyncSession,
) -> None:
    watch = await _seed_watch(db_session)
    repo = SubscriptionRepository(db_session)
    with pytest.raises(SubscriptionError, match="min_fraction"):
        await repo.create_subscription(name="x", watch_id=watch.id, min_fraction=1.5)


async def test_create_subscription_rejects_min_votes_zero(
    db_session: AsyncSession,
) -> None:
    watch = await _seed_watch(db_session)
    repo = SubscriptionRepository(db_session)
    with pytest.raises(SubscriptionError, match="min_votes"):
        await repo.create_subscription(name="x", watch_id=watch.id, min_votes=0)


async def test_create_subscription_rejects_unknown_watch(
    db_session: AsyncSession,
) -> None:
    repo = SubscriptionRepository(db_session)
    with pytest.raises(SubscriptionError, match="Watch"):
        await repo.create_subscription(name="x", watch_id="nope")


async def test_list_subscriptions_for_user_includes_owned_and_member(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    other = await _seed_user(db_session, sub="sub-other")
    watch = await _seed_watch(db_session)
    repo = SubscriptionRepository(db_session)
    owned = await repo.create_subscription(name="Owned", watch_id=watch.id, owner_user_id=owner.id)
    invited_to = await repo.create_subscription(
        name="Invited", watch_id=watch.id, owner_user_id=other.id
    )
    await repo.add_member(subscription_id=invited_to.id, user_id=owner.id, role="voter")

    subs = await repo.list_subscriptions_for_user(owner.id)
    sub_ids = {s.id for s in subs}
    assert owned.id in sub_ids
    assert invited_to.id in sub_ids


# ── Member management ───────────────────────────────────────────────────


async def test_add_member_idempotent_updates_role(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    invitee = await _seed_user(db_session, sub="sub-invitee")
    watch = await _seed_watch(db_session)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    await repo.add_member(subscription_id=sub.id, user_id=invitee.id, role="observer")
    upgraded = await repo.add_member(subscription_id=sub.id, user_id=invitee.id, role="voter")
    assert upgraded.role == "voter"
    # Still only one row for that (sub, user) pair.
    members = [m for m in await repo.list_members(sub.id) if m.user_id == invitee.id]
    assert len(members) == 1


async def test_add_member_rejects_unknown_role(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    invitee = await _seed_user(db_session, sub="sub-i")
    with pytest.raises(SubscriptionError, match="member role"):
        await repo.add_member(subscription_id=sub.id, user_id=invitee.id, role="bystander")


async def test_remove_member_returns_false_for_missing(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    assert await repo.remove_member(subscription_id=sub.id, user_id="ghost") is False


# ── Vote recording ──────────────────────────────────────────────────────


async def test_record_vote_rejects_non_member(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    stranger = await _seed_user(db_session, sub="sub-stranger")
    with pytest.raises(SubscriptionError, match="voting members"):
        await repo.record_vote(
            subscription_id=sub.id,
            run_id=run.id,
            voter_user_id=stranger.id,
            vote="yes",
        )


async def test_record_vote_rejects_observer(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    observer = await _seed_user(db_session, sub="sub-observer")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    await repo.add_member(subscription_id=sub.id, user_id=observer.id, role="observer")
    with pytest.raises(SubscriptionError, match="voting members"):
        await repo.record_vote(
            subscription_id=sub.id,
            run_id=run.id,
            voter_user_id=observer.id,
            vote="yes",
        )


async def test_record_vote_rejects_invalid_value(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    with pytest.raises(SubscriptionError, match="Invalid vote"):
        await repo.record_vote(
            subscription_id=sub.id,
            run_id=run.id,
            voter_user_id=owner.id,
            vote="maybe",
        )


async def test_record_vote_is_idempotent_upsert(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    first = await repo.record_vote(
        subscription_id=sub.id,
        run_id=run.id,
        voter_user_id=owner.id,
        vote="yes",
        rationale="initial",
    )
    second = await repo.record_vote(
        subscription_id=sub.id,
        run_id=run.id,
        voter_user_id=owner.id,
        vote="no",
        rationale="changed mind",
    )
    assert first.id == second.id  # Same row, mutated.
    assert second.vote == "no"
    votes = await repo.list_votes(subscription_id=sub.id, run_id=run.id)
    assert len(votes) == 1


async def test_record_vote_rejects_run_from_other_watch(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch_a = await _seed_watch(db_session, name="watch-A")
    watch_b = await _seed_watch(db_session, name="watch-B")
    run_b = await _seed_run(db_session, watch_b.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch_a.id, owner_user_id=owner.id)
    with pytest.raises(SubscriptionError, match="not found on this"):
        await repo.record_vote(
            subscription_id=sub.id,
            run_id=run_b.id,
            voter_user_id=owner.id,
            vote="yes",
        )


# ── Quorum tally ─────────────────────────────────────────────────────────


async def test_tally_quorum_cleared_when_min_votes_threshold_dominates(
    db_session: AsyncSession,
) -> None:
    """5 voters, min_votes=3, min_fraction=0.5 → threshold = max(3, 3) = 3."""
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(
        name="x",
        watch_id=watch.id,
        owner_user_id=owner.id,
        min_votes=3,
        min_fraction=0.5,
    )
    # 4 more voters (5 total).
    for i in range(4):
        u = await _seed_user(db_session, sub=f"sub-v{i}")
        await repo.add_member(subscription_id=sub.id, user_id=u.id, role="voter")

    voters = (await repo.list_members(sub.id))[:3]
    for v in voters:
        await repo.record_vote(
            subscription_id=sub.id, run_id=run.id, voter_user_id=v.user_id, vote="yes"
        )
    tally = await repo.tally(subscription_id=sub.id, run_id=run.id)
    assert tally["n_voters"] == 5
    assert tally["threshold"] == 3
    assert tally["yes"] == 3
    assert tally["quorum_cleared"] is True


async def test_tally_quorum_threshold_picks_the_higher_of_votes_and_fraction(
    db_session: AsyncSession,
) -> None:
    """10 voters, min_votes=2, min_fraction=0.6 → threshold = max(2, 6) = 6.
    5 yes-votes does NOT clear quorum."""
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(
        name="x",
        watch_id=watch.id,
        owner_user_id=owner.id,
        min_votes=2,
        min_fraction=0.6,
    )
    for i in range(9):
        u = await _seed_user(db_session, sub=f"sub-v{i}")
        await repo.add_member(subscription_id=sub.id, user_id=u.id, role="voter")

    voters = (await repo.list_members(sub.id))[:5]
    for v in voters:
        await repo.record_vote(
            subscription_id=sub.id, run_id=run.id, voter_user_id=v.user_id, vote="yes"
        )
    tally = await repo.tally(subscription_id=sub.id, run_id=run.id)
    assert tally["n_voters"] == 10
    assert tally["threshold"] == 6
    assert tally["yes"] == 5
    assert tally["quorum_cleared"] is False


async def test_tally_observers_not_counted_toward_denominator(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(
        name="x",
        watch_id=watch.id,
        owner_user_id=owner.id,
        min_votes=2,
        min_fraction=0.5,
    )
    observer = await _seed_user(db_session, sub="sub-observer")
    await repo.add_member(subscription_id=sub.id, user_id=observer.id, role="observer")
    # Owner + 1 voter = 2 voters; observers ignored.
    voter = await _seed_user(db_session, sub="sub-voter")
    await repo.add_member(subscription_id=sub.id, user_id=voter.id, role="voter")

    await repo.record_vote(
        subscription_id=sub.id, run_id=run.id, voter_user_id=owner.id, vote="yes"
    )
    await repo.record_vote(
        subscription_id=sub.id, run_id=run.id, voter_user_id=voter.id, vote="yes"
    )
    tally = await repo.tally(subscription_id=sub.id, run_id=run.id)
    assert tally["n_voters"] == 2
    # threshold = max(2, ceil(0.5 * 2)) = max(2, 1) = 2.
    assert tally["threshold"] == 2
    assert tally["quorum_cleared"] is True


async def test_tally_zero_voters_never_clears(db_session: AsyncSession) -> None:
    """A subscription with no voting members can't trip a group alert
    no matter what."""
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=None)
    tally = await repo.tally(subscription_id=sub.id, run_id=run.id)
    assert tally["n_voters"] == 0
    assert tally["quorum_cleared"] is False


# ── Notification fan-out ────────────────────────────────────────────────


async def test_fanout_creates_one_notification_per_member(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    voter = await _seed_user(db_session, sub="sub-voter")
    observer = await _seed_user(db_session, sub="sub-observer")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    await repo.add_member(subscription_id=sub.id, user_id=voter.id, role="voter")
    await repo.add_member(subscription_id=sub.id, user_id=observer.id, role="observer")
    created = await repo.fanout_group_notification(
        subscription_id=sub.id,
        run_id=run.id,
        title="t",
        summary="s",
        new_paper_count=2,
    )
    # Owner + voter + observer = 3 recipients.
    assert len(created) == 3
    # Every notification points to the subscription.
    for n in created:
        assert n.subscription_id == sub.id


async def test_fanout_is_idempotent_per_recipient(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    first = await repo.fanout_group_notification(
        subscription_id=sub.id, run_id=run.id, title="t", summary="s", new_paper_count=1
    )
    second = await repo.fanout_group_notification(
        subscription_id=sub.id, run_id=run.id, title="t", summary="s", new_paper_count=1
    )
    assert len(first) == 1
    assert len(second) == 0  # Owner already notified; no duplicates.
    all_notifs = (
        await db_session.scalars(select(Notification).where(Notification.subscription_id == sub.id))
    ).all()
    assert len(all_notifs) == 1


# ── End-to-end via the service orchestrator ─────────────────────────────


async def test_service_records_vote_and_fans_out_when_quorum_clears(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    voter = await _seed_user(db_session, sub="sub-voter")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(
        name="Cardio guideline",
        watch_id=watch.id,
        owner_user_id=owner.id,
        min_votes=2,
        min_fraction=0.5,
    )
    await repo.add_member(subscription_id=sub.id, user_id=voter.id, role="voter")

    # First yes-vote — below threshold (need 2).
    tally = await record_vote_and_fanout(
        db_session,
        subscription_id=sub.id,
        run_id=run.id,
        voter_user_id=owner.id,
        vote="yes",
    )
    assert tally["quorum_cleared"] is False
    assert tally["notifications_created"] == 0

    # Second yes-vote → quorum clears → fan-out.
    tally2 = await record_vote_and_fanout(
        db_session,
        subscription_id=sub.id,
        run_id=run.id,
        voter_user_id=voter.id,
        vote="yes",
    )
    assert tally2["quorum_cleared"] is True
    assert tally2["notifications_created"] == 2

    # Re-voting after fan-out shouldn't re-fire group alerts.
    tally3 = await record_vote_and_fanout(
        db_session,
        subscription_id=sub.id,
        run_id=run.id,
        voter_user_id=voter.id,
        vote="yes",  # still yes
    )
    assert tally3["quorum_cleared"] is True
    assert tally3["notifications_created"] == 0


async def test_service_rejects_voting_on_paused_subscription(
    db_session: AsyncSession,
) -> None:
    owner = await _seed_user(db_session, sub="sub-owner")
    watch = await _seed_watch(db_session)
    run = await _seed_run(db_session, watch.id)
    repo = SubscriptionRepository(db_session)
    sub = await repo.create_subscription(name="x", watch_id=watch.id, owner_user_id=owner.id)
    await repo.update_subscription(sub.id, status="paused")
    with pytest.raises(SubscriptionError, match="paused"):
        await record_vote_and_fanout(
            db_session,
            subscription_id=sub.id,
            run_id=run.id,
            voter_user_id=owner.id,
            vote="yes",
        )
