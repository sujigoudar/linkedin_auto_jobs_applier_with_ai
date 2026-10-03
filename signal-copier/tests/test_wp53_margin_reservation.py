"""WP-53: Margin reservation in admission gates.

Test that the engine properly calculates and reserves initial margin
requirements for margin accounts, distinguishing between:
- Cash-account trades (margin requirement = 0)
- Margin-account trades (margin requirement based on notional / leverage)

The user directive: "Budget can't be unlimited. There is available capital
and available capital plus margin."
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
        equity: float = 10000.0,
        maintenance_margin: float | None = 2000.0,
        starting_cash: float = 10000.0,
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
async def test_margin_account_reserves_initial_margin(store):
    """For a margin account with max_gross_leverage, initial margin is reserved."""
    # Account with 2:1 leverage (50% initial margin requirement)
    broker = MockMarginBroker(equity=10000.0, maintenance_margin=2000.0, starting_cash=5000.0)
    account = DestinationAccount(
        account_id="margin_acct",
        broker="mock_margin_broker",
        max_gross_leverage=2.0,  # 2:1 leverage = 50% initial margin
    )
    engine = _engine(store, broker, account)

    # Try to buy with a price in the signal
    # $5000 notional (10 shares @ $500)
    # Initial margin requirement: $5000 / 2.0 = $2500
    # Available: $5000 cash, so this should FILL
    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=500.0,  # $500/share
    )
    results = await engine.handle_signal(signal)

    # Should succeed - we have enough cash and margin capacity
    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_cash_account_no_margin_reservation(store):
    """For a cash account (no maintenance_margin), no margin is reserved."""
    # Cash account (no margin)
    broker = MockMarginBroker(
        equity=10000.0, maintenance_margin=None, starting_cash=10000.0
    )
    account = DestinationAccount(
        account_id="cash_acct",
        broker="mock_margin_broker",
    )
    engine = _engine(store, broker, account)

    # Try to buy
    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=500.0,
    )
    results = await engine.handle_signal(signal)

    # Should succeed - cash accounts have no margin requirement
    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_margin_insufficiency_blocks_entry(store):
    """Entry rejected when initial margin requirement exceeds available margin."""
    # Account with only $2000 equity - insufficient for margin trade
    broker = MockMarginBroker(
        equity=2000.0, maintenance_margin=1000.0, starting_cash=1000.0
    )
    account = DestinationAccount(
        account_id="low_margin_acct",
        broker="mock_margin_broker",
        max_gross_leverage=2.0,
    )
    engine = _engine(store, broker, account)

    # Try to buy $5000 notional
    # Initial margin requirement: $5000 / 2.0 = $2500
    # Available capital: $1000
    # Available margin: $1000 (equity - maintenance)
    # Total: $2000 < $2500 required → REJECT
    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=500.0,
    )
    results = await engine.handle_signal(signal)

    # Should be rejected due to insufficient margin
    assert results[0].status == OrderStatus.REJECTED
    assert "capital" in results[0].message.lower() or "margin" in results[0].message.lower()


@pytest.mark.asyncio
async def test_leverage_limit_constrains_position_size(store):
    """Position size is constrained by leverage limit."""
    # Account with 1.5:1 leverage max (66.7% initial margin)
    broker = MockMarginBroker(
        equity=10000.0, maintenance_margin=2000.0, starting_cash=5000.0
    )
    account = DestinationAccount(
        account_id="limited_leverage",
        broker="mock_margin_broker",
        max_gross_leverage=1.5,  # 1.5:1 leverage
    )
    engine = _engine(store, broker, account)

    # Try to buy $7500 notional (15 shares @ $500)
    # Initial margin requirement: $7500 / 1.5 = $5000
    # Available capital: $5000
    # This should just fit
    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=15.0,
        price=500.0,
    )
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED

    # Now try to buy more - should be rejected
    signal2 = Signal(
        source="tradingview",
        symbol="MSFT",
        side=Side.BUY,
        quantity=2.0,
        price=500.0,
    )
    results2 = await engine.handle_signal(signal2)

    # Should be rejected - margin already used
    assert results2[0].status == OrderStatus.REJECTED
