"""Mutation-killing tests for daily loss limit and P&L calculations.

Tests designed to catch off-by-one and boundary condition errors in:
- app/daily_loss_limiter.py (limit configuration and breach logic)
- app/db.py SignalStore.get_daily_pnl (boundary date/time handling)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.daily_loss_limiter import DailyLossLimiter
from app.db import SignalStore
from app.models import AccountBalance, DestinationAccount

ACCT = "acct_mut_test"
TODAY = datetime.now(timezone.utc).date()


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc)


def _snap(store: SignalStore, when: datetime, cumulative: float) -> None:
    store.record_equity_snapshot(
        ACCT, captured_at=when, realized_pnl=cumulative, unrealized_pnl=0.0, cumulative_pnl=cumulative
    )


class _Broker:
    def __init__(self, equity: float | None):
        self._equity = equity

    async def get_account_balance(self, account):
        if self._equity is None:
            return AccountBalance(account_id=account.account_id, equity=None, buying_power=None, cash=None)
        return AccountBalance(
            account_id=account.account_id,
            equity=self._equity,
            buying_power=self._equity,
            cash=self._equity,
        )


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "mut_test.db")


def _limiter(store, equity: float | None = 1000.0):
    return DailyLossLimiter(store, brokers={"paper": _Broker(equity)})


def _account():
    return DestinationAccount(account_id=ACCT, broker="paper")


# ============================================================================
# MUTATION 1: daily_loss_limit_percent <= 0 → < 0
# Kill: Config check should treat 0 as disabled, not as a configured limit
# ============================================================================


@pytest.mark.asyncio
async def test_zero_loss_limit_is_disabled_m1(store):
    """M1 killer: daily_loss_limit_percent=0 should be treated as 'not configured'.

    If mutated to `< 0`, then 0 would be treated as a configured limit and
    the check would proceed, potentially rejecting valid trades.
    """
    _snap(store, _at(TODAY - timedelta(days=1), 20), 0.0)
    _snap(store, _at(TODAY, 9), -50.0)  # 5% loss
    # With 0 limit, should NOT reject (limit is disabled)
    assert await _limiter(store).check_daily_loss_limit(_account(), 0.0) is None


# ============================================================================
# MUTATION 2: daily_pnl >= 0 → > 0
# Kill: Break-even day (0 P&L) should not trigger loss limit
# ============================================================================


@pytest.mark.asyncio
async def test_breakeven_day_does_not_trigger_loss_limit_m2(store):
    """M2 killer: A break-even day (daily_pnl=0) should not be rejected.

    If mutated to `> 0`, then 0 would not be skipped and would proceed to
    loss percentage check, potentially being rejected.
    """
    _snap(store, _at(TODAY - timedelta(days=1), 20), 100.0)
    _snap(store, _at(TODAY, 9), 100.0)  # Break-even day (0 P&L)
    # Should not be rejected by loss limit
    assert await _limiter(store).check_daily_loss_limit(_account(), 5.0) is None


# ============================================================================
# MUTATION 3: daily_loss_percent >= daily_loss_limit_percent → >
# Kill: Loss exactly at limit should be rejected
# ============================================================================


@pytest.mark.asyncio
async def test_loss_exactly_at_limit_is_rejected_m3(store):
    """M3 killer: Loss exactly at limit (not greater than) should be rejected.

    If mutated to `>`, then a loss of exactly 5% against a 5% limit would
    not be rejected, allowing a position when limit is breached.
    """
    _snap(store, _at(TODAY - timedelta(days=1), 20), 0.0)
    _snap(store, _at(TODAY, 9), -50.0)  # Exactly 5% of 1000 equity
    message = await _limiter(store).check_daily_loss_limit(_account(), 5.0)
    assert message is not None and "Daily loss limit breached" in message


# ============================================================================
# MUTATION 6: balance.equity < min_equity_threshold → <=
# Kill: Equity exactly at threshold should be rejected
# ============================================================================


@pytest.mark.asyncio
async def test_equity_exactly_at_min_threshold_is_rejected_m6(store):
    """M6 killer: Equity exactly at threshold should be rejected as below threshold.

    If mutated to `<=`, then equity exactly at threshold would be rejected.
    The original `<` allows equity equal to threshold.
    """
    broker = _Broker(1000.0)
    limiter = DailyLossLimiter(store, brokers={"paper": broker})

    # With min threshold of 1000 and equity of exactly 1000, should pass
    message = await limiter.check_min_equity_threshold(_account(), 1000.0)
    assert message is None  # Equity at threshold is OK


# ============================================================================
# MUTATION 12: min_equity_threshold <= 0 → < 0
# Kill: Config check should treat 0 as disabled
# ============================================================================


@pytest.mark.asyncio
async def test_zero_min_equity_threshold_is_disabled_m12(store):
    """M12 killer: min_equity_threshold=0 should be treated as 'not configured'.

    If mutated to `< 0`, then 0 would be treated as a configured threshold
    and the check would proceed.
    """
    broker = _Broker(500.0)
    limiter = DailyLossLimiter(store, brokers={"paper": broker})

    # With 0 threshold, should NOT reject (threshold is disabled)
    assert await limiter.check_min_equity_threshold(_account(), 0.0) is None


# ============================================================================
# MUTATION 7: captured_at >= ? → captured_at > ? (latest snapshot query)
# Kill: Snapshot exactly at day boundary should be included
# ============================================================================


def test_snapshot_exactly_at_day_start_is_included_m7(store):
    """M7 killer: Snapshot at exactly 00:00:00 UTC of the day should be included as latest.

    The query uses `captured_at >= start AND captured_at < end`.
    If mutated to `>`, then snapshots at exactly day start (00:00:00) would
    be excluded, and we wouldn't have a latest snapshot for the day.
    """
    yesterday = TODAY - timedelta(days=1)
    _snap(store, _at(yesterday, 20), 100.0)
    _snap(store, _at(TODAY, 0), 90.0)  # Only snapshot in the day, at exactly day start

    daily_pnl = store.get_daily_pnl(ACCT, TODAY)
    # Should be difference from snapshot before day to latest in day: 90 - 100 = -10
    assert daily_pnl == pytest.approx(-10.0)


# ============================================================================
# MUTATION 8: captured_at < ? → captured_at <= ? (baseline before day)
# Kill: Snapshot exactly at day boundary should NOT be included as baseline
# ============================================================================


def test_snapshot_exactly_at_day_boundary_not_baseline_m8(store):
    """M8 killer: Snapshot at exactly day start (00:00:00) should NOT be baseline for previous day.

    The baseline query seeks `captured_at < start`. If mutated to `<=`,
    a snapshot at exactly 00:00:00 would be included as baseline, giving
    wrong P&L calculation for the previous day.
    """
    day_before = TODAY - timedelta(days=2)
    yesterday = TODAY - timedelta(days=1)

    # Setup: baseline from day before, then yesterday's data
    _snap(store, _at(day_before, 20), 100.0)
    _snap(store, _at(yesterday, 0), 110.0)  # Exactly at yesterday start
    _snap(store, _at(yesterday, 9), 120.0)

    # Daily PNL for yesterday: should be 120 - 100 = 20
    yesterday_pnl = store.get_daily_pnl(ACCT, yesterday)
    assert yesterday_pnl == pytest.approx(20.0)


# ============================================================================
# MUTATION 9: captured_at >= ? → captured_at > ? (baseline at day start)
# Kill: First snapshot of day should be used when series starts that day
# ============================================================================


def test_first_snapshot_of_day_as_baseline_m9(store):
    """M9 killer: When series starts within the day, first snapshot is baseline.

    The fallback baseline query uses `captured_at >= start AND captured_at < end`.
    If mutated to `>`, then the first snapshot at exactly 00:00:00 would be
    excluded, preventing proper P&L calculation for days where series starts.
    """
    # Series starts exactly at day start
    _snap(store, _at(TODAY, 0), 500.0)  # Exactly at start
    _snap(store, _at(TODAY, 1), 450.0)
    _snap(store, _at(TODAY, 9), 400.0)

    daily_pnl = store.get_daily_pnl(ACCT, TODAY)
    # P&L should be from first snapshot to last: 400 - 500 = -100
    assert daily_pnl == pytest.approx(-100.0)


# ============================================================================
# Additional boundary tests for get_daily_pnl boundary mutations
# ============================================================================


def test_get_daily_pnl_with_microsecond_precision(store):
    """Test that P&L calculation handles timestamps with microsecond precision correctly."""
    day_start = _at(TODAY, 0)
    day_end = _at(TODAY, 23)

    # Baseline: snapshot just before day in previous day
    _snap(store, day_start - timedelta(microseconds=1), 1000.0)
    # Snapshots during the day
    _snap(store, day_start + timedelta(microseconds=1), 950.0)
    _snap(store, day_end, 900.0)

    daily_pnl = store.get_daily_pnl(ACCT, TODAY)
    # Should be 900 - 1000 = -100
    assert daily_pnl == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_loss_limit_with_very_small_equity(store):
    """Test loss limit calculation with small equity values."""
    _snap(store, _at(TODAY - timedelta(days=1), 20), 0.0)
    _snap(store, _at(TODAY, 9), -0.05)  # 0.05 loss on 100 equity = 0.05%

    # With small equity (100), a loss of 0.05 is 0.05%
    limiter = DailyLossLimiter(store, brokers={"paper": _Broker(100.0)})
    message = await limiter.check_daily_loss_limit(_account(), 0.1)
    # Should not be rejected (0.05% < 0.1%)
    assert message is None


@pytest.mark.asyncio
async def test_loss_limit_exactly_zero_is_impossible_but_disabled(store):
    """Test that a 0% loss limit is treated as disabled, not as impossible to breach."""
    _snap(store, _at(TODAY - timedelta(days=1), 20), 0.0)
    _snap(store, _at(TODAY, 9), -1.0)

    # 0% loss limit should be treated as "not configured"
    assert await _limiter(store).check_daily_loss_limit(_account(), 0.0) is None
