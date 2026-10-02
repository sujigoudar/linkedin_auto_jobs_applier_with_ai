"""Test that loss limit circuit breaker fails closed when P&L cannot be computed.

WP-30: Loss limit end-to-end wiring. This test verifies that when an account
has a configured daily_loss_limit_percent but daily P&L data is unavailable
(as is the case in the current build), entries are rejected rather than
silently bypassed. This is the fail-closed safety posture for unimplemented
risk controls.
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
    tmp_store: SignalStore, paper_broker: PaperBroker
) -> None:
    """When daily_loss_limit_percent is configured, entries are rejected (fail-closed).

    This verifies that configured loss limits do not silently bypass entries
    when daily P&L computation is not yet implemented. The limiter explicitly
    rejects entries with a message indicating the P&L source is missing,
    preventing unguarded trading.

    Scenario: An account is configured with daily_loss_limit_percent=5 (5% loss
    limit). Because daily P&L computation is not yet implemented in this build,
    the engine should REJECT an entry attempt with a clear message that the
    P&L source is not available.
    """
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
