"""WP-21: Definite rejection vs ambiguity in broker adapters.

Tests for B-05, C-11, C-12, C-20: HTTP 4xx/5xx and MT5 retcode classification.

- Definite 4xx validation rejections (400, 403, 422) -> REJECTED
- Ambiguous errors (5xx, 408, 429, timeouts, connection errors) -> ERROR
"""

import httpx
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


# --- Alpaca: C-20 ---


@pytest.mark.asyncio
async def test_alpaca_422_returns_rejected(monkeypatch):
    """C-20: Definite 4xx rejection (422 insufficient buying power) -> REJECTED."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_post(self, url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(
            422,
            text="Insufficient buying power",
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.REJECTED
    assert "422" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_alpaca_400_returns_rejected(monkeypatch):
    """C-20: Definite 4xx rejection (400 bad request) -> REJECTED."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_post(self, url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(
            400,
            text="Invalid symbol",
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    result = await broker.place_order(
        Signal(source="test", symbol="INVALID", side=Side.BUY), account, quantity=1.0, symbol="INVALID"
    )

    assert result.status == OrderStatus.REJECTED
    assert "400" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_alpaca_503_returns_error(monkeypatch):
    """C-20: Ambiguous error (5xx service unavailable) -> ERROR."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_post(self, url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(
            503,
            text="Service unavailable",
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "503" in result.message
    await broker.close()


@pytest.mark.asyncio
async def test_alpaca_408_returns_error(monkeypatch):
    """C-20: Ambiguous error (408 request timeout) -> ERROR."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_post(self, url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(
            408,
            text="Request timeout",
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "408" in result.message
    await broker.close()


# --- MT5: C-12 ---


def test_mt5_ambiguous_retcode_constants():
    """C-12: Verify MT5 ambiguous retcodes are properly classified."""
    from app.brokers.mt4_mt5 import _TRADE_RETCODE_AMBIGUOUS
    assert 10004 in _TRADE_RETCODE_AMBIGUOUS  # REQUOTE
    assert 10012 in _TRADE_RETCODE_AMBIGUOUS  # TIMEOUT
    assert 10031 in _TRADE_RETCODE_AMBIGUOUS  # CONNECTION
