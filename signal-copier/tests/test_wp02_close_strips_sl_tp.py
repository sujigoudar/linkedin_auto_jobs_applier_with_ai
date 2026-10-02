"""WP-02 (C-02): A plain-account CLOSE carrying SL/TP is sent as a bracket
whose child legs open a new position.

Fix: Strip `stop_loss`/`take_profit`/`targets` in `_resolve_close`.

A CLOSE signal that carries stop_loss/take_profit fields should have those
stripped when resolved, so that adapters cannot build reverse-side bracket
legs that would open unintended positions after the close executes.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, ProfitTarget, Side, Signal
from app.routing import RoutingConfig, RoutingRule


class _InspectingBroker(PaperBroker):
    """A PaperBroker that records what signal was actually passed to place_order."""

    def __init__(self):
        super().__init__()
        self.submitted_signals = []

    async def place_order(self, signal, account, quantity, symbol):
        self.submitted_signals.append(signal)
        return await super().place_order(signal, account, quantity, symbol)


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def broker():
    return _InspectingBroker()


@pytest.fixture
def engine(store, broker):
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts={"acct1": account},
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)


@pytest.mark.asyncio
async def test_plain_close_strips_stop_loss(store, broker, engine):
    """A plain-account CLOSE carrying stop_loss should have it stripped."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    # Close with stop_loss (this should be stripped)
    close = Signal(
        source="tradingview", symbol="AAPL", side=Side.CLOSE, stop_loss=90.0
    )
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    # The signal passed to place_order should NOT have stop_loss
    assert len(broker.submitted_signals) == 2  # entry + close
    close_signal = broker.submitted_signals[1]
    assert close_signal.stop_loss is None
    assert close_signal.side == Side.SELL  # resolved from CLOSE


@pytest.mark.asyncio
async def test_plain_close_strips_take_profit(store, broker, engine):
    """A plain-account CLOSE carrying take_profit should have it stripped."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    # Close with take_profit (this should be stripped)
    close = Signal(
        source="tradingview", symbol="AAPL", side=Side.CLOSE, take_profit=110.0
    )
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    # The signal passed to place_order should NOT have take_profit
    assert len(broker.submitted_signals) == 2  # entry + close
    close_signal = broker.submitted_signals[1]
    assert close_signal.take_profit is None
    assert close_signal.side == Side.SELL  # resolved from CLOSE


@pytest.mark.asyncio
async def test_plain_close_strips_targets(store, broker, engine):
    """A plain-account CLOSE carrying targets should have them stripped."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    # Close with targets (this should be stripped)
    close = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.CLOSE,
        targets=[
            ProfitTarget(price=110.0, fraction=0.5),
            ProfitTarget(price=120.0, fraction=0.5),
        ],
    )
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    # The signal passed to place_order should NOT have targets
    assert len(broker.submitted_signals) == 2  # entry + close
    close_signal = broker.submitted_signals[1]
    assert close_signal.targets == []
    assert close_signal.side == Side.SELL  # resolved from CLOSE


@pytest.mark.asyncio
async def test_plain_close_strips_all_risk_fields(store, broker, engine):
    """A plain-account CLOSE carrying all risk fields should have them all stripped."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    # Close with all SL/TP/targets (all should be stripped)
    close = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.CLOSE,
        stop_loss=90.0,
        take_profit=110.0,
        targets=[
            ProfitTarget(price=110.0, fraction=0.5),
            ProfitTarget(price=120.0, fraction=0.5),
        ],
    )
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    # The signal passed to place_order should NOT have any risk fields
    assert len(broker.submitted_signals) == 2  # entry + close
    close_signal = broker.submitted_signals[1]
    assert close_signal.stop_loss is None
    assert close_signal.take_profit is None
    assert close_signal.targets == []
    assert close_signal.side == Side.SELL  # resolved from CLOSE


@pytest.mark.asyncio
async def test_manual_close_strips_sl_tp(store, broker, engine):
    """A manual close (dashboard "Exit now") should also strip SL/TP."""
    account = engine.routing.accounts["acct1"]
    # Entry: SELL 5 (short)
    entry = Signal(source="tradingview", symbol="EURUSD", side=Side.SELL, quantity=5.0)
    await engine.handle_signal(entry)

    # Manual close via dashboard
    result = await engine.close_position(account, "EURUSD", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.FILLED
    # The signal passed to place_order should NOT have SL/TP (it shouldn't have any by default)
    assert len(broker.submitted_signals) == 2  # entry + close
    close_signal = broker.submitted_signals[1]
    assert close_signal.stop_loss is None
    assert close_signal.take_profit is None
    assert close_signal.targets == []
    assert close_signal.side == Side.BUY  # opposite of entry SELL
