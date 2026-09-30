import httpx
import pytest

from app.brokers.signalstack import SignalStackBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


@pytest.mark.asyncio
async def test_missing_webhook_url_reports_error(monkeypatch):
    monkeypatch.delenv("SIGNALSTACK_ACCT1_WEBHOOK_URL", raising=False)
    broker = SignalStackBroker()
    account = DestinationAccount(account_id="acct1", broker="signalstack")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "SIGNALSTACK_ACCT1_WEBHOOK_URL" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_successful_forward_reports_pending(monkeypatch):
    monkeypatch.setenv("SIGNALSTACK_ACCT1_WEBHOOK_URL", "https://signalstack.example/hook/abc123")
    broker = SignalStackBroker()

    captured = {}

    async def fake_post(self, url, json):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, request=httpx.Request("POST", url))

    broker._client.post = fake_post.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="signalstack")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=2.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.PENDING
    assert captured["url"] == "https://signalstack.example/hook/abc123"
    assert captured["json"] == {"symbol": "AAPL", "action": "buy", "quantity": 2.0}
    await broker.close()


@pytest.mark.asyncio
async def test_http_error_reports_error(monkeypatch):
    monkeypatch.setenv("SIGNALSTACK_ACCT1_WEBHOOK_URL", "https://signalstack.example/hook/abc123")
    broker = SignalStackBroker()

    async def fake_post(self, url, json):
        raise httpx.ConnectError("connection refused")

    broker._client.post = fake_post.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="signalstack")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "ConnectError" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_http_status_error_does_not_leak_webhook_token(monkeypatch):
    # SEC-01 regression: the webhook URL embeds a secret token, and httpx's
    # own HTTPStatusError.__str__ formats the FULL request URL into the
    # message ("... for url 'https://.../hook/<token>'"). A rejected/erroring
    # webhook call must never let that token reach `result.message` -- it
    # gets stored in this service's own order rows and logs.
    secret_url = "https://signalstack.example/hook/super-secret-token-999"
    monkeypatch.setenv("SIGNALSTACK_ACCT1_WEBHOOK_URL", secret_url)
    broker = SignalStackBroker()

    async def fake_post(self, url, json):
        request = httpx.Request("POST", url)
        return httpx.Response(401, request=request)

    broker._client.post = fake_post.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="signalstack")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "super-secret-token-999" not in result.message
    assert "signalstack.example" not in result.message
    assert "401" in result.message
    await broker.close()
