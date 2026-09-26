"""ADP-07: Alpaca managed positions had no `get_last_price` implementation,
so `has_last_price_capability` was False and `PriceMonitor` (app/pricing.py)
silently never polled Alpaca positions at all -- targets, trailing, and
stop resizing on a managed Alpaca account were dead code in practice.

Reproduces the audit's exact case (test_state_and_input_audit.py::
test_alpaca_managed_targets_need_price_observations).
"""
import httpx
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.models import DestinationAccount


@pytest.mark.asyncio
async def test_audits_exact_case_has_last_price_capability():
    broker = AlpacaBroker()
    try:
        assert broker.has_last_price_capability, "No target/trailing observation capability in this adapter"
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_get_last_price_reads_latest_trade_from_market_data_host(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()
    captured = {}

    async def fake_get(self, url, headers):
        captured["url"] = url
        captured["headers"] = headers
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"trade": {"p": 123.45}}, request=request)

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    price = await broker.get_last_price(account, "AAPL")

    assert price == 123.45
    assert captured["url"] == "https://data.alpaca.markets/v2/stocks/AAPL/trades/latest"
    assert captured["headers"]["APCA-API-KEY-ID"] == "key123"
    await broker.close()


@pytest.mark.asyncio
async def test_get_last_price_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        return httpx.Response(500, json={"message": "boom"}, request=request)

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    price = await broker.get_last_price(account, "AAPL")

    assert price is None
    await broker.close()


@pytest.mark.asyncio
async def test_get_last_price_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("ALPACA_ACCT1_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_ACCT1_API_SECRET", raising=False)
    broker = AlpacaBroker()
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    price = await broker.get_last_price(account, "AAPL")

    assert price is None
    await broker.close()
