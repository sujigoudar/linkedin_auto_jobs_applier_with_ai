"""app/brokers/mt4_mt5.py's `MetaApiBroker` -- the MT4/MT5 cloud-SDK
execution path -- had NO test coverage at all before this file (every
other broker adapter in this codebase -- Alpaca, IBKR, Robinhood, Schwab,
Tastytrade, TradeStation, Tradovate, OANDA, CCXT, and even `MT5Broker`'s
own Windows-only IPC path -- has a dedicated test file; this one didn't).
Unlike `MT5Broker` (which genuinely requires a Windows host + local MT5
terminal and can only be spot-tested), `MetaApiBroker` talks to a cloud
API and is exercised here the same way this codebase already tests
CCXT/Alpaca/etc.: build the instance via `object.__new__` (bypassing
`__init__`, which needs a real `MT4_MT5_METAAPI_TOKEN`) and inject fake
collaborators.

Covers the real, previously-untested behavior:
  - a FILLED result requires the connection's own
    stringCode == "TRADE_RETCODE_DONE" -- anything else (including a
    perfectly normal "TRADE_RETCODE_PLACED"/partial-fill style code) is
    PENDING, never silently upgraded to FILLED;
  - buy/sell dispatch to the matching connection method with
    stop_loss/take_profit forwarded only when the signal actually carries
    them;
  - a 'close' side is rejected locally, before ever touching the
    connection (this broker only accepts buy/sell, same contract as
    every other adapter in this file);
  - an exception raised by the connection during order placement is
    reported as ERROR, never left to propagate and crash the caller;
  - `_connection_for`'s own account-id resolution: a missing
    MT4_MT5_METAAPI_{ACCOUNT_ID}_ID environment variable is a caught
    RuntimeError -> ERROR OrderResult, not an unhandled exception, and a
    connection is cached per account_id (not re-established on every
    call);
  - `close()` closes every cached connection, not just one.
"""
from __future__ import annotations

import pytest

from app.brokers.mt4_mt5 import MetaApiBroker
from app.models import DestinationAccount, OrderStatus, Side, Signal

ACCOUNT = DestinationAccount(account_id="acct1", broker="mt4_mt5_metaapi")


def _broker() -> MetaApiBroker:
    """Bypasses `__init__` (which imports `metaapi_cloud_sdk` and requires
    a real `MT4_MT5_METAAPI_TOKEN`) -- the same `object.__new__` technique
    this file's own `test_adp08_mt5_partial_fill.py` already uses for
    `MT5Broker`."""
    broker = object.__new__(MetaApiBroker)
    broker._token = "test-token"
    broker._api = object()  # non-None: _connection_for must never touch this in these tests
    broker._connections = {}
    return broker


class _FakeConnection:
    def __init__(self, result=None, *, raises: Exception | None = None):
        self._result = result if result is not None else {"stringCode": "TRADE_RETCODE_DONE", "orderId": "o-1"}
        self._raises = raises
        self.buy_calls: list[dict] = []
        self.sell_calls: list[dict] = []
        self.closed = False

    async def create_market_buy_order(self, **kwargs):
        self.buy_calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._result

    async def create_market_sell_order(self, **kwargs):
        self.sell_calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._result

    async def close(self):
        self.closed = True


def _wire(broker: MetaApiBroker, connection: _FakeConnection) -> None:
    broker._connections["acct1"] = connection


# --- place_order: status mapping ---------------------------------------


@pytest.mark.asyncio
async def test_a_confirmed_done_retcode_is_filled():
    broker = _broker()
    connection = _FakeConnection({"stringCode": "TRADE_RETCODE_DONE", "orderId": "o-42"})
    _wire(broker, connection)

    result = await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 1.0, "EURUSD")

    assert result.status == OrderStatus.FILLED
    assert result.broker_order_id == "o-42"


@pytest.mark.asyncio
async def test_any_other_string_code_is_pending_never_silently_upgraded_to_filled():
    """The load-bearing distinction this class's status mapping makes:
    only the EXACT string "TRADE_RETCODE_DONE" counts as a confirmed
    fill. A regression that dropped this check (e.g. `if result: FILLED`)
    would wrongly report every accepted-but-unconfirmed order as filled."""
    broker = _broker()
    connection = _FakeConnection({"stringCode": "TRADE_RETCODE_PLACED", "orderId": "o-43"})
    _wire(broker, connection)

    result = await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 1.0, "EURUSD")

    assert result.status == OrderStatus.PENDING
    assert result.status != OrderStatus.FILLED


@pytest.mark.asyncio
async def test_missing_string_code_is_pending_not_filled():
    broker = _broker()
    connection = _FakeConnection({"orderId": "o-44"})  # no stringCode at all
    _wire(broker, connection)

    result = await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 1.0, "EURUSD")

    assert result.status == OrderStatus.PENDING


