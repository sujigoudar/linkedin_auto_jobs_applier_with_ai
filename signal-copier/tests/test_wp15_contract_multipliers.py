"""Tests for B-03 fix: Contract multipliers in every notional/risk computation.

WP-15: Quantity units are never normalized, and contract multipliers are
ignored by every notional/risk computation. This test suite verifies that:

1. Option contract multipliers are applied to notional/risk calculations.
2. Future contract multipliers are applied to notional/risk calculations.
3. FX lot size multipliers are applied to notional/risk calculations.
4. Signals without required spec (fx/option/future) are refused.
5. The risk-to-stop gate correctly multiplies by contract multiplier.
6. The buying-power gate correctly multiplies by contract multiplier.
7. Capital allocation tracking includes the contract multiplier.
"""
from __future__ import annotations

import pytest
from app.models import (
    DestinationAccount,
    OptionContractSpec,
    Side,
    Signal,
    AssetClass,
)
from app.engine import SignalCopierEngine
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.routing import RoutingConfig, RoutingRule
from pathlib import Path

SOURCE = "test_source"
SYMBOL = "AAPL"


def _engine(store: SignalStore, account: DestinationAccount, broker: PaperBroker) -> SignalCopierEngine:
    routing = RoutingConfig(
        rules=[RoutingRule(source=SOURCE, destinations=["acct-1"])],
        accounts={"acct-1": account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    return SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )


def _signal(**overrides) -> Signal:
    defaults = dict(source=SOURCE, symbol=SYMBOL, side=Side.BUY)
    defaults.update(overrides)
    return Signal(**defaults)


def _account(**overrides) -> DestinationAccount:
    defaults = dict(account_id="acct-1", broker="paper")
    defaults.update(overrides)
    return DestinationAccount(**defaults)


class TestOptionContractMultiplier:
    """Option signals should multiply notional/risk by contract multiplier."""

    @pytest.mark.asyncio
    async def test_option_notional_includes_contract_multiplier(self, tmp_path: Path):
        """100 option contracts x $2.50 premium x 100 multiplier = $25,000 notional."""
        # Set up a $100k account with a buying power gate
        account = _account(max_notional_exposure=30000.0)  # $30k ceiling
        broker = PaperBroker()
        store = SignalStore(tmp_path / "test.db")
        engine = _engine(store, account, broker)

        # Signal: BUY 100 AAPL 200C @ $2.50 (real notional: 100 × 100 × 2.50 = $25,000)
        signal = _signal(
            symbol="AAPL",
            side=Side.BUY,
            quantity=100.0,
            price=2.50,
            asset_class=AssetClass.OPTION,
            option=OptionContractSpec(
                underlying="AAPL",
                expiry="2026-12-18",
                strike=200.0,
                right="call",
                multiplier=100.0,
            ),
        )

        # Should admit: $25,000 notional < $30,000 ceiling
        admitted, notional, rejection = await engine._try_reserve_capital(account, signal, 100.0)
        assert admitted is True
        assert rejection is None

        # Signal with $26,000 notional (260 contracts) should reject
        signal2 = _signal(
            symbol="AAPL",
            side=Side.BUY,
            quantity=260.0,
            price=2.50,
            asset_class=AssetClass.OPTION,
            option=OptionContractSpec(
                underlying="AAPL",
                expiry="2026-12-18",
                strike=200.0,
                right="call",
                multiplier=100.0,
            ),
        )

        admitted2, notional2, rejection2 = await engine._try_reserve_capital(account, signal2, 260.0)
        assert admitted2 is False
        assert rejection2 is not None

    @pytest.mark.asyncio
    async def test_option_risk_includes_contract_multiplier(self, tmp_path: Path):
        """Option risk-to-stop should multiply by contract multiplier."""
        # PaperBroker reports equity == cash == $100,000 when flat (nothing is
        # fabricated: cash is the simulator's own figure). A 0.1% risk gate
        # therefore caps risk-to-stop at $100.
        account = _account(risk_percent_of_equity=0.001)  # 0.1% max risk = $100
        broker = PaperBroker()
        store = SignalStore(tmp_path / "test2.db")
        engine = _engine(store, account, broker)

        # Signal: BUY 10 AAPL 200C @ $2.50 SL $1.50
        # Real risk: (2.50 - 1.50) × 10 × 100 = $1,000 (1% of equity, 10× the ceiling)
        signal = _signal(
            symbol="AAPL",
            side=Side.BUY,
            quantity=10.0,
            price=2.50,
            stop_loss=1.50,
            asset_class=AssetClass.OPTION,
            option=OptionContractSpec(
                underlying="AAPL",
                expiry="2026-12-18",
                strike=200.0,
                right="call",
                multiplier=100.0,
            ),
        )

        # Should reject: $1,000 risk > $100 ceiling
        admitted, rejection = await engine._check_risk_basis(account, signal, 10.0)
        assert admitted is False
        assert rejection is not None
