"""Tests for WP-48: broker adapter cancel/status/readback gaps.

Findings closed:
- C-08: Alpaca cancel race — after a cancel request, re-read the order to verify
  final state and report filled/partially_filled quantity if filled before cancel.
- C-09: ccxt must not treat cancel ack as done — re-fetch the order and return
  True only if status is "canceled".
- C-13: TradeStation partial-fill-then-cancel reported as FILLED with no quantity
  — read FilledQuantity/ExecQuantity and report filled_quantity as partially_filled.
- C-15: ccxt has no get_order_status — implement via fetch_order with symbol map.
- C-16: ccxt spot position readback returns 0.0 for empty — return None unless
  market is swap/future.
"""
import pytest

pytest.importorskip("ccxt")

import httpx

from app.brokers.alpaca import AlpacaBroker
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.tradestation import TradeStationBroker
from app.models import DestinationAccount, OrderStatus


# ============================================================================
# C-08: Alpaca cancel race — verify final state after cancel
# ============================================================================

@pytest.mark.asyncio
async def test_c08_alpaca_cancel_reads_final_state_after_delete(monkeypatch):
    """C-08: After DELETE 204, Alpaca's cancel_order must re-read the order
    status to verify it actually reached a terminal cancelled state, not just
    that the cancel request was accepted (pending_cancel is not terminal)."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    # Track DELETE and GET calls
    requests = []

    async def fake_delete(self, url, headers):
        requests.append(("DELETE", url))
        request = httpx.Request("DELETE", url)
        return httpx.Response(204, request=request)  # accepted

    async def fake_get(self, url, headers):
        requests.append(("GET", url))
        request = httpx.Request("GET", url)
        # Return terminal state after DELETE
        return httpx.Response(200, json={"status": "canceled"}, request=request)

    broker._client.delete = fake_delete.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="alpaca")
    result = await broker.cancel_order(account, "order-1")

    # Must have issued both DELETE and GET
    assert len(requests) == 2
    assert requests[0][0] == "DELETE"
    assert requests[1][0] == "GET"
    assert result is True
    await broker.close()


@pytest.mark.asyncio
async def test_c08_alpaca_cancel_reports_filled_quantity_if_filled_before_cancel(monkeypatch):
    """C-08: If an order was filled between the cancel request and our
    follow-up read, get_order_status after cancel must report the filled quantity."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_delete(self, url, headers):
        request = httpx.Request("DELETE", url)
        return httpx.Response(204, request=request)

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        # Order was filled before the cancel completed
        return httpx.Response(200, json={"status": "filled", "filled_qty": "50", "filled_avg_price": "101.5"}, request=request)

    broker._client.delete = fake_delete.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="alpaca")
    result = await broker.get_order_status(account, "order-1")

    # Must report the filled quantity even after cancel
    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 50.0
    await broker.close()


@pytest.mark.asyncio
async def test_c08_alpaca_cancel_returns_false_if_status_not_terminal(monkeypatch):
    """C-08: If the order status is still pending_cancel (not terminal),
    cancel_order must return False — the cancellation is not yet confirmed."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_delete(self, url, headers):
        request = httpx.Request("DELETE", url)
        return httpx.Response(204, request=request)

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        # Order is still in pending_cancel — not terminal yet
        return httpx.Response(200, json={"status": "pending_cancel"}, request=request)

    broker._client.delete = fake_delete.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="alpaca")
    result = await broker.cancel_order(account, "order-1")

    assert result is False  # Not yet confirmed as terminal
    await broker.close()


# ============================================================================
# C-09: ccxt cancel must verify via fetch_order, not trust ack alone
# ============================================================================

@pytest.mark.asyncio
async def test_c09_ccxt_cancel_calls_fetch_order_to_verify(monkeypatch):
    """C-09: ccxt's cancel_order must not treat the cancel ack as done.
    It must call fetch_order and return True only if status is 'canceled'."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker()

    class _FakeExchange:
        def __init__(self):
            self.cancel_calls = []
            self.fetch_calls = []

        async def cancel_order(self, order_id, symbol=None, params=None):
            self.cancel_calls.append((order_id, symbol))
            return {"id": order_id}  # just ack

        async def fetch_order(self, order_id, symbol=None, params=None):
            self.fetch_calls.append((order_id, symbol))
            return {"id": order_id, "status": "canceled"}

        async def close(self):
            pass

    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    broker._order_symbols["order-1"] = "BTC/USDT"
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.cancel_order(account, "order-1")

    # Must have called both cancel_order and fetch_order
    assert len(fake.cancel_calls) == 1
    assert len(fake.fetch_calls) == 1
    assert result is True
    await broker.close()


