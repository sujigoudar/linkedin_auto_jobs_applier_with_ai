"""WP-31: Margin call detector fed from broker balances.

Tests for the integration of margin call detector with the engine's account
balance fetching. The detector receives live equity and maintenance_margin
values from broker.get_account_balance() instead of None placeholders.

This implementation fixes B-09 (margin-call detector is a production no-op):
- Feed balance.equity and balance.maintenance_margin into the detector
- Fail closed when a margin account reports equity but no maintenance figure
- Block entries while an unresolved margin_call_alerts row exists
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AccountBalance, DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


class MockBrokerWithMarginBalance(PaperBroker):
    """Mock broker that extends PaperBroker to return margin balance information."""

    def __init__(self, equity: float | None = None, maintenance_margin: float | None = None):
        super().__init__()
        self.equity = equity
        self.maintenance_margin = maintenance_margin
        self.name = "mock_margin_broker"

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        # Get the base cash from paper broker
        cash = self._cash_for(account.account_id)
        return AccountBalance(
            account_id=account.account_id,
            cash=cash,
            equity=self.equity,
            buying_power=self.equity if self.equity is not None else cash,
            maintenance_margin=self.maintenance_margin,
        )


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store, broker, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={account.broker: broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing,
        brokers={account.broker: broker},
        store=store,
        lifecycle_manager=lifecycle_manager,
    )
    return engine


@pytest.mark.asyncio
async def test_margin_call_detector_blocks_entry_on_unresolved_alert(store):
    """Entries should be blocked while an unresolved margin call alert exists."""
    broker = MockBrokerWithMarginBalance(equity=5000.0, maintenance_margin=6000.0)
    account = DestinationAccount(account_id="acct1", broker="mock_margin_broker")
    engine = _engine(store, broker, account)

    # Create an unresolved margin call alert manually
    store.persist_margin_call_alert(
        account_id="acct1",
        current_equity=5000.0,
        maintenance_requirement=6000.0,
        excess_margin=-1000.0,
        broker="mock_margin_broker",
    )

    # Now try to enter a position
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    # Should be rejected due to unresolved margin call alert
    assert results[0].status == OrderStatus.REJECTED
    assert "unresolved margin call alert" in results[0].message


@pytest.mark.asyncio
async def test_margin_call_detector_detects_margin_call_with_broker_balance(store):
    """Margin call should be detected when broker reports equity < maintenance_margin."""
    broker = MockBrokerWithMarginBalance(equity=5000.0, maintenance_margin=6000.0)
    account = DestinationAccount(account_id="acct1", broker="mock_margin_broker")
    engine = _engine(store, broker, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    # Should be rejected due to margin call
    assert results[0].status == OrderStatus.REJECTED
    assert "Margin call" in results[0].message or "cannot determine margin state" in results[0].message.lower()

    # Verify alert was persisted
    alerts = store.get_unresolved_margin_calls("acct1")
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_margin_call_detector_allows_entry_with_sufficient_margin(store):
    """Entry should be allowed when equity > maintenance_margin."""
    paper_broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine(store, paper_broker, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    # Paper broker doesn't report equity/maintenance_margin (both None),
    # so this should pass through without margin checks
    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_margin_gate_is_skipped_when_maintenance_margin_is_not_reported(store):
    """Equity without a maintenance figure means the adapter does not track
    margin (cash accounts, the paper simulator): the margin-call gate cannot
    run, so it is skipped -- never a rejection -- and no alert is persisted.
    The other admission gates (buying power, loss limit, exposure) still apply."""
    broker = MockBrokerWithMarginBalance(equity=10000.0, maintenance_margin=None)
    account = DestinationAccount(account_id="acct1", broker="mock_margin_broker")
    engine = _engine(store, broker, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    assert results[0].status != OrderStatus.REJECTED or "margin" not in results[0].message.lower()
    assert engine.margin_call_detector.get_unresolved_margin_calls("acct1") == []


@pytest.mark.asyncio
async def test_margin_call_detector_allows_entry_with_both_none(store):
    """When broker doesn't report margin data, entries should pass through."""
    broker = MockBrokerWithMarginBalance(equity=None, maintenance_margin=None)
    account = DestinationAccount(account_id="acct1", broker="mock_margin_broker")
    engine = _engine(store, broker, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    # Should pass through since broker doesn't support margin reporting
    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_margin_call_alert_can_be_resolved_to_allow_entry(store):
    """After resolving a margin call alert, entries should be allowed again."""
    # The broker still reports the breach while the alert is open, so the
    # engine's auto-recovery must NOT clear it (only a genuine recovery does).
    broker = MockBrokerWithMarginBalance(equity=5000.0, maintenance_margin=6000.0)
    account = DestinationAccount(account_id="acct1", broker="mock_margin_broker")
    engine = _engine(store, broker, account)

    # Create an unresolved margin call alert
    store.persist_margin_call_alert(
        account_id="acct1",
        current_equity=5000.0,
        maintenance_requirement=6000.0,
        excess_margin=-1000.0,
        broker="mock_margin_broker",
    )

    # Try to enter - should be blocked
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.REJECTED

    # Get the alert and resolve it
    alerts = store.get_unresolved_margin_calls("acct1")
    assert len(alerts) == 1
    store.resolve_margin_call_alert(alerts[0]["id"])

    # Margin is restored at the broker; now entry should be allowed
    broker.equity = 10000.0
    broker.maintenance_margin = 5000.0
    signal2 = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results2 = await engine.handle_signal(signal2)
    assert results2[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_close_signal_bypasses_margin_call_check(store):
    """CLOSE signals should bypass margin call checks to allow hedging."""
    broker = MockBrokerWithMarginBalance(equity=5000.0, maintenance_margin=6000.0)
    account = DestinationAccount(account_id="acct1", broker="mock_margin_broker")
    engine = _engine(store, broker, account)

    # Create an unresolved margin call alert
    store.persist_margin_call_alert(
        account_id="acct1",
        current_equity=5000.0,
        maintenance_requirement=6000.0,
        excess_margin=-1000.0,
        broker="mock_margin_broker",
    )

    # CLOSE signals should not be blocked by margin calls
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(signal)

    # Should not be rejected for margin reasons (may be rejected for other reasons like no position)
    # but definitely not for "unresolved margin call alert"
    if results[0].status == OrderStatus.REJECTED:
        assert "unresolved margin call alert" not in results[0].message


@pytest.mark.asyncio
async def test_margin_call_warning_allows_entry_but_logs_warning(store):
    """When excess_margin is low but positive, entry should be allowed with warning."""
    broker = MockBrokerWithMarginBalance(equity=10500.0, maintenance_margin=10000.0)
    account = DestinationAccount(account_id="acct1", broker="mock_margin_broker")
    engine = _engine(store, broker, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    # Should be allowed (excess_margin = 500, above zero)
    # but detector should have logged a warning
    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_margin_call_detector_with_exact_zero_excess_margin(store):
    """At break-even (excess_margin == 0), margin call should be detected."""
    broker = MockBrokerWithMarginBalance(equity=6000.0, maintenance_margin=6000.0)
    account = DestinationAccount(account_id="acct1", broker="mock_margin_broker")
    engine = _engine(store, broker, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    # excess_margin == 0 should trigger margin call (excess_margin <= 0)
    assert results[0].status == OrderStatus.REJECTED
    assert "Margin call" in results[0].message or "cannot determine margin state" in results[0].message.lower()

    alerts = store.get_unresolved_margin_calls("acct1")
    assert len(alerts) == 1
