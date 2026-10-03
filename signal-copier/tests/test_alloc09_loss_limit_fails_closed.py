"""Test that loss limit circuit breaker fails closed when P&L cannot be computed.

WP-30: Loss limit end-to-end wiring. When an account has a configured
daily_loss_limit_percent but daily P&L cannot be obtained (a store with no
daily-P&L source), entries are rejected rather than silently bypassed. The real
store now provides get_daily_pnl (see tests/test_daily_loss_limit_real_pnl.py);
the engine-level tests below cover both the fail-closed path and a real breach.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, Signal, Side, OrderStatus
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def tmp_store(tmp_path: Path) -> SignalStore:
    """Create a fresh SignalStore for this test."""
    return SignalStore(str(tmp_path / "test.db"))


@pytest.fixture
def paper_broker() -> PaperBroker:
    """Create a paper broker for testing."""
    return PaperBroker()


async def test_daily_loss_limit_configured_fails_closed(
    tmp_store: SignalStore, paper_broker: PaperBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When daily_loss_limit_percent is configured but the store has no daily-P&L
    source, entries are rejected (fail-closed) rather than silently bypassed.

    Scenario: an account has daily_loss_limit_percent=5 and the store exposes no
    get_daily_pnl. The engine must REJECT the entry with a message saying the P&L
    source is not available.
    """
    monkeypatch.setattr(SignalStore, "get_daily_pnl", None)
    # Set up account with a configured loss limit
    account = DestinationAccount(
        account_id="paper-loss-limited",
        broker="paper",
        daily_loss_limit_percent=5.0,  # 5% loss limit
    )
    # Persist the account so it can be loaded
    tmp_store.upsert_config_account(
        account_id=account.account_id,
        broker=account.broker,
        multiplier=account.multiplier,
        daily_loss_limit_percent=account.daily_loss_limit_percent,
    )

    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["paper-loss-limited"])],
        accounts={"paper-loss-limited": account},
    )

    # Create engine with the account
    brokers = {"paper": paper_broker}
    engine = SignalCopierEngine(store=tmp_store, routing=routing, brokers=brokers)

    # Try to enter a position; should be rejected due to missing P&L source
    signal = Signal(
        id="test-entry",
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=150.0,
        stop_loss=145.0,
    )

    results = await engine.handle_signal(signal)

    # Should have one result (one destination account)
    assert len(results) == 1
    result = results[0]

    # Entry should be REJECTED
    assert result.status == OrderStatus.REJECTED
    assert "Daily loss limit check failed" in result.message
    assert "no daily P&L source is implemented" in result.message


async def test_daily_loss_limit_none_allows_entries(
    tmp_store: SignalStore, paper_broker: PaperBroker
) -> None:
    """When daily_loss_limit_percent is None, entries are not rejected by the loss limiter.

    This is the normal case: accounts without a configured loss limit proceed
    normally (other gates may still reject, but not the loss limiter).
    """
    account = DestinationAccount(
        account_id="paper-unlimited",
        broker="paper",
        daily_loss_limit_percent=None,  # No loss limit
    )
    tmp_store.upsert_config_account(
        account_id=account.account_id,
        broker=account.broker,
    )

    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["paper-unlimited"])],
        accounts={"paper-unlimited": account},
    )

    brokers = {"paper": paper_broker}
    engine = SignalCopierEngine(store=tmp_store, routing=routing, brokers=brokers)

    signal = Signal(
        id="test-entry-no-limit",
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=150.0,
        stop_loss=145.0,
    )

    results = await engine.handle_signal(signal)

    # Should have one result
    assert len(results) == 1
    result = results[0]

    # Entry should NOT be rejected by loss limiter (may be rejected by other gates)
    # If rejected, should not be due to daily loss limit
    if result.status == OrderStatus.REJECTED:
        assert "Daily loss limit check failed" not in result.message


