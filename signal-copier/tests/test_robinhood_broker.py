import httpx
import pytest

from app.brokers.robinhood import RobinhoodBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side

_ENV_VARS = {
    "ROBINHOOD_ACCT1_USERNAME": "trader1",
    "ROBINHOOD_ACCT1_PASSWORD": "pw",
    "ROBINHOOD_ACCT1_ACCOUNT_NUMBER": "5AB12345",
}


def _set_env(monkeypatch):
    for key, value in _ENV_VARS.items():
        monkeypatch.setenv(key, value)


def _fake_login_response(request):
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 86400}, request=request)


@pytest.mark.asyncio
async def test_missing_credentials_reports_error(monkeypatch):
    for key in _ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    broker = RobinhoodBroker()
    account = DestinationAccount(account_id="acct1", broker="robinhood")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "ROBINHOOD_ACCT1_USERNAME" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_buy_is_sent_as_limit_pegged_above_ask_not_plain_market(monkeypatch):
    """Confirmed via robin_stocks's own real order() logic: Robinhood has
    no plain "market buy" during regular hours -- a real buy is
    submitted as a LIMIT order pegged 5% above the current ask price.
    Sending a genuine "market" type for a buy would not match how
    Robinhood's own real API is actually used."""
    _set_env(monkeypatch)
    broker = RobinhoodBroker()
    captured = {}

    async def fake_post(self, url, headers=None, data=None, json=None):
        request = httpx.Request("POST", url)
        if url == "https://api.robinhood.com/oauth2/token/":
            assert data["grant_type"] == "password"
            assert data["client_id"] == "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"
            return _fake_login_response(request)
        assert url == "https://api.robinhood.com/orders/"
        captured["json"] = json
        captured["headers"] = headers
        return httpx.Response(201, json={"id": "order-abc-123"}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        if url == "https://api.robinhood.com/instruments/":
            return httpx.Response(200, json={"results": [{"url": "https://api.robinhood.com/instruments/xyz/"}]}, request=request)
        assert url == "https://api.robinhood.com/quotes/"
        return httpx.Response(200, json={"results": [{"ask_price": "150.25", "bid_price": "150.00"}]}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="robinhood")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=5.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id == "order-abc-123"
    assert captured["json"]["type"] == "limit"
    assert captured["json"]["price"] == 150.25
    assert captured["json"]["preset_percent_limit"] == "0.05"
    assert captured["json"]["side"] == "buy"
    assert captured["json"]["account"] == "https://api.robinhood.com/accounts/5AB12345/"
    assert captured["json"]["instrument"] == "https://api.robinhood.com/instruments/xyz/"
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    await broker.close()


@pytest.mark.asyncio
async def test_sell_is_sent_as_plain_market_order(monkeypatch):
    _set_env(monkeypatch)
    broker = RobinhoodBroker()
    captured = {}

    async def fake_post(self, url, headers=None, data=None, json=None):
        request = httpx.Request("POST", url)
        if url == "https://api.robinhood.com/oauth2/token/":
            return _fake_login_response(request)
        captured["json"] = json
        return httpx.Response(201, json={"id": "order-1"}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"results": [{"url": "https://api.robinhood.com/instruments/xyz/"}]}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="robinhood")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL), account, quantity=1.0, symbol="AAPL"
    )

    assert captured["json"]["type"] == "market"
    assert captured["json"]["side"] == "sell"
    assert "price" not in captured["json"]
    await broker.close()


@pytest.mark.asyncio
async def test_close_side_is_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = RobinhoodBroker()
    account = DestinationAccount(account_id="acct1", broker="robinhood")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.CLOSE), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_unknown_symbol_reports_error(monkeypatch):
    _set_env(monkeypatch)
    broker = RobinhoodBroker()

    async def fake_post(self, url, headers=None, data=None, json=None):
        request = httpx.Request("POST", url)
        return _fake_login_response(request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"results": []}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="robinhood")

    result = await broker.place_order(
        Signal(source="test", symbol="NOTREAL", side=Side.BUY), account, quantity=1.0, symbol="NOTREAL"
    )

    assert result.status == OrderStatus.ERROR
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_filled_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = RobinhoodBroker()

    async def fake_post(self, url, headers=None, data=None, json=None):
        request = httpx.Request("POST", url)
        return _fake_login_response(request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"id": "order-1", "state": "filled"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="robinhood")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_rejected_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = RobinhoodBroker()

    async def fake_post(self, url, headers=None, data=None, json=None):
        request = httpx.Request("POST", url)
        return _fake_login_response(request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"id": "order-1", "state": "rejected"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="robinhood")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_queued_reports_nothing_new(monkeypatch):
    _set_env(monkeypatch)
    broker = RobinhoodBroker()

    async def fake_post(self, url, headers=None, data=None, json=None):
        request = httpx.Request("POST", url)
        return _fake_login_response(request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"id": "order-1", "state": "queued"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="robinhood")

    result = await broker.get_order_status(account, "order-1")

    assert result is None
    await broker.close()
