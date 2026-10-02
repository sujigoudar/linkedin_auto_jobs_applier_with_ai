"""WP-01: Unsized entries are rejected, never default to 1.0.

Finding B-02 from docs/audit/SOLUTION_GAP_ANALYSIS.md:
A signal with no quantity silently becomes 1.0 unit — fix by rejecting
an entry when both signal.quantity and account.fixed_quantity are None.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.mark.asyncio
async def test_plain_account_unsized_entry_rejected(tmp_path):
    """Plain account, Signal(quantity=None) BUY → status rejected, message contains
    "no quantity"; broker received nothing."""
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="plain1", broker="paper")
    engine = SignalCopierEngine(
        routing=RoutingConfig(
            rules=[RoutingRule(source="test", destinations=["plain1"])],
            accounts={"plain1": account},
        ),
        brokers={"paper": broker},
        store=store,
    )

    result = await engine.handle_signal(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=None, price=100.0))
    assert len(result) == 1
    assert result[0].status == OrderStatus.REJECTED
    assert "no quantity" in result[0].message.lower()
    assert broker.fills == []


@pytest.mark.asyncio
async def test_managed_account_unsized_entry_rejected(tmp_path):
    """Managed account, Signal(quantity=None) BUY → rejected, no lifecycle created."""
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="mgd1", broker="paper", managed_lifecycle=True)
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=RoutingConfig(
            rules=[RoutingRule(source="test", destinations=["mgd1"])],
            accounts={"mgd1": account},
        ),
        brokers={"paper": broker},
        store=store,
        lifecycle_manager=manager,
    )

    result = await engine.handle_signal(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=None, price=100.0))
    assert len(result) == 1
    assert result[0].status == OrderStatus.REJECTED
    assert "no quantity" in result[0].message.lower()
    # No lifecycle should be created
    lifecycle = manager.get_lifecycle("mgd1", "AAPL")
    assert lifecycle is None


@pytest.mark.asyncio
async def test_fixed_quantity_account_fills_with_fixed_amount(tmp_path):
    """Account with fixed_quantity=2 → fills 2 even when signal.quantity=None."""
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="fixed1", broker="paper", fixed_quantity=2.0)
    engine = SignalCopierEngine(
        routing=RoutingConfig(
            rules=[RoutingRule(source="test", destinations=["fixed1"])],
            accounts={"fixed1": account},
        ),
        brokers={"paper": broker},
        store=store,
    )

    result = await engine.handle_signal(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=None, price=100.0))
    assert len(result) == 1
    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 2.0
    assert broker.positions["fixed1"]["AAPL"] == 2.0


@pytest.mark.asyncio
async def test_close_without_quantity_on_plain_account_holding_5(tmp_path):
    """quantity=None CLOSE on a plain account holding 5 → still closes 5
    (CLOSE is not sized by size_for_account)."""
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="plain2", broker="paper")
    engine = SignalCopierEngine(
        routing=RoutingConfig(
            rules=[RoutingRule(source="test", destinations=["plain2"])],
            accounts={"plain2": account},
        ),
        brokers={"paper": broker},
        store=store,
    )

    # First, establish a position
    entry = await engine.handle_signal(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=5, price=100.0))
    assert entry[0].status == OrderStatus.FILLED
    assert broker.positions["plain2"]["AAPL"] == 5.0

    # Close with no quantity specified
    close = await engine.handle_signal(Signal(source="test", symbol="AAPL", side=Side.CLOSE, quantity=None, price=105.0))
    assert close[0].status == OrderStatus.FILLED
    assert close[0].filled_quantity == 5.0
    assert broker.positions["plain2"]["AAPL"] == 0.0
