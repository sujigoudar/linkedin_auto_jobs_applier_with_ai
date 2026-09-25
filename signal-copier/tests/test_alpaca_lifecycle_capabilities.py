import httpx
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.models import DestinationAccount, OrderStatus, Side


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    return DestinationAccount(account_id="acct1", broker="alpaca")


@pytest.fixture
def broker():
    return AlpacaBroker()


def _mock_response(status_code, json_body=None):
    request = httpx.Request("GET", "https://paper-api.alpaca.markets/v2/x")
    return httpx.Response(status_code, json=json_body or {}, request=request)


@pytest.mark.asyncio
async def test_place_protective_stop_for_long_position_sells(broker, account, monkeypatch):
    captured = {}

    async def fake_post(self, url, headers, json):
        captured["url"] = url
        captured["json"] = json
        return _mock_response(200, {"id": "stop-order-1", "status": "new"})

    monkeypatch.setattr(broker._client, "post", fake_post.__get__(broker._client))

    result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id == "stop-order-1"
    assert captured["json"]["side"] == "sell"  # opposite of a long position
    assert captured["json"]["type"] == "stop"
    assert captured["json"]["stop_price"] == "48.5"


@pytest.mark.asyncio
async def test_place_protective_stop_for_short_position_buys(broker, account, monkeypatch):
    captured = {}

    async def fake_post(self, url, headers, json):
        captured["json"] = json
        return _mock_response(200, {"id": "stop-order-2", "status": "new"})

    monkeypatch.setattr(broker._client, "post", fake_post.__get__(broker._client))

    await broker.place_protective_stop(account, "AAPL", quantity=30.0, stop_price=52.0, exit_side=Side.BUY)

    assert captured["json"]["side"] == "buy"


@pytest.mark.asyncio
async def test_cancel_order_success_on_204(broker, account, monkeypatch):
    async def fake_delete(self, url, headers):
        return _mock_response(204)

    monkeypatch.setattr(broker._client, "delete", fake_delete.__get__(broker._client))

    assert await broker.cancel_order(account, "stop-order-1") is True


@pytest.mark.asyncio
async def test_cancel_order_fails_on_404_already_gone(broker, account, monkeypatch):
    async def fake_delete(self, url, headers):
        return _mock_response(404)

    monkeypatch.setattr(broker._client, "delete", fake_delete.__get__(broker._client))

    assert await broker.cancel_order(account, "stop-order-1") is False


@pytest.mark.asyncio
async def test_replace_returns_the_new_order_id(broker, account, monkeypatch):
    async def fake_patch(self, url, headers, json):
        # Alpaca's real behavior: replace creates a brand new order id
        return _mock_response(200, {"id": "new-replaced-order-id", "status": "new"})

    monkeypatch.setattr(broker._client, "patch", fake_patch.__get__(broker._client))

    result = await broker.replace_stop_quantity(account, "old-order-id", new_quantity=47.0, new_price=51.50)

    assert result.broker_order_id == "new-replaced-order-id"
    assert result.status == OrderStatus.PENDING


@pytest.mark.asyncio
async def test_replace_returns_none_on_failure(broker, account, monkeypatch):
    async def fake_patch(self, url, headers, json):
        return _mock_response(422)

    monkeypatch.setattr(broker._client, "patch", fake_patch.__get__(broker._client))

    result = await broker.replace_stop_quantity(account, "old-order-id", new_quantity=47.0)

    assert result is None


@pytest.mark.asyncio
async def test_get_broker_position_returns_quantity(broker, account, monkeypatch):
    async def fake_get(self, url, headers):
        return _mock_response(200, {"qty": "62"})

    monkeypatch.setattr(broker._client, "get", fake_get.__get__(broker._client))

    assert await broker.get_broker_position(account, "AAPL") == 62.0


@pytest.mark.asyncio
async def test_get_broker_position_404_means_flat_not_none(broker, account, monkeypatch):
    async def fake_get(self, url, headers):
        return _mock_response(404)

    monkeypatch.setattr(broker._client, "get", fake_get.__get__(broker._client))

    assert await broker.get_broker_position(account, "AAPL") == 0.0
