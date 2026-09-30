import pytest

pytest.importorskip("async_rithmic")

import async_rithmic

from app.sources.rithmic import RithmicSource


class _FakeNotification:
    def __init__(self, notify_type, transaction_type, symbol, account_id, exchange,
                 fill_size, fill_price, avg_fill_price):
        self.notify_type = notify_type
        self.transaction_type = transaction_type
        self.symbol = symbol
        self.account_id = account_id
        self.exchange = exchange
        self.fill_size = fill_size
        self.fill_price = fill_price
        self.avg_fill_price = avg_fill_price


class _FakeHandlerSet:
    """Mimics async_rithmic's `+=`-registerable event handler."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeRithmicClient:
    last_instance = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.on_exchange_order_notification = _FakeHandlerSet()
        self.connected = False
        _FakeRithmicClient.last_instance = self

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(async_rithmic, "RithmicClient", _FakeRithmicClient)
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = RithmicSource(
        on_signal, user="u", password="p", system_name="s", gateway_url="g"
    )
    return src, received


@pytest.mark.asyncio
async def test_genuine_zero_fill_price_is_not_silently_discarded(source):
    """RISK-FC-02 regression: `fill_price or avg_fill_price or None` used to
    treat a real, reported 0.0 fill price the same as "not reported," and
    fell through to `avg_fill_price` (or None) instead. `signal.price`
    feeding app/engine.py's `_try_reserve_capital` means a price that
    collapses to None isn't just a cosmetic gap -- it SKIPS the account's
    notional-exposure ceiling check entirely. `fill_price=0.0` must be
    reported as a real 0.0, not silently overridden by `avg_fill_price`."""
    src, received = source
    await src.start()

    notification = _FakeNotification(
        notify_type=async_rithmic.ExchangeOrderNotificationType.FILL,
        transaction_type=async_rithmic.TransactionType.BUY,
        symbol="ESZ6",
        account_id="acct1",
        exchange="CME",
        fill_size=1,
        fill_price=0.0,
        avg_fill_price=4500.0,  # a different value -- must NOT be substituted for the real 0.0
    )
    handler = _FakeRithmicClient.last_instance.on_exchange_order_notification.handlers[0]
    await handler(notification)

    assert len(received) == 1
    assert received[0].price == 0.0


@pytest.mark.asyncio
async def test_genuine_zero_fill_size_is_not_silently_discarded(source):
    src, received = source
    await src.start()

    notification = _FakeNotification(
        notify_type=async_rithmic.ExchangeOrderNotificationType.FILL,
        transaction_type=async_rithmic.TransactionType.SELL,
        symbol="ESZ6",
        account_id="acct1",
        exchange="CME",
        fill_size=0.0,
        fill_price=4500.0,
        avg_fill_price=4500.0,
    )
    handler = _FakeRithmicClient.last_instance.on_exchange_order_notification.handlers[0]
    await handler(notification)

    assert len(received) == 1
    assert received[0].quantity == 0.0


@pytest.mark.asyncio
async def test_missing_fill_price_falls_back_to_avg_fill_price(source):
    """When Rithmic genuinely didn't report `fill_price` (None), falling
    back to `avg_fill_price` is still correct -- only a real reported value
    must never be discarded."""
    src, received = source
    await src.start()

    notification = _FakeNotification(
        notify_type=async_rithmic.ExchangeOrderNotificationType.FILL,
        transaction_type=async_rithmic.TransactionType.BUY,
        symbol="ESZ6",
        account_id="acct1",
        exchange="CME",
        fill_size=1,
        fill_price=None,
        avg_fill_price=4501.25,
    )
    handler = _FakeRithmicClient.last_instance.on_exchange_order_notification.handlers[0]
    await handler(notification)

    assert len(received) == 1
    assert received[0].price == 4501.25
