import httpx
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


@pytest.mark.asyncio
async def test_missing_credentials_reports_error(monkeypatch):
    monkeypatch.delenv("ALPACA_ACCT1_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_ACCT1_API_SECRET", raising=False)
    broker = AlpacaBroker()
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "ALPACA_ACCT1_API_KEY" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_successful_order_reports_pending(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    captured = {}

    async def fake_post(self, url, headers, json):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"id": "order-1", "status": "accepted"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="alpaca")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=2.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id == "order-1"
    assert captured["url"] == "https://paper-api.alpaca.markets/v2/orders"
    assert captured["headers"]["APCA-API-KEY-ID"] == "key123"
    assert captured["json"] == {
        "symbol": "AAPL",
        "qty": "2.0",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    await broker.close()


@pytest.mark.asyncio
async def test_both_sl_and_tp_sends_bracket_order(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    captured = {}

    async def fake_post(self, url, headers, json):
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"id": "order-1", "status": "accepted"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="alpaca")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY, stop_loss=185.0, take_profit=200.0),
        account,
        quantity=1.0,
        symbol="AAPL",
    )

    assert captured["json"]["order_class"] == "bracket"
    assert captured["json"]["take_profit"] == {"limit_price": 200.0}
    assert captured["json"]["stop_loss"] == {"stop_price": 185.0}
    await broker.close()


@pytest.mark.asyncio
async def test_only_take_profit_sends_oto_order(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    captured = {}

    async def fake_post(self, url, headers, json):
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"id": "order-1", "status": "accepted"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="alpaca")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY, take_profit=200.0),
        account,
        quantity=1.0,
        symbol="AAPL",
    )

    assert captured["json"]["order_class"] == "oto"
    assert captured["json"]["take_profit"] == {"limit_price": 200.0}
    assert "stop_loss" not in captured["json"]
    await broker.close()


@pytest.mark.asyncio
async def test_close_side_is_rejected(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.CLOSE), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()
