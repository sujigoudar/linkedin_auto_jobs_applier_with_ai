"""ADP-01: a market order type is not a guarantee of a synchronous fill.
place_order used to unconditionally label every ccxt response FILLED, and
`order.get("filled") or quantity` silently turned a genuine zero fill into
"filled at the full requested quantity" (`0 or quantity` == `quantity` in
Python). These reproduce the exact controlled open/canceled/rejected/
zero-fill responses the audit describes and confirm they're now honored."""
import pytest

pytest.importorskip("ccxt")

from app.brokers.ccxt_broker import CCXTBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    return CCXTBroker()


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="ccxt")


class _ScriptedExchange:
    def __init__(self, response):
        self._response = response

    async def create_order(self, **kwargs):
        return self._response

    async def close(self):
        pass


async def _place(broker, account, response, quantity=1.0):
    broker._exchanges["acct1"] = _ScriptedExchange(response)
    return await broker.place_order(
        Signal(source="test", symbol="BTCUSDT", side=Side.BUY), account, quantity=quantity, symbol="BTC/USDT"
    )


@pytest.mark.asyncio
async def test_open_order_is_not_reported_filled(broker, account):
    result = await _place(broker, account, {"id": "o1", "status": "open", "filled": 0.0, "amount": 1.0})
    assert result.status == OrderStatus.PENDING
    assert result.filled_quantity == 0.0


@pytest.mark.asyncio
async def test_canceled_order_is_not_reported_filled(broker, account):
    result = await _place(broker, account, {"id": "o1", "status": "canceled", "filled": 0.0, "amount": 1.0})
    assert result.status == OrderStatus.REJECTED
    assert result.filled_quantity == 0.0


@pytest.mark.asyncio
async def test_rejected_order_is_not_reported_filled(broker, account):
    result = await _place(broker, account, {"id": "o1", "status": "rejected", "filled": 0.0, "amount": 1.0})
    assert result.status == OrderStatus.REJECTED
    assert result.filled_quantity == 0.0


@pytest.mark.asyncio
async def test_closed_order_is_reported_filled_with_its_real_quantity(broker, account):
    result = await _place(broker, account, {"id": "o1", "status": "closed", "filled": 1.0, "amount": 1.0})
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 1.0


@pytest.mark.asyncio
async def test_explicit_zero_fill_is_not_coerced_to_the_requested_quantity(broker, account):
    """The core `0 or quantity` bug: a real, explicit zero must stay zero,
    never fall back to whatever was requested."""
    result = await _place(
        broker, account, {"id": "o1", "status": "open", "filled": 0.0, "amount": 5.0}, quantity=5.0
    )
    assert result.filled_quantity == 0.0
    assert result.status != OrderStatus.FILLED