async def test_close_signals_bypass_loss_limit_check(
    tmp_store: SignalStore, paper_broker: PaperBroker
) -> None:
    """CLOSE signals bypass the loss limit check to allow hedging/unwinding.

    When an account has breached its loss limit (or would, if checked),
    a CLOSE signal should still be allowed to reduce/exit positions.
    This is the safety valve for risk control: allow operators to unwind
    after a limit is hit.
    """
    account = DestinationAccount(
        account_id="paper-close-allowed",
        broker="paper",
        daily_loss_limit_percent=1.0,  # Very tight limit to force a breach
    )
    tmp_store.upsert_config_account(
        account_id=account.account_id,
        broker=account.broker,
        daily_loss_limit_percent=account.daily_loss_limit_percent,
    )

    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["paper-close-allowed"])],
        accounts={"paper-close-allowed": account},
    )

    brokers = {"paper": paper_broker}
    engine = SignalCopierEngine(store=tmp_store, routing=routing, brokers=brokers)

    # Try a CLOSE signal (should NOT be checked against loss limit)
    close_signal = Signal(
        id="test-close",
        source="test",
        symbol="AAPL",
        side=Side.CLOSE,
        quantity=100.0,
    )

    results = await engine.handle_signal(close_signal)

    # Should have one result
    assert len(results) == 1
    result = results[0]

    # CLOSE should NOT be rejected by loss limit check
    assert "Daily loss limit check failed" not in (result.message or "")


def _limited_engine(store: SignalStore, broker: PaperBroker, account_id: str):
    account = DestinationAccount(account_id=account_id, broker="paper", daily_loss_limit_percent=5.0)
    store.upsert_config_account(
        account_id=account.account_id,
        broker=account.broker,
        multiplier=account.multiplier,
        daily_loss_limit_percent=account.daily_loss_limit_percent,
    )
    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=[account_id])],
        accounts={account_id: account},
    )
    return SignalCopierEngine(store=store, routing=routing, brokers={"paper": broker})


def _entry(signal_id: str) -> Signal:
    return Signal(id=signal_id, source="test", symbol="AAPL", side=Side.BUY, quantity=10.0, price=150.0, stop_loss=145.0)


def _snapshots(store: SignalStore, account_id: str, today_cumulative: float) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    for when, value in ((start - timedelta(hours=1), 0.0), (start + timedelta(seconds=1), today_cumulative)):
        store.record_equity_snapshot(
            account_id, captured_at=when, realized_pnl=value, unrealized_pnl=0.0, cumulative_pnl=value
        )


async def test_real_loss_over_limit_rejects_entries(tmp_store: SignalStore, paper_broker: PaperBroker) -> None:
    """With real snapshots showing a loss beyond the limit, the engine rejects the entry."""
    engine = _limited_engine(tmp_store, paper_broker, "paper-breached")
    equity = float((await paper_broker.get_account_balance(DestinationAccount(account_id="paper-breached", broker="paper"))).equity)
    _snapshots(tmp_store, "paper-breached", -(equity * 0.06))  # 6% loss vs 5% limit

    results = await engine.handle_signal(_entry("breach-entry"))

    assert len(results) == 1 and results[0].status == OrderStatus.REJECTED
    assert "Daily loss limit breached" in results[0].message


async def test_loss_under_limit_does_not_block_entries(tmp_store: SignalStore, paper_broker: PaperBroker) -> None:
    """A loss inside the limit is not rejected by the loss limiter."""
    engine = _limited_engine(tmp_store, paper_broker, "paper-within")
    equity = float((await paper_broker.get_account_balance(DestinationAccount(account_id="paper-within", broker="paper"))).equity)
    _snapshots(tmp_store, "paper-within", -(equity * 0.02))  # 2% loss vs 5% limit

    results = await engine.handle_signal(_entry("within-entry"))

    assert len(results) == 1
    assert "Daily loss limit" not in (results[0].message or "")
