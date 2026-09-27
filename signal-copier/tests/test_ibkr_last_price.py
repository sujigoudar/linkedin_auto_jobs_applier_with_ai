"""Extends the live price feed driving PriceMonitor (app/pricing.py)
beyond ccxt/Alpaca to IBKR, via ib_async's `reqTickersAsync` one-shot
market-data snapshot.
"""
import math

import pytest

pytest.importorskip("ib_async")

from app.brokers.ibkr import IBKRBroker
from app.models import DestinationAccount


class _FakeTicker:
    def __init__(self, last=math.nan, close=math.nan):
        self.last = last
        self.close = close


class _FakeIB:
    def __init__(self, tickers):
        self._tickers = tickers

    async def reqTickersAsync(self, contract):
        return self._tickers


@pytest.fixture
def broker():
    return IBKRBroker()


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_declares_last_price_capability(broker):
    assert broker.has_last_price_capability is True


@pytest.mark.asyncio
async def test_get_last_price_reads_the_last_trade(broker, monkeypatch):
    fake_ib = _FakeIB([_FakeTicker(last=123.45)])
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    price = await broker.get_last_price(account, "AAPL")

    assert price == 123.45


@pytest.mark.asyncio
async def test_get_last_price_falls_back_to_close_when_last_is_nan(broker, monkeypatch):
    """No live/delayed trade-tick entitlement reports `last` as NaN (ib_async's
    convention for "no value"), not None -- close (yesterday's settle) is
    reported far more reliably and is still a usable, real price."""
    fake_ib = _FakeIB([_FakeTicker(last=math.nan, close=99.0)])
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    price = await broker.get_last_price(account, "AAPL")

    assert price == 99.0


@pytest.mark.asyncio
async def test_get_last_price_returns_none_when_neither_last_nor_close_is_reported(broker, monkeypatch):
    fake_ib = _FakeIB([_FakeTicker(last=math.nan, close=math.nan)])
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    assert await broker.get_last_price(account, "AAPL") is None


@pytest.mark.asyncio
async def test_get_last_price_returns_none_when_the_snapshot_request_fails(broker, monkeypatch):
    class _FailingIB:
        async def reqTickersAsync(self, contract):
            raise RuntimeError("no market data subscription")

    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(_FailingIB()))
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    assert await broker.get_last_price(account, "AAPL") is None


@pytest.mark.asyncio
async def test_get_last_price_returns_none_when_not_connected(broker, monkeypatch):
    async def _raise_connection_error():
        raise RuntimeError("could not connect")

    monkeypatch.setattr(broker, "_connected_ib", _raise_connection_error)
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    assert await broker.get_last_price(account, "AAPL") is None
