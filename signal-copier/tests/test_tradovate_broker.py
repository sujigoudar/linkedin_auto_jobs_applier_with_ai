import httpx
import pytest

from app.brokers.tradovate import TradovateBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side

_ENV_VARS = {
    "TRADOVATE_ACCT1_USERNAME": "trader1",
    "TRADOVATE_ACCT1_PASSWORD": "pw",
    "TRADOVATE_ACCT1_APP_ID": "myapp",
    "TRADOVATE_ACCT1_CID": "123",
    "TRADOVATE_ACCT1_SECRET": "sec",
    "TRADOVATE_ACCT1_DEVICE_ID": "device-1",
    "TRADOVATE_ACCT1_ACCOUNT_SPEC": "DEMO123456",
}


def _set_env(monkeypatch):
    for key, value in _ENV_VARS.items():
        monkeypatch.setenv(key, value)


@pytest.mark.asyncio
async def test_missing_credentials_reports_error(monkeypatch):
    for key in _ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    broker = TradovateBroker()
    account = DestinationAccount(account_id="acct1", broker="tradovate")

    result = await broker.place_order(
        Signal(source="test", symbol="MNQZ4", side=Side.BUY), account, quantity=1.0, symbol="MNQZ4"
    )

    assert result.status == OrderStatus.ERROR
    assert "TRADOVATE_ACCT1_USERNAME" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_successful_order_reports_pending_and_sends_isautomated_true(monkeypatch):
    """Tradovate's own official tutorial README states this exactly: an
    algorithmic order MUST carry isAutomated=true or it can violate real
    exchange policy -- this is a compliance requirement, not a style
    choice, so this test asserts it's always sent, never omitted."""
    _set_env(monkeypatch)
    broker = TradovateBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        if url.endswith("/auth/accesstokenrequest"):
            return httpx.Response(200, json={"accessToken": "tok-1", "expirationTime": "2099-01-01T00:00:00Z"}, request=request)
        assert url.endswith("/order/placeOrder")
        captured["order_json"] = json
        captured["order_headers"] = headers
        return httpx.Response(200, json={"orderId": 555}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        assert url.endswith("/account/list")
        return httpx.Response(200, json=[{"id": 999, "name": "DEMO123456"}], request=request)

    captured: dict = {}
    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradovate")
    result = await broker.place_order(
        Signal(source="test", symbol="MNQZ4", side=Side.BUY), account, quantity=2.0, symbol="MNQZ4"
    )

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id == "555"
    assert captured["order_json"]["isAutomated"] is True
    assert captured["order_json"]["accountId"] == 999
    assert captured["order_json"]["accountSpec"] == "DEMO123456"
    assert captured["order_json"]["action"] == "Buy"
    assert captured["order_json"]["orderQty"] == 2
    assert captured["order_headers"]["Authorization"] == "Bearer tok-1"
    await broker.close()


@pytest.mark.asyncio
async def test_a_fractional_contract_quantity_is_rejected_not_silently_truncated(monkeypatch):
    """`orderQty=int(quantity)` truncates toward zero with no warning at
    all -- a computed size of 4.9 contracts must never quietly become 4.
    This must be refused, not rounded, and no order request sent."""
    _set_env(monkeypatch)
    broker = TradovateBroker()
    post_calls = []

    async def fake_post(self, url, json=None, headers=None):
        post_calls.append(url)
        request = httpx.Request("POST", url)
        if url.endswith("/auth/accesstokenrequest"):
            return httpx.Response(200, json={"accessToken": "tok-1", "expirationTime": "2099-01-01T00:00:00Z"}, request=request)
        return httpx.Response(200, json={"orderId": 1}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=[{"id": 999, "name": "DEMO123456"}], request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradovate")
    result = await broker.place_order(
        Signal(source="test", symbol="MNQZ4", side=Side.BUY), account, quantity=4.9, symbol="MNQZ4"
    )

    assert result.status == OrderStatus.ERROR
    assert not any(url.endswith("/order/placeOrder") for url in post_calls)
    await broker.close()


@pytest.mark.asyncio
async def test_failure_text_reports_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = TradovateBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        if url.endswith("/auth/accesstokenrequest"):
            return httpx.Response(200, json={"accessToken": "tok-1", "expirationTime": "2099-01-01T00:00:00Z"}, request=request)
        return httpx.Response(200, json={"failureText": "insufficient margin"}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=[{"id": 999, "name": "DEMO123456"}], request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradovate")
    result = await broker.place_order(
        Signal(source="test", symbol="MNQZ4", side=Side.SELL), account, quantity=1.0, symbol="MNQZ4"
    )

    assert result.status == OrderStatus.REJECTED
    assert "insufficient margin" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_close_side_is_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = TradovateBroker()
    account = DestinationAccount(account_id="acct1", broker="tradovate")

    result = await broker.place_order(
        Signal(source="test", symbol="MNQZ4", side=Side.CLOSE), account, quantity=1.0, symbol="MNQZ4"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_access_token_is_cached_not_re_requested(monkeypatch):
    """A second place_order call within the token's TTL must reuse the
    cached token rather than hitting /auth/accesstokenrequest again."""
    _set_env(monkeypatch)
    broker = TradovateBroker()
    auth_calls = []

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        if url.endswith("/auth/accesstokenrequest"):
            auth_calls.append(1)
            return httpx.Response(200, json={"accessToken": "tok-1", "expirationTime": "2099-01-01T00:00:00Z"}, request=request)
        return httpx.Response(200, json={"orderId": 1}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=[{"id": 999, "name": "DEMO123456"}], request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradovate")
    signal = Signal(source="test", symbol="MNQZ4", side=Side.BUY)
    await broker.place_order(signal, account, quantity=1.0, symbol="MNQZ4")
    await broker.place_order(signal, account, quantity=1.0, symbol="MNQZ4")

    assert len(auth_calls) == 1
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_filled_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = TradovateBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"accessToken": "tok-1", "expirationTime": "2099-01-01T00:00:00Z"}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"ordStatus": "Filled"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradovate")
    result = await broker.get_order_status(account, "555")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_rejected_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = TradovateBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"accessToken": "tok-1", "expirationTime": "2099-01-01T00:00:00Z"}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"ordStatus": "Rejected"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradovate")
    result = await broker.get_order_status(account, "555")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_working_reports_nothing_new(monkeypatch):
    _set_env(monkeypatch)
    broker = TradovateBroker()

    async def fake_post(self, url, json=None, headers=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"accessToken": "tok-1", "expirationTime": "2099-01-01T00:00:00Z"}, request=request)

    async def fake_get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"ordStatus": "Working"}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradovate")
    result = await broker.get_order_status(account, "555")

    assert result is None
    await broker.close()