# --- place_order: side dispatch and sl/tp forwarding --------------------


@pytest.mark.asyncio
async def test_buy_side_calls_create_market_buy_order_not_sell():
    broker = _broker()
    connection = _FakeConnection()
    _wire(broker, connection)

    await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 2.5, "EURUSD")

    assert len(connection.buy_calls) == 1
    assert connection.sell_calls == []
    assert connection.buy_calls[0]["symbol"] == "EURUSD"
    assert connection.buy_calls[0]["volume"] == 2.5


@pytest.mark.asyncio
async def test_sell_side_calls_create_market_sell_order_not_buy():
    broker = _broker()
    connection = _FakeConnection()
    _wire(broker, connection)

    await broker.place_order(Signal("s", "EURUSD", Side.SELL), ACCOUNT, 1.0, "EURUSD")

    assert len(connection.sell_calls) == 1
    assert connection.buy_calls == []


@pytest.mark.asyncio
async def test_stop_loss_and_take_profit_are_forwarded_when_present():
    broker = _broker()
    connection = _FakeConnection()
    _wire(broker, connection)

    signal = Signal("s", "EURUSD", Side.BUY, stop_loss=1.05, take_profit=1.20)
    await broker.place_order(signal, ACCOUNT, 1.0, "EURUSD")

    assert connection.buy_calls[0]["stop_loss"] == 1.05
    assert connection.buy_calls[0]["take_profit"] == 1.20


@pytest.mark.asyncio
async def test_no_stop_loss_or_take_profit_means_neither_kwarg_is_sent():
    """A signal with no protective levels must not send `stop_loss=None`/
    `take_profit=None` to the connection -- the kwargs are omitted
    entirely, not sent as an explicit null."""
    broker = _broker()
    connection = _FakeConnection()
    _wire(broker, connection)

    await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 1.0, "EURUSD")

    assert "stop_loss" not in connection.buy_calls[0]
    assert "take_profit" not in connection.buy_calls[0]


# --- place_order: 'close' side and exceptions ----------------------------


@pytest.mark.asyncio
async def test_close_side_is_rejected_locally_without_touching_the_connection():
    broker = _broker()
    connection = _FakeConnection()
    _wire(broker, connection)

    result = await broker.place_order(Signal("s", "EURUSD", Side.CLOSE), ACCOUNT, 1.0, "EURUSD")

    assert result.status == OrderStatus.REJECTED
    assert "only accepts buy/sell" in result.message
    assert connection.buy_calls == []
    assert connection.sell_calls == []


@pytest.mark.asyncio
async def test_an_exception_from_the_connection_is_reported_as_error_not_raised():
    broker = _broker()
    connection = _FakeConnection(raises=RuntimeError("simulated MetaApi transport failure"))
    _wire(broker, connection)

    result = await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 1.0, "EURUSD")

    assert result.status == OrderStatus.ERROR
    assert "simulated MetaApi transport failure" in result.message


# --- _connection_for: account resolution and caching ---------------------


@pytest.mark.asyncio
async def test_a_missing_account_id_env_var_is_a_caught_error_not_an_unhandled_exception(monkeypatch):
    """`_connection_for` raises RuntimeError for a missing
    MT4_MT5_METAAPI_{ACCOUNT_ID}_ID -- `place_order` must catch that (it
    only explicitly catches RuntimeError) and turn it into an ERROR
    result, never let it propagate out of place_order."""
    broker = _broker()
    monkeypatch.delenv("MT4_MT5_METAAPI_ACCT1_ID", raising=False)

    result = await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 1.0, "EURUSD")

    assert result.status == OrderStatus.ERROR
    assert "MT4_MT5_METAAPI_ACCT1_ID" in result.message


@pytest.mark.asyncio
async def test_an_already_cached_connection_is_reused_not_rebuilt(monkeypatch):
    """Once a connection exists in `_connections`, a second call for the
    same account must reuse it directly -- never re-enter the
    MetaApi()/deploy/wait_connected setup path (which would need a real
    `metaapi_cloud_sdk` import and a live token)."""
    broker = _broker()
    connection = _FakeConnection()
    _wire(broker, connection)

    await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 1.0, "EURUSD")
    await broker.place_order(Signal("s", "EURUSD", Side.BUY), ACCOUNT, 1.0, "EURUSD")

    assert len(connection.buy_calls) == 2
    assert broker._connections["acct1"] is connection  # never replaced


# --- close() -------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_closes_every_cached_connection_not_just_one():
    broker = _broker()
    conn_a = _FakeConnection()
    conn_b = _FakeConnection()
    broker._connections = {"acct1": conn_a, "acct2": conn_b}

    await broker.close()

    assert conn_a.closed is True
    assert conn_b.closed is True
