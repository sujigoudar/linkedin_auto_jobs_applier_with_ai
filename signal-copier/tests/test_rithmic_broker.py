"""app/brokers/rithmic.py's `RithmicBroker` -- the actual order-placement
logic had NO test coverage at all before this file. `tests/test_broker_
capability_gate.py` only checks the class's DECLARED capability flags
(has_protective_stop_capability etc.); nothing anywhere calls `place_order`
or `close`. Every other broker adapter in this codebase (Alpaca, IBKR,
Robinhood, Schwab, Tastytrade, TradeStation, Tradovate, OANDA, CCXT,
MT5Broker, MetaApiBroker) has a test file exercising its real
`place_order` behavior; this one didn't.

Built the same way this codebase already tests other SDK-backed brokers
whose real client can't be constructed in CI (`test_adp08_mt5_partial_
fill.py` for `MT5Broker`, `test_metaapi_broker.py` for `MetaApiBroker`):
`object.__new__` bypasses `__init__` (which opens a real `RithmicClient`
connection), and a fake `_client` stands in for the SDK's own async
methods.
"""
from __future__ import annotations

import pytest

from app.brokers.rithmic import RithmicBroker
from app.models import DestinationAccount, OrderStatus, Side, Signal

ACCOUNT = DestinationAccount(account_id="acct1", broker="rithmic")


class _FakeOrderType:
    MARKET = "MARKET"


class _FakeTransactionType:
    BUY = "BUY"
    SELL = "SELL"


class _FakeClient:
    def __init__(self, *, submit_raises: Exception | None = None):
        self.connected = False
        self.disconnected = False
        self.submit_calls: list[dict] = []
        self.exit_calls: list[dict] = []
        self._submit_raises = submit_raises

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def submit_order(self, order_id, symbol, exchange, *, qty, order_type, transaction_type, account_id):
        if self._submit_raises:
            raise self._submit_raises
        self.submit_calls.append(
            dict(
                order_id=order_id, symbol=symbol, exchange=exchange, qty=qty,
                order_type=order_type, transaction_type=transaction_type, account_id=account_id,
            )
        )

    async def exit_position(self, *, symbol, exchange):
        self.exit_calls.append({"symbol": symbol, "exchange": exchange})


def _broker(client: _FakeClient) -> RithmicBroker:
    """Bypasses `__init__` (which imports `async_rithmic` and opens a real
    connection) -- same `object.__new__` technique this file's own sibling
    tests use for `MT5Broker`/`MetaApiBroker`."""
    import asyncio

    broker = object.__new__(RithmicBroker)
    broker._OrderType = _FakeOrderType
    broker._TransactionType = _FakeTransactionType
    broker._client = client
    broker._connect_lock = asyncio.Lock()
    broker._connected = False
    return broker


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RITHMIC_ACCT1_RITHMIC_ACCOUNT_ID", "R-12345")
    monkeypatch.setenv("RITHMIC_ACCT1_EXCHANGE", "CME")


# --- place_order: buy/sell dispatch and connection lifecycle ------------


@pytest.mark.asyncio
async def test_a_buy_signal_submits_a_buy_order_with_the_resolved_rithmic_account_and_exchange():
    client = _FakeClient()
    broker = _broker(client)

    result = await broker.place_order(Signal("s", "ESZ5", Side.BUY), ACCOUNT, 2.0, "ESZ5")

    assert result.status == OrderStatus.PENDING
    assert len(client.submit_calls) == 1
    call = client.submit_calls[0]
    assert call["symbol"] == "ESZ5"
    assert call["exchange"] == "CME"
    assert call["account_id"] == "R-12345"
    assert call["transaction_type"] == _FakeTransactionType.BUY
    assert call["qty"] == 2
    assert result.broker_order_id == call["order_id"]


@pytest.mark.asyncio
async def test_a_sell_signal_submits_a_sell_order_not_a_buy():
    client = _FakeClient()
    broker = _broker(client)

    await broker.place_order(Signal("s", "ESZ5", Side.SELL), ACCOUNT, 1.0, "ESZ5")

    assert client.submit_calls[0]["transaction_type"] == _FakeTransactionType.SELL


@pytest.mark.asyncio
async def test_a_close_signal_calls_exit_position_not_submit_order():
    """CLOSE is a genuinely different Rithmic call
    (`exit_position`) -- never routed through `submit_order`, which has
    no notion of closing an existing position."""
    client = _FakeClient()
    broker = _broker(client)

    result = await broker.place_order(Signal("s", "ESZ5", Side.CLOSE), ACCOUNT, 1.0, "ESZ5")

    assert result.status == OrderStatus.PENDING
    assert client.exit_calls == [{"symbol": "ESZ5", "exchange": "CME"}]
    assert client.submit_calls == []


@pytest.mark.asyncio
async def test_first_order_connects_the_client_a_second_does_not_reconnect():
    client = _FakeClient()
    broker = _broker(client)
    assert client.connected is False

    await broker.place_order(Signal("s", "ESZ5", Side.BUY), ACCOUNT, 1.0, "ESZ5")
    assert client.connected is True

    client.connected = "should-not-be-touched-again"  # sentinel: a second connect() would overwrite this
    await broker.place_order(Signal("s", "ESZ5", Side.BUY), ACCOUNT, 1.0, "ESZ5")
    assert client.connected == "should-not-be-touched-again"


# --- place_order: missing account config and transport errors -----------


@pytest.mark.asyncio
async def test_missing_rithmic_account_env_vars_is_an_error_result_not_an_unhandled_exception(monkeypatch):
    monkeypatch.delenv("RITHMIC_ACCT1_RITHMIC_ACCOUNT_ID", raising=False)
    client = _FakeClient()
    broker = _broker(client)

    result = await broker.place_order(Signal("s", "ESZ5", Side.BUY), ACCOUNT, 1.0, "ESZ5")

    assert result.status == OrderStatus.ERROR
    assert "RITHMIC_ACCT1_RITHMIC_ACCOUNT_ID" in result.message
    # Never even attempted a connection for a request that can't be routed.
    assert client.connected is False
    assert client.submit_calls == []


@pytest.mark.asyncio
async def test_a_submit_order_exception_is_reported_as_error_not_raised():
    """A genuinely ambiguous outcome (the request may or may not have
    reached Rithmic before the failure) -- must come back as an ERROR
    OrderResult the caller can act on, never propagate and crash the
    engine."""
    client = _FakeClient(submit_raises=ConnectionError("simulated Rithmic transport failure"))
    broker = _broker(client)

    result = await broker.place_order(Signal("s", "ESZ5", Side.BUY), ACCOUNT, 1.0, "ESZ5")

    assert result.status == OrderStatus.ERROR
    assert "simulated Rithmic transport failure" in result.message


# --- close() --------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_disconnects_when_a_connection_was_ever_established():
    client = _FakeClient()
    broker = _broker(client)
    await broker.place_order(Signal("s", "ESZ5", Side.BUY), ACCOUNT, 1.0, "ESZ5")  # connects

    await broker.close()

    assert client.disconnected is True


@pytest.mark.asyncio
async def test_close_is_a_no_op_when_never_connected():
    """No spurious `disconnect()` call against a client that was never
    actually connected (e.g. every order for this account failed before
    `_ensure_connected` ever ran)."""
    client = _FakeClient()
    broker = _broker(client)

    await broker.close()

    assert client.disconnected is False
