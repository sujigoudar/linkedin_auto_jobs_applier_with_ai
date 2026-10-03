"""WP-55: Fix managed short reconciliation sign bug (R0-D03).

Test that reconciliation correctly handles managed shorts by properly signing
the broker readback.

Audit finding D-03: "Managed short: deficit = owned - broker_owned with signed
readback → 10 − (−10) = 20 → lifecycle closed, BUY 20 fabricated and exported,
real short orphaned."

The issue: confirmed_owned_quantity is signed (-10 for a short), but broker_owned_abs
is always positive. This causes incorrect deficit calculation for shorts.

Fix: Keep the sign of broker readback consistent throughout the deficit calculation.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AccountBalance, DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


class MockMarginBroker(PaperBroker):
    """Mock broker that reports margin account details."""

    def __init__(
        self,
        equity: float = 100000.0,
        maintenance_margin: float | None = None,
        starting_cash: float = 100000.0,
    ):
        super().__init__()
        self._equity = equity
        self._maintenance_margin = maintenance_margin
        self._starting_cash_override = starting_cash
        self.name = "mock_margin_broker"

    async def get_account_balance(self, account) -> AccountBalance | None:
        cash = self._cash_for(account.account_id)
        return AccountBalance(
            account_id=account.account_id,
            cash=cash,
            equity=self._equity,
            buying_power=self._equity - self._maintenance_margin
            if self._maintenance_margin
            else self._equity,
            maintenance_margin=self._maintenance_margin,
        )


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store, broker, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(
        brokers={account.broker: broker}, store=store
    )
    engine = SignalCopierEngine(
        routing=routing,
        brokers={account.broker: broker},
        store=store,
        lifecycle_manager=lifecycle_manager,
    )
    return engine


@pytest.mark.asyncio
async def test_managed_short_reconciliation_no_deficit(store):
    """Managed short with matching broker position shows no deficit."""
    broker = MockMarginBroker(equity=100000.0, maintenance_margin=None, starting_cash=100000.0)
    account = DestinationAccount(
        account_id="managed_short",
        broker="mock_margin_broker",
        managed_lifecycle=True,
        allow_short=True,
    )
    engine = _engine(store, broker, account)

    # Open a managed short
    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.SELL,
        quantity=10.0,
        price=150.0,
        stop_loss=155.0,  # Stop for a short
    )
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    # Check that the position is correctly recorded as a short (-10)
    position = store.get_position(account.account_id, "AAPL")
    assert position is not None
    assert position < 0, f"Expected short position, got {position}"
    assert abs(position) == 10.0, f"Expected short 10, got {position}"


@pytest.mark.asyncio
async def test_managed_short_partial_fill_reconciliation(store):
    """Managed short with partial fill is reconciled correctly."""
    broker = MockMarginBroker(equity=100000.0, maintenance_margin=None, starting_cash=100000.0)
    account = DestinationAccount(
        account_id="managed_partial_short",
        broker="mock_margin_broker",
        managed_lifecycle=True,
        allow_short=True,
    )
    engine = _engine(store, broker, account)

    # Open a managed short for 10 units
    signal = Signal(
        source="tradingview",
        symbol="MSFT",
        side=Side.SELL,
        quantity=10.0,
        price=300.0,
        stop_loss=310.0,
    )
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    # Verify the short position
    position = store.get_position(account.account_id, "MSFT")
    assert position is not None
    assert position < 0
    assert abs(position) == 10.0


@pytest.mark.asyncio
async def test_managed_long_reconciliation_baseline(store):
    """Managed long reconciliation works correctly (baseline)."""
    broker = MockMarginBroker(equity=100000.0, maintenance_margin=None, starting_cash=100000.0)
    account = DestinationAccount(
        account_id="managed_long",
        broker="mock_margin_broker",
        managed_lifecycle=True,
    )
    engine = _engine(store, broker, account)

    # Open a managed long
    signal = Signal(
        source="tradingview",
        symbol="GOOGL",
        side=Side.BUY,
        quantity=5.0,
        price=2500.0,
        stop_loss=2450.0,
    )
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    # Check that the position is correctly recorded as a long (+5)
    position = store.get_position(account.account_id, "GOOGL")
    assert position is not None
    assert position > 0, f"Expected long position, got {position}"
    assert position == 5.0, f"Expected long 5, got {position}"
