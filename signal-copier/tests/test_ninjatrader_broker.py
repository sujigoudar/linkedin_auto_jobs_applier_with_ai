import httpx
import pytest

from app.brokers.ninjatrader import NinjaTraderBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


@pytest.mark.asyncio
async def test_missing_url_reports_error(monkeypatch):
    monkeypatch.delenv("NT8_ACCT1_URL", raising=False)
    broker = NinjaTraderBroker()
    account = DestinationAccount(account_id="acct1", broker="ninjatrader")

    result = await broker.place_order(
        Signal(source="test", symbol="ES", side=Side.BUY), account, quantity=1.0, symbol="ES"
    )

    assert result.status == OrderStatus.ERROR
    assert "NT8_ACCT1_URL" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_buy_forwards_correct_payload(monkeypatch):
    monkeypatch.setenv("NT8_ACCT1_URL", "http://127.0.0.1:7091/webhook/")
    broker = NinjaTraderBroker()

    captured = {}

    async def fake_post(self, url, json):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, request=httpx.Request("POST", url))

    broker._client.post = fake_post.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="ninjatrader")
    result = await broker.place_order(
        Signal(source="test", symbol="ES", side=Side.BUY, price=5300.0), account, quantity=2.0, symbol="ES"
    )

    assert result.status == OrderStatus.PENDING
    assert captured["url"] == "http://127.0.0.1:7091/webhook/"
    assert captured["json"]["action"] == "buy"
    assert captured["json"]["sentiment"] == "long"
    assert captured["json"]["quantity"] == 2.0
    assert captured["json"]["price"] == 5300.0
    await broker.close()


@pytest.mark.asyncio
async def test_close_side_is_rejected(monkeypatch):
    monkeypatch.setenv("NT8_ACCT1_URL", "http://127.0.0.1:7091/webhook/")
    broker = NinjaTraderBroker()
    account = DestinationAccount(account_id="acct1", broker="ninjatrader")

    result = await broker.place_order(
        Signal(source="test", symbol="ES", side=Side.CLOSE), account, quantity=1.0, symbol="ES"
    )

    assert result.status == OrderStatus.REJECTED
    await broker.close()


@pytest.mark.asyncio
async def test_http_error_reports_error(monkeypatch):
    monkeypatch.setenv("NT8_ACCT1_URL", "http://127.0.0.1:7091/webhook/")
    broker = NinjaTraderBroker()

    async def fake_post(self, url, json):
        raise httpx.ConnectError("connection refused")

    broker._client.post = fake_post.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="ninjatrader")
    result = await broker.place_order(
        Signal(source="test", symbol="ES", side=Side.SELL), account, quantity=1.0, symbol="ES"
    )

    assert result.status == OrderStatus.ERROR
    assert "connection refused" in result.message
    await broker.close()
