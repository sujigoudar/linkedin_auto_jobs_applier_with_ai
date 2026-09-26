"""Reproduces the exact defect flagged as release-blocking: a broker with no
real way to honor stop_loss/take_profit must never silently admit an entry
that requests them -- on the plain path (no managed_lifecycle) or the
managed path alike."""
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.brokers.ninjatrader import NinjaTraderBroker
from app.brokers.paper import PaperBroker
from app.brokers.rithmic import RithmicBroker
from app.brokers.signalstack import SignalStackBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_paper_broker_has_no_native_bracket_but_real_protective_stop():
    broker = PaperBroker()
    assert broker.supports_native_bracket is False
    assert broker.has_protective_stop_capability is True
    assert broker.can_protect_a_managed_position() is True


def test_signalstack_broker_has_no_protection_capability_at_all(monkeypatch):
    monkeypatch.setenv("SIGNALSTACK_ACCT1_WEBHOOK_URL", "https://example.com/hook")
    broker = SignalStackBroker()
    assert broker.supports_native_bracket is False
    assert broker.has_protective_stop_capability is False
    assert broker.has_order_status_capability is False
    assert broker.can_protect_a_managed_position() is False


def test_ninjatrader_has_no_protection_capability():
    assert NinjaTraderBroker().can_protect_a_managed_position() is False


def test_rithmic_has_no_protection_capability():
    pytest.importorskip("async_rithmic")
    broker = RithmicBroker(user="u", password="p", system_name="s", gateway_url="g")
    assert broker.can_protect_a_managed_position() is False


def test_brokers_with_native_bracket_can_protect():
    assert AlpacaBroker().can_protect_a_managed_position() is True


@pytest.mark.asyncio
async def test_plain_path_refuses_entry_with_stop_loss_on_a_broker_that_would_drop_it(store, monkeypatch):
    monkeypatch.setenv("SIGNALSTACK_ACCT1_WEBHOOK_URL", "https://example.com/hook")
    broker = SignalStackBroker()
    account = DestinationAccount(account_id="acct1", broker="signalstack")  # managed_lifecycle defaults False
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"signalstack": broker}, store=store)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "silently open unprotected" in results[0].message
    await broker.close()


@pytest.mark.asyncio
async def test_plain_path_allows_entry_with_no_protection_requested(store, monkeypatch):
    monkeypatch.setenv("SIGNALSTACK_ACCT1_WEBHOOK_URL", "https://example.com/hook")
    broker = SignalStackBroker()

    async def fake_post(url, json):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

        return _Resp()

    monkeypatch.setattr(broker._client, "post", fake_post)
    account = DestinationAccount(account_id="acct1", broker="signalstack")
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"signalstack": broker}, store=store)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)  # no stop_loss requested
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.PENDING  # unchanged pre-existing behavior
    await broker.close()


@pytest.mark.asyncio
async def test_plain_path_allows_stop_loss_on_a_broker_that_actually_embeds_it(store):
    broker = PaperBroker()
    # PaperBroker doesn't override place_order to embed SL/TP either -- it's a
    # mock broker without supports_native_bracket, so this documents that even
    # paper requires managed_lifecycle for real protection, matching the same
    # rule as any other non-bracket broker.
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "silently open unprotected" in results[0].message


@pytest.mark.asyncio
async def test_managed_entry_refused_on_a_broker_with_no_protection_capability(store, monkeypatch):
    monkeypatch.setenv("SIGNALSTACK_ACCT1_WEBHOOK_URL", "https://example.com/hook")
    broker = SignalStackBroker()
    account = DestinationAccount(account_id="acct1", broker="signalstack", managed_lifecycle=True)
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"signalstack": broker})
    engine = SignalCopierEngine(
        routing=routing, brokers={"signalstack": broker}, store=store, lifecycle_manager=lifecycle_manager
    )

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "no verified way to keep this position protected" in results[0].message
    assert lifecycle_manager.get_lifecycle("acct1", "AAPL") is None  # never registered
    await broker.close()
