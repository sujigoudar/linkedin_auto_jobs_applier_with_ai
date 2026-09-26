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


@pytest.mark.asyncio
async def test_get_order_status_partially_filled_reports_pending_with_progress(monkeypatch):
    """A still-open, partially-filled order must surface its real fill
    progress (so a managed-lifecycle entry can be protected for what's
    actually confirmed owned so far -- see
    PositionLifecycleManager.resolve_pending_entry) rather than being
    discarded the same as a fully-unfilled "nothing new" order."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"status": "partially_filled", "filled_qty": "30", "filled_avg_price": "101.5"},
            request=request,
        )

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.PENDING  # still open, not terminal
    assert result.filled_quantity == 30.0
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_new_unfilled_order_reports_nothing_new(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"status": "new", "filled_qty": "0"}, request=request)

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    result = await broker.get_order_status(account, "order-1")

    assert result is None
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_filled_reports_terminal(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200, json={"status": "filled", "filled_qty": "100", "filled_avg_price": "102.0"}, request=request
        )

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 100.0
    await broker.close()


@pytest.mark.asyncio
async def test_get_order_status_canceled_with_partial_fill_reports_it(monkeypatch):
    """A canceled order can still carry a real partial fill from before the
    cancellation -- app/reconciliation.py's _correct_position needs this to
    avoid wiping a confirmed partial fill to zero."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200, json={"status": "canceled", "filled_qty": "30", "filled_avg_price": "101.0"}, request=request
        )

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    assert result.filled_quantity == 30.0
    await broker.close()
