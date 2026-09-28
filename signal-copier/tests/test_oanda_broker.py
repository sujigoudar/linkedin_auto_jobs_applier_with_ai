import httpx
import pytest

from app.brokers.oanda import OANDABroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


def _set_env(monkeypatch):
    monkeypatch.setenv("OANDA_ACCT1_TOKEN", "tok-1")
    monkeypatch.setenv("OANDA_ACCT1_ACCOUNT_ID", "101-004-1435156-001")


@pytest.mark.asyncio
async def test_missing_credentials_reports_error(monkeypatch):
    monkeypatch.delenv("OANDA_ACCT1_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCT1_ACCOUNT_ID", raising=False)
    broker = OANDABroker()
    account = DestinationAccount(account_id="acct1", broker="oanda")

    result = await broker.place_order(
        Signal(source="test", symbol="EUR_USD", side=Side.BUY), account, quantity=1000.0, symbol="EUR_USD"
    )

    assert result.status == OrderStatus.ERROR
    assert "OANDA_ACCT1_TOKEN" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_buy_sends_positive_units_sell_sends_negative(monkeypatch):
    """OANDA's own real order model uses one signed `units` field --
    positive = buy/long, negative = sell/short -- confirmed via
    oandapyV20's own documented MarketOrderRequest. Never a separate
    side flag."""
    _set_env(monkeypatch)
    broker = OANDABroker()
    captured = {}

    async def fake_post(self, url, headers=None, json=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        request = httpx.Request("POST", url)
        return httpx.Response(
            201,
            json={
                "orderFillTransaction": {"orderID": "2503", "units": json["order"]["units"], "price": "1.08463"},
                "orderCreateTransaction": {"id": "2503"},
                "lastTransactionID": "2504",
            },
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="oanda")

    buy_result = await broker.place_order(
        Signal(source="test", symbol="EUR_USD", side=Side.BUY), account, quantity=1000.0, symbol="EUR_USD"
    )
    assert captured["json"]["order"]["units"] == "1000.0"
    assert buy_result.status == OrderStatus.FILLED

    sell_result = await broker.place_order(
        Signal(source="test", symbol="EUR_USD", side=Side.SELL), account, quantity=1000.0, symbol="EUR_USD"
    )
    assert captured["json"]["order"]["units"] == "-1000.0"
    assert sell_result.status == OrderStatus.FILLED

    assert captured["url"] == "https://api-fxpractice.oanda.com/v3/accounts/101-004-1435156-001/orders"
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    await broker.close()


@pytest.mark.asyncio
async def test_immediate_fill_reports_filled_directly_not_pending(monkeypatch):
    """A MARKET order (FOK) either fills or is rejected in the SAME
    response -- confirmed via oandapyV20's own documented example
    response, which shows orderFillTransaction alongside
    orderCreateTransaction in one response body. place_order must report
    FILLED directly here, not PENDING waiting for a later poll."""
    _set_env(monkeypatch)
    broker = OANDABroker()

    async def fake_post(self, url, headers=None, json=None):
        request = httpx.Request("POST", url)
        return httpx.Response(
            201,
            json={
                "orderFillTransaction": {
                    "orderID": "2503", "units": "10000", "price": "1.08463", "id": "2504",
                },
                "orderCreateTransaction": {"id": "2503"},
                "lastTransactionID": "2504",
            },
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="oanda")

    result = await broker.place_order(
        Signal(source="test", symbol="EUR_USD", side=Side.BUY), account, quantity=10000.0, symbol="EUR_USD"
    )

    assert result.status == OrderStatus.FILLED
    assert result.broker_order_id == "2503"
    assert result.filled_quantity == 10000.0
    assert result.filled_price == 1.08463
    await broker.close()


@pytest.mark.asyncio
async def test_rejected_order_reports_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = OANDABroker()

    async def fake_post(self, url, headers=None, json=None):
        request = httpx.Request("POST", url)
        return httpx.Response(
            201,
            json={
                "orderCancelTransaction": {"reason": "FIFO_VIOLATION_SAFEGUARD_VIOLATION"},
                "lastTransactionID": "2504",
            },
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="oanda")

    result = await broker.place_order(
        Signal(source="test", symbol="EUR_USD", side=Side.BUY), account, quantity=1000.0, symbol="EUR_USD"
    )

    assert result.status == OrderStatus.REJECTED
    assert "FIFO_VIOLATION_SAFEGUARD_VIOLATION" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_close_side_is_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = OANDABroker()
    account = DestinationAccount(account_id="acct1", broker="oanda")

    result = await broker.place_order(
        Signal(source="test", symbol="EUR_USD", side=Side.CLOSE), account, quantity=1000.0, symbol="EUR_USD"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_live_env_uses_live_url(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setenv("OANDA_ACCT1_ENV", "live")
    broker = OANDABroker()
    captured = {}

    async def fake_post(self, url, headers=None, json=None):
        captured["url"] = url
        request = httpx.Request("POST", url)
        return httpx.Response(201, json={"orderCreateTransaction": {"id": "1"}}, request=request)

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="oanda")

    await broker.place_order(
        Signal(source="test", symbol="EUR_USD", side=Side.BUY), account, quantity=1000.0, symbol="EUR_USD"
    )

    assert captured["url"].startswith("https://api-fxtrade.oanda.com")
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_filled_reports_terminal(monkeypatch):
    _set_env(monkeypatch)
    broker = OANDABroker()

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"order": {"state": "FILLED"}}, request=request)

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="oanda")

    result = await broker.get_order_status(account, "2503")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_cancelled_reports_rejected(monkeypatch):
    _set_env(monkeypatch)
    broker = OANDABroker()

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"order": {"state": "CANCELLED"}}, request=request)

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="oanda")

    result = await broker.get_order_status(account, "2503")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_pending_reports_nothing_new(monkeypatch):
    _set_env(monkeypatch)
    broker = OANDABroker()

    async def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"order": {"state": "PENDING"}}, request=request)

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="oanda")

    result = await broker.get_order_status(account, "2503")

    assert result is None
    await broker.close()
