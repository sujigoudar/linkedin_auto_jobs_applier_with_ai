"""The daily-loss limit must work against a real store, not a mocked P&L source.

Daily P&L is the change in persisted cumulative P&L (the equity-snapshot series)
over the UTC day. Only losses count toward the limit.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.daily_loss_limiter import DailyLossLimiter
from app.db import SignalStore
from app.models import AccountBalance, DestinationAccount

ACCT = "acct1"
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
    return SignalStore(tmp_path / "dl.db")


def _limiter(store, equity: float | None = 1000.0):
    return DailyLossLimiter(store, brokers={"paper": _Broker(equity)})


def _account():
    return DestinationAccount(account_id=ACCT, broker="paper")


def test_no_snapshots_means_no_data(store):
    assert store.get_daily_pnl(ACCT, TODAY) is None


def test_daily_pnl_is_difference_from_last_snapshot_before_the_day(store):
    yesterday = TODAY - timedelta(days=1)
    _snap(store, _at(yesterday, 20), 100.0)
    _snap(store, _at(TODAY, 1), 90.0)
    _snap(store, _at(TODAY, 9), 40.0)
    assert store.get_daily_pnl(ACCT, TODAY) == pytest.approx(-60.0)


def test_baseline_is_first_snapshot_of_the_day_when_series_starts_today(store):
    _snap(store, _at(TODAY, 1), 500.0)
    _snap(store, _at(TODAY, 9), 450.0)
    assert store.get_daily_pnl(ACCT, TODAY) == pytest.approx(-50.0)


@pytest.mark.asyncio
async def test_loss_at_or_over_the_limit_is_rejected(store):
    _snap(store, _at(TODAY - timedelta(days=1), 20), 0.0)
    _snap(store, _at(TODAY, 9), -60.0)  # 6% of 1000 equity
    message = await _limiter(store).check_daily_loss_limit(_account(), 5.0)
    assert message is not None and "Daily loss limit breached" in message


@pytest.mark.asyncio
async def test_loss_under_the_limit_is_allowed(store):
    _snap(store, _at(TODAY - timedelta(days=1), 20), 0.0)
    _snap(store, _at(TODAY, 9), -40.0)  # 4% of 1000 equity
    assert await _limiter(store).check_daily_loss_limit(_account(), 5.0) is None


@pytest.mark.asyncio
async def test_a_large_profit_never_trips_the_loss_limit(store):
    _snap(store, _at(TODAY - timedelta(days=1), 20), 0.0)
    _snap(store, _at(TODAY, 9), 400.0)  # +40% day
    assert await _limiter(store).check_daily_loss_limit(_account(), 5.0) is None


@pytest.mark.asyncio
async def test_no_data_today_is_allowed_but_unreadable_equity_fails_closed(store):
    assert await _limiter(store).check_daily_loss_limit(_account(), 5.0) is None
    _snap(store, _at(TODAY - timedelta(days=1), 20), 0.0)
    _snap(store, _at(TODAY, 9), -10.0)
    message = await _limiter(store, equity=None).check_daily_loss_limit(_account(), 5.0)
    assert message is not None and "cannot determine account equity" in message


@pytest.mark.asyncio
async def test_limit_unset_is_a_no_op(store):
    assert await _limiter(store).check_daily_loss_limit(_account(), None) is None
