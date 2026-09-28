import httpx
import pytest

from app.brokers.tradestation import TradeStationBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side

_ENV_VARS = {
    "TRADESTATION_ACCT1_CLIENT_ID": "client-1",
    "TRADESTATION_ACCT1_REFRESH_TOKEN": "refresh-1",
    "TRADESTATION_ACCT1_TS_ACCOUNT_ID": "TS12345",
}


def _set_env(monkeypatch):
    for key, value in _ENV_VARS.items():
        monkeypatch.setenv(key, value)


def _fake_auth_response(request):
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1200}, request=request)


@pytest.mark.asyncio
async def test_missing_credentials_reports_error(monkeypatch):
    for key in _ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    broker = TradeStationBroker()
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "TRADESTATION_ACCT1_CLIENT_ID" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_successful_order_reports_pending(monkeypatch):
    _set_env(monkeypatch)
    broker = TradeStationBroker()
    captured = {}

    async def fake_post(self, url, data=None, headers=None, json=None):
        request = httpx.Request("POST", url)
        if url == "https://signin.tradestation.com/oauth/token":
            assert data["grant_type"] == "refresh_token"
            assert data["client_id"] == "client-1"
            assert data["refresh_token"] == "refresh-1"
            assert "client_secret" not in data  # not set in env -- must not be sent
            return _fake_auth_response(request)
        assert url == "https://sim.api.tradestation.com/v3/orderexecution/orders"
        captured["json"] = json
        captured["headers"] = headers
        return httpx.Response(200, json={"Orders": [{"OrderID": "ORD-1", "Message": "received"}]}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=2.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id == "ORD-1"
    assert captured["json"] == {
        "AccountID": "TS12345",
        "Symbol": "AAPL",
        "Quantity": "2.0",
        "OrderType": "Market",
        "TradeAction": "BUY",
        "TimeInForce": {"Duration": "DAY"},
        "Route": "Intelligent",
    }
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    await broker.close()


@pytest.mark.asyncio
async def test_sell_sends_sell_trade_action(monkeypatch):
    _set_env(monkeypatch)
    broker = TradeStationBroker()
    captured = {}

    async def fake_post(self, url, data=None, headers=None, json=None):
        request = httpx.Request("POST", url)
        if url == "https://signin.tradestation.com/oauth/token":
            return _fake_auth_response(request)
        captured["json"] = json
        return httpx.Response(200, json={"Orders": [{"OrderID": "ORD-2"}]}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL), account, quantity=1.0, symbol="AAPL"
    )

    assert captured["json"]["TradeAction"] == "SELL"
    await broker.close()


@pytest.mark.asyncio
async def test_errors_field_reports_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = TradeStationBroker()

    async def fake_post(self, url, data=None, headers=None, json=None):
        request = httpx.Request("POST", url)
        if url == "https://signin.tradestation.com/oauth/token":
            return _fake_auth_response(request)
        return httpx.Response(
            200, json={"Errors": [{"OrderID": "", "Error": "InvalidQuantity", "Message": "quantity too small"}]},
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=0.001, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    assert "quantity too small" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_close_side_is_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = TradeStationBroker()
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.CLOSE), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_live_env_uses_live_url(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setenv("TRADESTATION_ACCT1_ENV", "live")
    broker = TradeStationBroker()
    captured = {}

    async def fake_post(self, url, data=None, headers=None, json=None):
        request = httpx.Request("POST", url)
        if url == "https://signin.tradestation.com/oauth/token":
            return _fake_auth_response(request)
        captured["url"] = url
        return httpx.Response(200, json={"Orders": [{"OrderID": "ORD-1"}]}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert captured["url"].startswith("https://api.tradestation.com")
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_filled_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = TradeStationBroker()

    async def fake_post(self, url, data=None, headers=None, json=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"Orders": [{"OrderID": "ORD-1", "Status": "FLL"}]}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    result = await broker.get_order_status(account, "ORD-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_rejected_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = TradeStationBroker()

    async def fake_post(self, url, data=None, headers=None, json=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"Orders": [{"OrderID": "ORD-1", "Status": "REJ"}]}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    result = await broker.get_order_status(account, "ORD-1")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_working_reports_nothing_new(monkeypatch):
    _set_env(monkeypatch)
    broker = TradeStationBroker()

    async def fake_post(self, url, data=None, headers=None, json=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"Orders": [{"OrderID": "ORD-1", "Status": "ACK"}]}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tradestation")

    result = await broker.get_order_status(account, "ORD-1")

    assert result is None
    await broker.close()
