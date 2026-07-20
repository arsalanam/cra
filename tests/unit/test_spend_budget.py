"""T1 spend quota — Q3: budget enforcement (hard 429 stop, D3).

Pins the enforcement invariants: cap boundary behaviour, disabled
budgets, the budget-start-date window, unknown/None accounts passing
through, and the JSON payload shape the frontend consumes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.models import Account, SpendLedger
from research_assistant.services.spend import (
    AccountBudgetExceeded,
    build_budget_payload,
    build_spend_report,
    enforce_account_budget,
    get_account_spend,
    get_budget_status,
)


async def _seed_account(
    db: AsyncSession,
    *,
    budget_usd: float = 0.0,
    budget_start_at: datetime | None = None,
    warn_percent: int = 80,
) -> Account:
    account = Account(
        name=f"Budget program {budget_usd}-{warn_percent}",
        status="active",
        budget_usd=budget_usd,
        budget_start_at=budget_start_at,
        budget_warn_percent=warn_percent,
    )
    db.add(account)
    await db.flush()
    return account


def _row(account_id: str, usd: float, *, at: datetime | None = None) -> SpendLedger:
    row = SpendLedger(account_id=account_id, category="turn", quantity=1, usd=usd)
    if at is not None:
        row.created_at = at
    return row


# ── get_account_spend ───────────────────────────────────────────────────


async def test_spend_sums_only_this_account(db_session: AsyncSession) -> None:
    a = await _seed_account(db_session, budget_usd=10)
    b = await _seed_account(db_session, budget_usd=20)
    db_session.add_all([_row(a.id, 1.5), _row(a.id, 2.5), _row(b.id, 99.0)])
    await db_session.flush()

    assert await get_account_spend(db_session, account_id=a.id) == pytest.approx(4.0)


async def test_spend_window_excludes_pre_start_rows(db_session: AsyncSession) -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    a = await _seed_account(db_session, budget_usd=10, budget_start_at=start)
    db_session.add_all(
        [
            _row(a.id, 100.0, at=datetime(2026, 6, 1, tzinfo=UTC)),  # before window
            _row(a.id, 3.0, at=datetime(2026, 7, 2, tzinfo=UTC)),
        ]
    )
    await db_session.flush()

    status = await get_budget_status(db_session, account_id=a.id)
    assert status is not None
    assert status.spent_usd == pytest.approx(3.0)


# ── enforce_account_budget ──────────────────────────────────────────────


async def test_enforce_under_budget_passes(db_session: AsyncSession) -> None:
    a = await _seed_account(db_session, budget_usd=500.0)
    db_session.add(_row(a.id, 499.99))
    await db_session.flush()

    status = await enforce_account_budget(db_session, account_id=a.id)
    assert status is not None and status.remaining_usd == pytest.approx(0.01)


async def test_enforce_at_cap_raises_429_material(db_session: AsyncSession) -> None:
    a = await _seed_account(db_session, budget_usd=500.0)
    db_session.add(_row(a.id, 500.0))
    await db_session.flush()

    with pytest.raises(AccountBudgetExceeded) as exc_info:
        await enforce_account_budget(db_session, account_id=a.id)
    assert exc_info.value.status.limit_usd == 500.0
    assert exc_info.value.status.spent_usd == pytest.approx(500.0)


async def test_enforce_disabled_budget_never_raises(db_session: AsyncSession) -> None:
    a = await _seed_account(db_session, budget_usd=0.0)
    db_session.add(_row(a.id, 1_000_000.0))
    await db_session.flush()

    status = await enforce_account_budget(db_session, account_id=a.id)
    assert status is not None and not status.enabled


async def test_enforce_none_account_passes(db_session: AsyncSession) -> None:
    assert await enforce_account_budget(db_session, account_id=None) is None


async def test_enforce_unknown_account_passes(db_session: AsyncSession) -> None:
    assert await enforce_account_budget(db_session, account_id="nope") is None


async def test_enforce_ignores_pre_start_spend(db_session: AsyncSession) -> None:
    """Raising then restarting a budget (new start date) resumes work."""
    start = datetime(2026, 7, 10, tzinfo=UTC)
    a = await _seed_account(db_session, budget_usd=100.0, budget_start_at=start)
    db_session.add(_row(a.id, 5_000.0, at=datetime(2026, 7, 1, tzinfo=UTC)))
    await db_session.flush()

    status = await enforce_account_budget(db_session, account_id=a.id)
    assert status is not None and status.spent_usd == 0.0


# ── payload + warning threshold ─────────────────────────────────────────


async def test_payload_shape_and_warning(db_session: AsyncSession) -> None:
    a = await _seed_account(db_session, budget_usd=100.0, warn_percent=80)
    db_session.add(_row(a.id, 85.0))
    await db_session.flush()

    payload = build_budget_payload(await get_budget_status(db_session, account_id=a.id))
    assert payload is not None
    assert payload["enabled"] is True
    assert payload["limit_usd"] == 100.0
    assert payload["spent_usd"] == pytest.approx(85.0)
    assert payload["remaining_usd"] == pytest.approx(15.0)
    assert payload["percent_used"] == pytest.approx(85.0)
    assert payload["warning"] is True


async def test_payload_below_warn_threshold(db_session: AsyncSession) -> None:
    a = await _seed_account(db_session, budget_usd=100.0, warn_percent=80)
    db_session.add(_row(a.id, 10.0))
    await db_session.flush()

    payload = build_budget_payload(await get_budget_status(db_session, account_id=a.id))
    assert payload is not None and payload["warning"] is False


def test_payload_none_passthrough() -> None:
    assert build_budget_payload(None) is None


# ── build_spend_report (Q4) ─────────────────────────────────────────────


async def test_spend_report_breakdowns_and_burn(db_session: AsyncSession) -> None:
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    a = await _seed_account(db_session, budget_usd=100.0, budget_start_at=start)
    db_session.add_all(
        [
            # In-window spend across categories and trials.
            SpendLedger(
                account_id=a.id,
                trial_id="trial-x",
                category="turn",
                quantity=1000,
                usd=10.0,
                created_at=now - timedelta(days=2),
            ),
            SpendLedger(
                account_id=a.id,
                category="search",
                quantity=5,
                usd=0.04,
                created_at=now - timedelta(days=1),
            ),
            # Older than 7 days: counts toward budget, not toward burn.
            SpendLedger(
                account_id=a.id,
                category="embedding",
                quantity=50_000,
                usd=1.0,
                created_at=datetime(2026, 7, 2, tzinfo=UTC),
            ),
            # Before the budget window entirely: invisible to the report.
            SpendLedger(
                account_id=a.id,
                category="turn",
                quantity=999,
                usd=500.0,
                created_at=datetime(2026, 6, 1, tzinfo=UTC),
            ),
        ]
    )
    await db_session.flush()

    report = await build_spend_report(db_session, account_id=a.id, now=now)
    assert report is not None
    assert report["budget"]["spent_usd"] == pytest.approx(11.04)
    assert report["by_category"]["turn"]["usd"] == pytest.approx(10.0)
    assert report["by_category"]["search"]["quantity"] == 5
    assert report["by_trial"]["trial-x"] == pytest.approx(10.0)
    assert report["by_trial"]["_untargeted"] == pytest.approx(1.04)
    # Burn: only the last 7 days (10.0 + 0.04) / 7.
    assert report["burn_usd_per_day_7d"] == pytest.approx(10.04 / 7, rel=1e-3)
    # Projection exists and is a date string beyond now.
    assert report["projected_exhaustion_date"] is not None
    assert report["projected_exhaustion_date"] > "2026-07-19"


async def test_spend_report_unknown_account_is_none(db_session: AsyncSession) -> None:
    assert await build_spend_report(db_session, account_id="nope") is None


async def test_spend_report_no_burn_no_projection(db_session: AsyncSession) -> None:
    a = await _seed_account(db_session, budget_usd=100.0)
    report = await build_spend_report(db_session, account_id=a.id)
    assert report is not None
    assert report["burn_usd_per_day_7d"] == 0.0
    assert report["projected_exhaustion_date"] is None
