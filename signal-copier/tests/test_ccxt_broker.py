import pytest

pytest.importorskip("ccxt")

from app.brokers.ccxt_broker import CCXTBroker, build_ccxt_brokers
from app.models import DestinationAccount, OrderStatus, Signal, Side


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    return CCXTBroker()


class _FakeExchange:
    def __init__(self, has: dict | None = None):
        self.create_order_calls = []
        self.closed = False
        self.has = has or {}

    async def create_order(self, **kwargs):
        self.create_order_calls.append(kwargs)
        return {"id": "order-1", "filled": kwargs["amount"], "average": 65000.0}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_stop_loss_and_take_profit_passed_as_unified_params_when_exchange_declares_support(broker):
    """ADP-02: `stopLossPrice`/`takeProfitPrice` are ccxt's UNIFIED param
    names, accepted syntactically by every exchange class whether or not
    the venue actually honors them as a genuine attached bracket -- this
    exchange declares real support via `exchange.has`, so the order goes
    through with the (also unified, but the actual attached-bracket-named)
    stopLoss/takeProfit params."""
    fake = _FakeExchange(has={"createOrderWithTakeProfitAndStopLoss": True})
    broker._exchanges["acct1"] = fake
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    await broker.place_order(
        Signal(source="test", symbol="BTCUSDT", side=Side.BUY, stop_loss=63000.0, take_profit=70000.0),
        account,
        quantity=1.0,
        symbol="BTC/USDT",
    )

    assert fake.create_order_calls[0]["params"] == {"stopLoss": 63000.0, "takeProfit": 70000.0}


@pytest.mark.asyncio
async def test_stop_loss_and_take_profit_refused_when_exchange_does_not_declare_support(broker):
    """The audit's own reproduced gap: an exchange with no verified
    attached-bracket capability must not receive unqualified flat trigger
    parameters -- refuse rather than guess."""
    fake = _FakeExchange()  # no .has declaration at all
    broker._exchanges["acct1"] = fake
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.place_order(
        Signal(source="test", symbol="BTCUSDT", side=Side.BUY, stop_loss=63000.0, take_profit=70000.0),
        account,
        quantity=1.0,
        symbol="BTC/USDT",
    )

    assert result.status == OrderStatus.REJECTED
    assert fake.create_order_calls == []


@pytest.mark.asyncio
async def test_no_sl_tp_sends_empty_params(broker):
    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    await broker.place_order(
        Signal(source="test", symbol="BTCUSDT", side=Side.BUY), account, quantity=1.0, symbol="BTC/USDT"
    )

    assert fake.create_order_calls[0]["params"] == {}


@pytest.mark.asyncio
async def test_close_side_is_rejected(broker):
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.place_order(
        Signal(source="test", symbol="BTCUSDT", side=Side.CLOSE), account, quantity=1.0, symbol="BTC/USDT"
    )

    assert result.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_sandbox_true_points_exchange_at_its_test_urls(monkeypatch):
    """Previously there was no way at all to point this broker at an
    exchange's sandbox/testnet -- only ever its real live venue. A real
    (not faked) ccxt Binance instance is used here so this actually
    exercises ccxt's own `set_sandbox_mode`, not a mock of it."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker("binance", sandbox=True)
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    exchange = broker._exchange_for(account)

    assert exchange.isSandboxModeEnabled is True
    assert exchange.urls["api"] == exchange.urls["test"]


@pytest.mark.asyncio
async def test_sandbox_false_leaves_exchange_on_its_real_urls(monkeypatch):
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker("binance", sandbox=False)
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    exchange = broker._exchange_for(account)

    assert not getattr(exchange, "isSandboxModeEnabled", False)


def test_build_ccxt_brokers_registers_one_per_exchange_id():
    """Previously CCXTBroker was only ever constructed ONCE per deployment
    (app/main.py's own broker registry), hardcoding it to a single
    exchange -- a real gap for an operator who wants accounts on two
    different exchanges at once. Each returned broker is a genuinely
    separate CCXTBroker instance pointed at its own exchange_id, not
    the same instance keyed under different names."""
    brokers = build_ccxt_brokers(["binance", "kraken"], sandbox=False)

    assert set(brokers) == {"ccxt_binance", "ccxt_kraken"}
    assert brokers["ccxt_binance"].exchange_id == "binance"
    assert brokers["ccxt_kraken"].exchange_id == "kraken"
    assert brokers["ccxt_binance"] is not brokers["ccxt_kraken"]


def test_build_ccxt_brokers_applies_sandbox_to_every_exchange():
    brokers = build_ccxt_brokers(["binance", "kraken"], sandbox=True)

    assert brokers["ccxt_binance"].sandbox is True
    assert brokers["ccxt_kraken"].sandbox is True


def test_build_ccxt_brokers_empty_list_returns_empty_dict():
    assert build_ccxt_brokers([], sandbox=False) == {}
