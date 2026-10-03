"""Tests for WP-15b: FX unit sizes and capital allocator notional multiplication.

WP-15b extends WP-15 to:
1. Implement FX unit sizes: "standard_lot_100000"→100000, "mini_lot_10000"→10000,
   "micro_lot_1000"→1000, "units"→1
2. FX without spec defaults to "units" (1.0) and returns note "fx unit assumed: units"
3. capital_allocator multiplies notional by contract_multiplier in all queries
4. orders.contract_multiplier column stores the multiplier for each order (for later use
   in notional calculations when position is replayed from orders)
"""
from __future__ import annotations

import pytest
from pathlib import Path
from app.models import (
    DestinationAccount,
    OptionContractSpec,
    FxContractSpec,
    Side,
    Signal,
    AssetClass,
    OrderStatus,
)
from app.engine import SignalCopierEngine
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.routing import RoutingConfig, RoutingRule
from app.capital_allocator import confirmed_open_notional
from app.risk import contract_multiplier

SOURCE = "test_source"
SYMBOL_OPTION = "AAPL"
SYMBOL_FX = "EURUSD"


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
    defaults = dict(source=SOURCE, symbol=SYMBOL_OPTION, side=Side.BUY)
    defaults.update(overrides)
    return Signal(**defaults)


def _account(**overrides) -> DestinationAccount:
    defaults = dict(account_id="acct-1", broker="paper")
    defaults.update(overrides)
    return DestinationAccount(**defaults)


class TestFxUnitSizes:
    """FX unit size parsing and multiplier derivation."""

    def test_fx_micro_lot_multiplier(self):
        """FX micro lot (1000 units) → multiplier 1000."""
        signal = _signal(
            symbol=SYMBOL_FX,
            asset_class=AssetClass.FOREX,
            fx=FxContractSpec(base_currency="EUR", quote_currency="USD", unit="micro_lot_1000"),
        )
        mult, error, note = contract_multiplier(signal)
        assert mult == 1000.0
        assert error is None
        assert note is None

    def test_fx_mini_lot_multiplier(self):
        """FX mini lot (10000 units) → multiplier 10000."""
        signal = _signal(
            symbol=SYMBOL_FX,
            asset_class=AssetClass.FOREX,
            fx=FxContractSpec(base_currency="EUR", quote_currency="USD", unit="mini_lot_10000"),
        )
        mult, error, note = contract_multiplier(signal)
        assert mult == 10000.0
        assert error is None
        assert note is None

    def test_fx_standard_lot_multiplier(self):
        """FX standard lot (100000 units) → multiplier 100000."""
        signal = _signal(
            symbol=SYMBOL_FX,
            asset_class=AssetClass.FOREX,
            fx=FxContractSpec(base_currency="EUR", quote_currency="USD", unit="standard_lot_100000"),
        )
        mult, error, note = contract_multiplier(signal)
        assert mult == 100000.0
        assert error is None
        assert note is None

    def test_fx_units_multiplier(self):
        """FX units (1 unit) → multiplier 1."""
        signal = _signal(
            symbol=SYMBOL_FX,
            asset_class=AssetClass.FOREX,
            fx=FxContractSpec(base_currency="EUR", quote_currency="USD", unit="units"),
        )
        mult, error, note = contract_multiplier(signal)
        assert mult == 1.0
        assert error is None
        assert note is None

    def test_fx_no_spec_defaults_to_units(self):
        """FOREX without spec → multiplier 1.0 and note 'fx unit assumed: units'."""
        signal = _signal(
            symbol=SYMBOL_FX,
            asset_class=AssetClass.FOREX,
            fx=None,  # No spec
        )
        mult, error, note = contract_multiplier(signal)
        assert mult == 1.0
        assert error is None
        assert note == "fx unit assumed: units"


