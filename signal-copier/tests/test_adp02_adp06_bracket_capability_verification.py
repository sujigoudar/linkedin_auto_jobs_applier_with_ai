"""ADP-02: CCXTBroker sent stopLossPrice/takeProfitPrice (ccxt's UNIFIED
param names, syntactically accepted by every exchange class) with no
verification that the specific exchange actually honors them as a genuine
attached bracket.

ADP-06: `supports_native_bracket` alone counted as "can protect a managed
position," even though a managed-lifecycle entry deliberately strips
stop_loss/take_profit before ever calling place_order -- a bracket-only
broker has no way to protect a position through that path at all.

Reproduces the audit's exact cases:
test_adapter_research_audit::test_ccxt_attached_protection_not_unqualified_flat_trigger_parameters,
test_adapter_research_audit::test_ibkr_bracket_capability_does_not_claim_standalone_stop,
test_trading_audit::test_bracket_only_cannot_satisfy_standalone_managed_recipe.
"""
import pytest

from app.brokers.base import BrokerAdapter
from app.brokers.ibkr import IBKRBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import DestinationAccount, Side


def test_audits_exact_case_ibkr_bracket_capability_does_not_claim_standalone_stop():
    broker = IBKRBroker()
    assert not broker.can_protect_a_managed_position()


class _BracketOnlyBroker(BrokerAdapter):
    name = "bracket"
    supports_native_bracket = True

    async def place_order(self, *args):
        raise AssertionError("must block before submit")


def test_audits_exact_case_bracket_only_cannot_satisfy_standalone_managed_recipe():
    manager = PositionLifecycleManager(brokers={"bracket": _BracketOnlyBroker()})
    plan = PositionPlan(
        account_id="a", symbol="AAPL", side=Side.BUY, planned_quantity=10, broker="bracket", initial_stop=90
    )
    assert manager.validate_plan(plan) is not None


class _StandaloneStopOnlyBroker(BrokerAdapter):
    name = "standalone"
    supports_native_bracket = False

    async def place_protective_stop(self, account, symbol, quantity, stop_price, exit_side):
        return None  # unused here -- only capability introspection matters

    async def place_order(self, *args):
        raise AssertionError("not exercised in this test")


def test_a_broker_with_only_a_verified_standalone_stop_still_qualifies():
    """Confirms the fix didn't overcorrect: a broker with NO native
    bracket but a REAL standalone place_protective_stop override must
    still be admitted."""
    broker = _StandaloneStopOnlyBroker()
    assert broker.can_protect_a_managed_position() is True


@pytest.mark.asyncio
async def test_audits_exact_case_ccxt_rejects_unqualified_bracket_params():
    ccxt = pytest.importorskip("ccxt")
    from app.brokers.ccxt_broker import CCXTBroker
    from app.models import Signal

    broker = CCXTBroker()

    class _Exchange:
        def __init__(self):
            self.create_order = self._create_order
            self.calls = []

        async def _create_order(self, **kwargs):
            self.calls.append(kwargs)
            return {"id": "1", "status": "open", "filled": 0}

    exchange = _Exchange()
    broker._exchanges["a"] = exchange

    result = await broker.place_order(
        Signal(source="s", symbol="BTC/USDT", side=Side.BUY, stop_loss=90, take_profit=110),
        DestinationAccount("a", "ccxt"),
        10,
        "BTC/USDT",
    )

    if result.status.value in ("rejected", "error"):
        return
    assert "stopLoss" in exchange.calls[0]["params"]
    assert "takeProfit" in exchange.calls[0]["params"]
    assert "stopLossPrice" not in exchange.calls[0]["params"]