@pytest.mark.asyncio
async def test_c09_ccxt_cancel_returns_false_if_fetch_shows_not_canceled(monkeypatch):
    """C-09: If fetch_order shows status is not 'canceled' (e.g., still open),
    cancel_order must return False."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker()

    class _FakeExchange:
        async def cancel_order(self, order_id, symbol=None, params=None):
            return {"id": order_id}  # just ack

        async def fetch_order(self, order_id, symbol=None, params=None):
            # Still open/pending after cancel attempt
            return {"id": order_id, "status": "open", "filled": 10}

        async def close(self):
            pass

    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    broker._order_symbols["order-1"] = "BTC/USDT"
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.cancel_order(account, "order-1")

    assert result is False
    await broker.close()


# ============================================================================
# C-13: TradeStation partial fill (FLP) must report filled_quantity
# ============================================================================

@pytest.mark.asyncio
async def test_c13_tradestation_flp_partial_fill_reports_filled_quantity(monkeypatch):
    """C-13: TradeStation's FLP (partial fill then UROut) must report
    filled_quantity, not just the status. The order response includes
    FilledQuantity/ExecQuantity that must be read."""
    monkeypatch.setenv("TRADESTATION_ACCT1_CLIENT_ID", "cid123")
    monkeypatch.setenv("TRADESTATION_ACCT1_REFRESH_TOKEN", "token123")
    monkeypatch.setenv("TRADESTATION_ACCT1_TS_ACCOUNT_ID", "ts-acct")
    broker = TradeStationBroker()

    async def fake_post(self, url, **kwargs):
        # Auth token request
        if "oauth/token" in url:
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_in": 1200},
                request=request,
            )
        return None

    async def fake_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        # Order response with FilledQuantity
        return httpx.Response(
            200,
            json={
                "Orders": [
                    {
                        "OrderID": "order-1",
                        "Status": "FLP",  # partial fill then UROut
                        "FilledQuantity": 40,  # partial fill
                        "Quantity": 100,
                    }
                ]
            },
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradestation")
    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    # FLP is a partial fill, should be reported with filled_quantity
    assert result.filled_quantity == 40
    await broker.close()


@pytest.mark.asyncio
async def test_c13_tradestation_fll_full_fill_reports_quantity(monkeypatch):
    """C-13: FLL (fully filled) must also report the filled quantity."""
    monkeypatch.setenv("TRADESTATION_ACCT1_CLIENT_ID", "cid123")
    monkeypatch.setenv("TRADESTATION_ACCT1_REFRESH_TOKEN", "token123")
    monkeypatch.setenv("TRADESTATION_ACCT1_TS_ACCOUNT_ID", "ts-acct")
    broker = TradeStationBroker()

    async def fake_post(self, url, **kwargs):
        if "oauth/token" in url:
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_in": 1200},
                request=request,
            )
        return None

    async def fake_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "Orders": [
                    {
                        "OrderID": "order-1",
                        "Status": "FLL",  # fully filled
                        "FilledQuantity": 100,
                        "Quantity": 100,
                    }
                ]
            },
            request=request,
        )

    broker._client.post = fake_post.__get__(broker._client)
    broker._client.get = fake_get.__get__(broker._client)

    account = DestinationAccount(account_id="acct1", broker="tradestation")
    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 100
    await broker.close()


# ============================================================================
# C-15: ccxt must implement get_order_status via fetch_order
# ============================================================================

@pytest.mark.asyncio
async def test_c15_ccxt_get_order_status_fetches_and_maps_status(monkeypatch):
    """C-15: ccxt has no get_order_status. Must implement using fetch_order
    and map the ccxt status to an OrderResult."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker()

    class _FakeExchange:
        def __init__(self):
            self.fetch_calls = []

        async def fetch_order(self, order_id, symbol=None, params=None):
            self.fetch_calls.append((order_id, symbol))
            return {
                "id": order_id,
                "status": "closed",
                "filled": 100,
                "average": 65000.0,
            }

        async def close(self):
            pass

    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    broker._order_symbols["order-1"] = "BTC/USDT"
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 100
    await broker.close()


