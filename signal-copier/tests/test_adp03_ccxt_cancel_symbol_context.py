"""ADP-03: CCXTBroker.cancel_order called ccxt's unified cancel_order(id)
with NO symbol at all, even though ccxt's own signature is
cancel_order(id, symbol=None, params={}) precisely because many exchanges
REQUIRE the symbol to resolve which market the order belongs to. Calling
cancel_order without it fails outright on any such exchange.

Reproduces the audit's exact case
(test_adapter_research_audit::test_ccxt_cancel_contract_can_supply_required_symbol).
"""
import pytest

from app.brokers.ccxt_broker import CCXTBroker
from app.models import AssetClass, DestinationAccount, Side, Signal


class _SymbolRequiringExchange:
    """A stand-in for an exchange whose cancel_order raises if no symbol is
    supplied -- exactly the audit's own reproduction."""

    def __init__(self):
        self.cancel_calls = []

    async def create_order(self, symbol, type, side, amount, params):
        return {"id": "order-1", "status": "open", "filled": 0, "amount": amount}

    async def cancel_order(self, order_id, symbol=None):
        self.cancel_calls.append((order_id, symbol))
        if not symbol:
            raise ValueError("exchange requires symbol")
        return {"status": "canceled"}


@pytest.fixture
def account():
    return DestinationAccount(account_id="a", broker="ccxt")


@pytest.fixture
def broker():
    return CCXTBroker()


@pytest.mark.asyncio
async def test_audits_exact_case_cancel_supplies_the_required_symbol(broker, account):
    exchange = _SymbolRequiringExchange()
    broker._exchanges["a"] = exchange

    place_result = await broker.place_order(
        Signal(source="s", symbol="BTC/USDT", side=Side.BUY, asset_class=AssetClass.CRYPTO), account, 1.0, "BTC/USDT"
    )

    assert await broker.cancel_order(account, place_result.broker_order_id) is True
    assert exchange.cancel_calls == [(place_result.broker_order_id, "BTC/USDT")]


@pytest.mark.asyncio
async def test_protective_stop_order_id_also_remembers_its_symbol(broker, account):
    from app.models import Side as ExitSide

    exchange = _SymbolRequiringExchange()
    broker._exchanges["a"] = exchange

    stop_result = await broker.place_protective_stop(account, "BTC/USDT", 1.0, 60000.0, ExitSide.SELL)

    assert await broker.cancel_order(account, stop_result.broker_order_id) is True
    assert exchange.cancel_calls == [(stop_result.broker_order_id, "BTC/USDT")]


@pytest.mark.asyncio
async def test_cancel_of_an_untracked_order_id_falls_back_to_no_symbol(broker, account):
    """An order id this broker never placed (e.g. from before a restart --
    the in-memory cache is honestly documented as not surviving one) still
    attempts the old (unqualified) call shape rather than guessing a
    symbol; the exchange's own refusal is reported as an unconfirmed
    cancel (False), not an unhandled exception."""
    exchange = _SymbolRequiringExchange()
    broker._exchanges["a"] = exchange

    assert await broker.cancel_order(account, "unknown-order-id") is False
    assert exchange.cancel_calls == [("unknown-order-id", None)]
