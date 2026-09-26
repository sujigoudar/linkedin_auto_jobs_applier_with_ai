"""E06: a managed-lifecycle manual close (dashboard "Exit now"/"Flatten")
saved its order row with side=Side.CLOSE, contradicting
SignalStore.save_order_result's own documented contract ("for a resolved
close, side is the opposing buy/sell, not Side.CLOSE") -- the plain-
account close path already honored it. A P&L report reconstructing
realized gains from the `orders` table needs the actual trade direction;
"close" tells it nothing.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_managed_close_records_resolved_buy_sell_not_close(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    await engine.handle_signal(entry)

    await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    rows = store.list_recent_orders(account_id="acct1")
    most_recent = rows[0]
    assert most_recent["side"] == Side.SELL.value
    assert most_recent["side"] != Side.CLOSE.value


@pytest.mark.asyncio
async def test_plain_account_close_still_records_resolved_side(store):
    """Contrast case: the plain-account path already got this right --
    must keep working."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    rows = store.list_recent_orders(account_id="acct1")
    most_recent = rows[0]
    assert most_recent["side"] == Side.SELL.value