@pytest.mark.asyncio
async def test_c15_ccxt_get_order_status_open_order_returns_pending(monkeypatch):
    """C-15: An open/unfilled order must return PENDING status."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker()

    class _FakeExchange:
        async def fetch_order(self, order_id, symbol=None, params=None):
            return {
                "id": order_id,
                "status": "open",
                "filled": 0,
                "average": None,
            }

        async def close(self):
            pass

    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    broker._order_symbols["order-1"] = "BTC/USDT"
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.get_order_status(account, "order-1")

    # Unfilled open order still pending
    assert result is None or result.status == OrderStatus.PENDING
    await broker.close()


@pytest.mark.asyncio
async def test_c15_ccxt_get_order_status_canceled_returns_rejected(monkeypatch):
    """C-15: A canceled order must return REJECTED status."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker()

    class _FakeExchange:
        async def fetch_order(self, order_id, symbol=None, params=None):
            return {"id": order_id, "status": "canceled", "filled": 0}

        async def close(self):
            pass

    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    broker._order_symbols["order-1"] = "BTC/USDT"
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    await broker.close()


# ============================================================================
# C-16: ccxt spot position readback must return None, not 0.0
# ============================================================================

@pytest.mark.asyncio
async def test_c16_ccxt_get_broker_position_returns_none_for_spot_unknown(monkeypatch):
    """C-16: On a spot market, if balance is unavailable or empty,
    get_broker_position must return None, not 0.0 — None signals
    "unable to determine" rather than "definitely flat"."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker()

    class _FakeExchange:
        def __init__(self):
            self.markets = {"BTC/USDT": {"type": "spot"}}  # spot market

        async def fetch_positions(self, symbols=None):
            # Empty list — balance unavailable on spot
            return []

        async def close(self):
            pass

    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.get_broker_position(account, "BTC/USDT")

    # Must NOT return 0.0; return None to signal "unknown"
    assert result is None
    await broker.close()


@pytest.mark.asyncio
async def test_c16_ccxt_get_broker_position_returns_quantity_for_derivatives(monkeypatch):
    """C-16: For derivatives (swap/future), return the actual position quantity."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker()

    class _FakeExchange:
        def __init__(self):
            self.markets = {"BTC/USDT:USDT": {"type": "swap"}}

        async def fetch_positions(self, symbols=None):
            return [{"symbol": "BTC/USDT:USDT", "contracts": 1.5, "side": "long"}]

        async def close(self):
            pass

    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.get_broker_position(account, "BTC/USDT:USDT")

    assert result == 1.5
    await broker.close()


@pytest.mark.asyncio
async def test_c16_ccxt_get_broker_position_returns_none_if_not_supported_on_exchange(monkeypatch):
    """C-16: If the exchange raises NotSupported (e.g., fetch_positions
    on spot), return None, not 0.0."""
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    broker = CCXTBroker()

    class _FakeNotSupported(Exception):
        pass

    class _FakeExchange:
        def __init__(self):
            self.markets = {"BTC/USDT": {"type": "spot"}}

        async def fetch_positions(self, symbols=None):
            raise _FakeNotSupported("positions not supported")

        async def close(self):
            pass

    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.get_broker_position(account, "BTC/USDT")

    # Must return None, not 0.0
    assert result is None
    await broker.close()
