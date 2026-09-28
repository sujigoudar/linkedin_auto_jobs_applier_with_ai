import httpx
import pytest

from app.brokers.tastytrade import TastytradeBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side

_ENV_VARS = {
    "TASTYTRADE_ACCT1_SECRET": "secret-1",
    "TASTYTRADE_ACCT1_REFRESH_TOKEN": "refresh-1",
    "TASTYTRADE_ACCT1_TT_ACCOUNT_NUMBER": "5WX00001",
}


def _set_env(monkeypatch):
    for key, value in _ENV_VARS.items():
        monkeypatch.setenv(key, value)


def _fake_auth_response(request):
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 900}, request=request)


@pytest.mark.asyncio
async def test_missing_credentials_reports_error(monkeypatch):
    for key in _ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    broker = TastytradeBroker()
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "TASTYTRADE_ACCT1_SECRET" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_successful_buy_sends_buy_to_open_kebab_case_body(monkeypatch):
    """Tastytrade's own real field naming is kebab-case (confirmed via
    the tastyware/tastytrade SDK's own `_dasherize` alias generator) --
    a body sent with underscored/camelCase keys would be silently
    ignored by the real API."""
    _set_env(monkeypatch)
    broker = TastytradeBroker()
    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        if url.endswith("/oauth/token"):
            assert json["grant_type"] == "refresh_token"
            assert json["client_secret"] == "secret-1"
            return _fake_auth_response(request)
        assert url == "https://api.cert.tastyworks.com/accounts/5WX00001/orders"
        captured["json"] = json
        captured["headers"] = headers
        return httpx.Response(
            201,
            json={"data": {"order": {"id": 998877, "status": "Received"}}},
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=5.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id == "998877"
    assert captured["json"] == {
        "order-type": "Market",
        "time-in-force": "Day",
        "legs": [{"instrument-type": "Equity", "symbol": "AAPL", "action": "Buy to Open", "quantity": 5.0}],
    }
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    await broker.close()


@pytest.mark.asyncio
async def test_sell_sends_sell_to_open(monkeypatch):
    _set_env(monkeypatch)
    broker = TastytradeBroker()
    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        if url.endswith("/oauth/token"):
            return _fake_auth_response(request)
        captured["json"] = json
        return httpx.Response(201, json={"data": {"order": {"id": 1, "status": "Received"}}}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL), account, quantity=1.0, symbol="AAPL"
    )

    assert captured["json"]["legs"][0]["action"] == "Sell to Open"
    await broker.close()


@pytest.mark.asyncio
async def test_errors_field_reports_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = TastytradeBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        if url.endswith("/oauth/token"):
            return _fake_auth_response(request)
        return httpx.Response(
            201, json={"data": {"errors": [{"code": "invalid_quantity", "message": "quantity too small"}]}},
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=0.001, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_close_side_is_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = TastytradeBroker()
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.CLOSE), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_live_env_uses_live_url(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setenv("TASTYTRADE_ACCT1_ENV", "live")
    broker = TastytradeBroker()
    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        if url.endswith("/oauth/token"):
            return _fake_auth_response(request)
        captured["url"] = url
        return httpx.Response(201, json={"data": {"order": {"id": 1, "status": "Received"}}}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert captured["url"].startswith("https://api.tastyworks.com")
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_filled_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = TastytradeBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": {"id": 1, "status": "Filled"}}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    result = await broker.get_order_status(account, "1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_rejected_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = TastytradeBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": {"id": 1, "status": "Rejected"}}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    result = await broker.get_order_status(account, "1")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_live_reports_nothing_new(monkeypatch):
    _set_env(monkeypatch)
    broker = TastytradeBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": {"id": 1, "status": "Live"}}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="tastytrade")

    result = await broker.get_order_status(account, "1")

    assert result is None
    await broker.close()
