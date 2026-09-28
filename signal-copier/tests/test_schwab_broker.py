import httpx
import pytest

from app.brokers.schwab import SchwabBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side

_ENV_VARS = {
    "SCHWAB_ACCT1_CLIENT_ID": "client-1",
    "SCHWAB_ACCT1_CLIENT_SECRET": "secret-1",
    "SCHWAB_ACCT1_REFRESH_TOKEN": "refresh-1",
    "SCHWAB_ACCT1_ACCOUNT_HASH": "ABC123HASH",
}


def _set_env(monkeypatch):
    for key, value in _ENV_VARS.items():
        monkeypatch.setenv(key, value)


def _fake_auth_response(request):
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800}, request=request)


@pytest.mark.asyncio
async def test_missing_credentials_reports_error(monkeypatch):
    for key in _ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    broker = SchwabBroker()
    account = DestinationAccount(account_id="acct1", broker="schwab")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "SCHWAB_ACCT1_CLIENT_ID" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_successful_order_extracts_id_from_location_header_not_body(monkeypatch):
    """Confirmed via schwab-py's own Utils.extract_order_id: a successful
    placeOrder response carries NO JSON body at all -- only a Location
    header. This test intentionally returns an empty body to prove the
    adapter doesn't depend on one."""
    _set_env(monkeypatch)
    broker = SchwabBroker()
    captured = {}

    async def fake_post(self, url, headers=None, json=None, data=None, auth=None):
        request = httpx.Request("POST", url)
        if url == "https://api.schwabapi.com/v1/oauth/token":
            assert auth == ("client-1", "secret-1")
            assert data["grant_type"] == "refresh_token"
            return _fake_auth_response(request)
        assert url == "https://api.schwabapi.com/trader/v1/accounts/ABC123HASH/orders"
        captured["json"] = json
        captured["headers"] = headers
        return httpx.Response(
            201,
            headers={"Location": "https://api.schwabapi.com/trader/v1/accounts/ABC123HASH/orders/998877"},
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="schwab")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=3.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id == "998877"
    assert captured["json"] == {
        "orderType": "MARKET",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {"instruction": "BUY", "instrument": {"assetType": "EQUITY", "symbol": "AAPL"}, "quantity": 3.0}
        ],
    }
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    await broker.close()


@pytest.mark.asyncio
async def test_sell_sends_sell_instruction(monkeypatch):
    _set_env(monkeypatch)
    broker = SchwabBroker()
    captured = {}

    async def fake_post(self, url, headers=None, json=None, data=None, auth=None):
        request = httpx.Request("POST", url)
        if url == "https://api.schwabapi.com/v1/oauth/token":
            return _fake_auth_response(request)
        captured["json"] = json
        return httpx.Response(
            201,
            headers={"Location": "https://api.schwabapi.com/trader/v1/accounts/ABC123HASH/orders/1"},
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="schwab")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL), account, quantity=1.0, symbol="AAPL"
    )

    assert captured["json"]["orderLegCollection"][0]["instruction"] == "SELL"
    await broker.close()


@pytest.mark.asyncio
async def test_error_status_code_reports_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = SchwabBroker()

    async def fake_post(self, url, headers=None, json=None, data=None, auth=None):
        request = httpx.Request("POST", url)
        if url == "https://api.schwabapi.com/v1/oauth/token":
            return _fake_auth_response(request)
        return httpx.Response(400, text='{"message": "invalid quantity"}', request=request)

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="schwab")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=0.001, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    assert "invalid quantity" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_close_side_is_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = SchwabBroker()
    account = DestinationAccount(account_id="acct1", broker="schwab")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.CLOSE), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_filled_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = SchwabBroker()

    async def fake_post(self, url, headers=None, json=None, data=None, auth=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"status": "FILLED"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="schwab")

    result = await broker.get_order_status(account, "998877")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_rejected_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = SchwabBroker()

    async def fake_post(self, url, headers=None, json=None, data=None, auth=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"status": "REJECTED"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="schwab")

    result = await broker.get_order_status(account, "998877")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_working_reports_nothing_new(monkeypatch):
    _set_env(monkeypatch)
    broker = SchwabBroker()

    async def fake_post(self, url, headers=None, json=None, data=None, auth=None):
        request = httpx.Request("POST", url)
        return _fake_auth_response(request)

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"status": "WORKING"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="schwab")

    result = await broker.get_order_status(account, "998877")

    assert result is None
    await broker.close()