class TestCapitalAllocatorNotionalMultiplication:
    """Capital allocator applies contract_multiplier to notional calculations."""

    @pytest.mark.asyncio
    async def test_option_entry_notional_includes_multiplier(self, tmp_path: Path):
        """Option: 10 contracts @ $2.50 with multiplier 100 → notional $2500."""
        account = _account(max_notional_exposure=3000.0)
        broker = PaperBroker()
        store = SignalStore(tmp_path / "test.db")
        engine = _engine(store, account, broker)

        # Entry: 10 AAPL 200C @ $2.50
        # Notional: 10 × 2.50 × 100 = $2500
        signal = _signal(
            symbol=SYMBOL_OPTION,
            side=Side.BUY,
            quantity=10.0,
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

        results = await engine.handle_signal(signal)
        assert len(results) == 1
        result = results[0]
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == 10.0

        # Check notional calculation: should include multiplier
        exposure = confirmed_open_notional(store, account.account_id)
        # position has 10 contracts @ $2.50 avg, with multiplier 100
        # notional = 10 * 2.50 * 100 = $2500
        assert exposure.notional == pytest.approx(2500.0)

    @pytest.mark.asyncio
    async def test_fx_micro_lot_notional_gates_allocation(self, tmp_path: Path):
        """FX micro lot: 3 contracts @ 1.1 with multiplier 1000 → notional 3300.

        Rejected when max_notional_exposure=3000, admitted when 4000.
        """
        account_small = _account(account_id="acct-small", max_notional_exposure=3000.0)
        account_large = _account(account_id="acct-large", max_notional_exposure=4000.0)
        broker = PaperBroker()
        store_small = SignalStore(tmp_path / "small.db")
        store_large = SignalStore(tmp_path / "large.db")

        engine_small = _engine(store_small, account_small, broker)
        engine_large = _engine(store_large, account_large, broker)

        # FX micro lot: 3 × 1.1 × 1000 = 3300 notional
        signal = _signal(
            symbol=SYMBOL_FX,
            side=Side.BUY,
            quantity=3.0,
            price=1.1,
            asset_class=AssetClass.FOREX,
            fx=FxContractSpec(base_currency="EUR", quote_currency="USD", unit="micro_lot_1000"),
        )

        # Should be rejected for acct-small (ceiling 3000 < 3300)
        results_small = await engine_small.handle_signal(signal)
        assert len(results_small) == 1
        assert results_small[0].status == OrderStatus.REJECTED

        # Should be admitted for acct-large (ceiling 4000 >= 3300)
        results_large = await engine_large.handle_signal(signal)
        assert len(results_large) == 1
        assert results_large[0].status == OrderStatus.FILLED

        # Check notional for admitted case
        exposure = confirmed_open_notional(store_large, account_large.account_id)
        assert exposure.notional == pytest.approx(3300.0)

    @pytest.mark.asyncio
    async def test_fx_no_spec_fills_and_notes_unit_assumption(self, tmp_path: Path):
        """FOREX without spec fills the order with note 'fx unit assumed: units'."""
        account = _account(max_notional_exposure=1000.0)
        broker = PaperBroker()
        store = SignalStore(tmp_path / "test.db")
        engine = _engine(store, account, broker)

        # FX without spec: 10 @ 1.1 with assumed multiplier 1.0 → notional 11
        signal = _signal(
            symbol=SYMBOL_FX,
            side=Side.BUY,
            quantity=10.0,
            price=1.1,
            asset_class=AssetClass.FOREX,
            fx=None,  # No spec - will default to "units" (1.0)
        )

        results = await engine.handle_signal(signal)
        assert len(results) == 1
        result = results[0]
        # Should be FILLED with note about unit assumption
        assert result.status == OrderStatus.FILLED
        # The message should contain info about FX unit assumption
        assert "fx unit assumed" in result.message

        # Notional should be 10 * 1.1 * 1.0 = 11
        exposure = confirmed_open_notional(store, account.account_id)
        assert exposure.notional == pytest.approx(11.0)


class TestContractMultiplierPersistence:
    """contract_multiplier is stored in orders table for later use."""

    @pytest.mark.asyncio
    async def test_contract_multiplier_saved_for_option(self, tmp_path: Path):
        """Option order saves contract_multiplier=100."""
        account = _account()
        broker = PaperBroker()
        store = SignalStore(tmp_path / "test.db")
        engine = _engine(store, account, broker)

        signal = _signal(
            symbol=SYMBOL_OPTION,
            side=Side.BUY,
            quantity=10.0,
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

        results = await engine.handle_signal(signal)
        assert len(results) == 1

        # Query the order to verify contract_multiplier is saved
        rows = store.list_filled_orders_chronological(account.account_id)
        assert len(rows) == 1
        # Note: contract_multiplier may not be in the dict returned by list_filled_orders_chronological
        # so we check it via a direct query
        with store._connect() as conn:
            row = conn.execute(
                "SELECT contract_multiplier FROM orders WHERE account_id = ?",
                (account.account_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == 100.0

    @pytest.mark.asyncio
    async def test_contract_multiplier_saved_for_fx_micro_lot(self, tmp_path: Path):
        """FX micro lot order saves contract_multiplier=1000."""
        account = _account()
        broker = PaperBroker()
        store = SignalStore(tmp_path / "test.db")
        engine = _engine(store, account, broker)

        signal = _signal(
            symbol=SYMBOL_FX,
            side=Side.BUY,
            quantity=5.0,
            price=1.2,
            asset_class=AssetClass.FOREX,
            fx=FxContractSpec(base_currency="EUR", quote_currency="USD", unit="micro_lot_1000"),
        )

        results = await engine.handle_signal(signal)
        assert len(results) == 1

        # Query the order to verify contract_multiplier is saved
        with store._connect() as conn:
            row = conn.execute(
                "SELECT contract_multiplier FROM orders WHERE account_id = ?",
                (account.account_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == 1000.0
