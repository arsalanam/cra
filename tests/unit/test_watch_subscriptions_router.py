"""Router-factory smoke test for the watch-subscriptions endpoints (P2 #4).

Pins the route surface — adding or renaming a path should be deliberate;
this test catches accidental regressions."""

from __future__ import annotations

from research_assistant.web.watch_subscriptions import create_subscriptions_router


def test_router_factory_lists_expected_paths() -> None:
    router = create_subscriptions_router()
    paths = {r.path for r in router.routes}
    # CRUD on the subscription itself.
    assert "/watch-subscriptions" in paths
    assert "/watch-subscriptions/{sub_id}" in paths
    # Members.
    assert "/watch-subscriptions/{sub_id}/members" in paths
    assert "/watch-subscriptions/{sub_id}/members/{uid}" in paths
    # Runs + tally + voting.
    assert "/watch-subscriptions/{sub_id}/runs" in paths
    assert "/watch-subscriptions/{sub_id}/runs/{run_id}/vote" in paths
    assert "/watch-subscriptions/{sub_id}/runs/{run_id}/tally" in paths


def test_router_dtos_round_trip() -> None:
    """The DTOs validate against shaped sample data."""
    from datetime import UTC, datetime

    from research_assistant.web.watch_subscriptions import (
        MemberAdd,
        MemberView,
        SubscriptionCreate,
        SubscriptionView,
        TallyView,
        VoteIn,
    )

    now = datetime.now(UTC)
    create = SubscriptionCreate(
        name="Cardio guideline panel",
        watch_id="w-1",
        description="Quarterly review",
        min_votes=3,
        min_fraction=0.5,
    )
    assert create.min_votes == 3

    sv = SubscriptionView(
        id="s1",
        name="x",
        description="",
        watch_id="w-1",
        watch_name="My watch",
        owner_user_id="u-1",
        status="active",
        min_votes=2,
        min_fraction=0.5,
        n_voters=4,
        n_observers=1,
        created_at=now,
        updated_at=now,
    )
    assert sv.n_voters == 4

    member_add = MemberAdd(email="committee@example.com", role="voter")
    assert member_add.email == "committee@example.com"
    assert member_add.user_id is None

    mv = MemberView(user_id="u-2", role="voter", joined_at=now)
    assert mv.email is None

    vote = VoteIn(vote="yes", rationale="Practice-changing trial.")
    assert vote.vote == "yes"

    tally = TallyView(
        subscription_id="s1",
        run_id="r1",
        yes=3,
        no=1,
        abstain=0,
        n_voters=5,
        threshold=3,
        quorum_cleared=True,
        notifications_created=5,
    )
    assert tally.quorum_cleared is True


def test_vote_dto_rejects_invalid_vote_string() -> None:
    import pytest
    from pydantic import ValidationError

    from research_assistant.web.watch_subscriptions import VoteIn

    with pytest.raises(ValidationError):
        VoteIn(vote="maybe")


def test_subscription_create_rejects_invalid_fraction() -> None:
    import pytest
    from pydantic import ValidationError

    from research_assistant.web.watch_subscriptions import SubscriptionCreate

    with pytest.raises(ValidationError):
        SubscriptionCreate(name="x", watch_id="w-1", min_fraction=1.5)


def test_subscription_create_rejects_zero_min_votes() -> None:
    import pytest
    from pydantic import ValidationError

    from research_assistant.web.watch_subscriptions import SubscriptionCreate

    with pytest.raises(ValidationError):
        SubscriptionCreate(name="x", watch_id="w-1", min_votes=0)
