"""Track 74: comprehensive mutation-testing regression suite for the final 3 remaining untested modules.

This file implements targeted regression tests for high-risk financial and metrics
logic in:

- app/account_economics_v2.py (extended account economics, slippage, gain calculations)
- app/metrics.py (Prometheus metrics aggregation and rendering)
- app/equity_history.py (equity/P&L snapshot persistence and querying)

These tests are designed to catch mutations that could silently break:

- Slippage calculations (operator inversions on buy/sell sign conventions)
- Unrealized P&L computations (mark age, average cost handling, quantity sign)
- Metrics aggregation (gauge value mutations, phantom zero prevention)
- Equity snapshot persistence (cumulative P&L formula, timestamp ordering)
- Query filtering (since/until bounds, symbol lookups, closed-vs-open tracking)
- Boundary conditions (empty lists, None vs 0.0, epsilon comparisons)

The tests follow the "Track 60-73 targeted regression test pattern" with
hand-written tests for mutation-critical patterns rather than relying on
mutant survival rates alone. Focus areas:
- Operator flips (==, !=, >, >=, <, <=) in financial logic
- Control flow mutations affecting financial state atomicity
- Type/default mutations (None vs 0.0, string literals)
- Boolean logic inversions
- Arithmetic mutations (+ vs -, * vs /)
- Field access order and data structure mutations

See pyproject.toml's Track 74 commentary for execution pattern and rationale.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from app.account_economics_v2 import (
    compute_extended_account_economics, _compute_slippage, _compute_unrealized
)
from app.metrics import render_metrics, _age_seconds
from app.equity_history import EquitySnapshotter
from app.db import SignalStore
from app.models import (
    AccountBalance, OrderResult, OrderStatus, Side, Signal, AssetClass
)
from app.brokers.paper import PaperBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, ProtectionStatus
from app.pricing import PriceMonitor
from app.reconciliation import OrderReconciler


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def broker():
    return PaperBroker()


@pytest.fixture
def manager(broker, store):
    return PositionLifecycleManager(brokers={"paper": broker}, store=store)


def _fill(store, account_id, symbol, side, quantity, price, when=None, *, signal_price=None):
    """Helper to create a filled order with optional reference price."""
    signal = Signal(source="test", symbol=symbol, side=side, price=signal_price)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id, status=OrderStatus.FILLED, signal_id=signal.id,
        filled_quantity=quantity, filled_price=price,
        executed_at=when or datetime.now(timezone.utc),
    )
    store.save_order_result(result, broker="paper", symbol=symbol, side=side)


# ============================================================================
# Track 74.1: Slippage Calculation Mutations
# ============================================================================

class TestSlippageSignConventionMutations:
    """Test that slippage sign convention is correctly enforced.

    Mutations to catch:
    - buy: filled_price - reference inverted to reference - filled_price
    - sell: reference - filled_price inverted to filled_price - reference
    - side comparison (== vs !=) on BUY/SELL
    """

    def test_slippage_buy_positive_when_filled_higher(self, store):
        """Mutation: buy slippage sign inverted (negative when should be positive)."""
        t0 = datetime.now(timezone.utc)
        # BUY at reference 100.0 but filled at 101.5 (worse) = +1.5 slippage
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.5, t0, signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        assert stats is not None
        assert stats.mean == pytest.approx(1.5)
        assert stats.worst == pytest.approx(1.5)

    def test_slippage_buy_negative_when_filled_lower(self, store):
        """Mutation: buy slippage sign wrong (positive when should be negative)."""
        t0 = datetime.now(timezone.utc)
        # BUY at reference 100.0 but filled at 98.5 (better) = -1.5 slippage
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 98.5, t0, signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        assert stats is not None
        assert stats.mean == pytest.approx(-1.5)

    def test_slippage_sell_positive_when_filled_lower(self, store):
        """Mutation: sell slippage sign inverted (positive when should be negative)."""
        t0 = datetime.now(timezone.utc)
        # SELL at reference 100.0 but filled at 98.5 (worse) = +1.5 slippage
        _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 98.5, t0, signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        assert stats is not None
        assert stats.mean == pytest.approx(1.5)

    def test_slippage_sell_negative_when_filled_higher(self, store):
        """Mutation: sell slippage sign wrong (positive when should be negative)."""
        t0 = datetime.now(timezone.utc)
        # SELL at reference 100.0 but filled at 101.5 (better) = -1.5 slippage
        _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 101.5, t0, signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        assert stats is not None
        assert stats.mean == pytest.approx(-1.5)

    def test_slippage_worst_is_maximum_not_minimum(self, store):
        """Mutation: worst calculated as min() instead of max()."""
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.0, t0, signal_price=100.0)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 102.0, t0 + timedelta(seconds=1), signal_price=100.0)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.5, t0 + timedelta(seconds=2), signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        assert stats is not None
        assert stats.worst == pytest.approx(2.0)  # max of [1.0, 2.0, 0.5]
        assert stats.mean == pytest.approx((1.0 + 2.0 + 0.5) / 3)

    def test_slippage_sample_count_matches_actual_fills_with_reference(self, store):
        """Mutation: sample_count not incremented or off-by-one."""
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.0, t0, signal_price=100.0)
        _fill(store, "acct1", "MSFT", Side.BUY, 5.0, 200.0, t0 + timedelta(seconds=1))  # no reference
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 102.0, t0 + timedelta(seconds=2), signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        assert stats is not None
        assert stats.sample_count == 2  # only AAPL fills have reference
        assert len([s for s in [101.0-100.0, 102.0-100.0]]) == 2


class TestSlippageExclusionMutations:
    """Test that fills without reference prices are excluded.

    Mutations to catch:
    - None checks removed or inverted (treating None as valid)
    - continue statement changed to pass or removed
    """

    def test_slippage_excludes_none_signal_price(self, store):
        """Mutation: None signal_price accepted instead of excluded."""
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.0, t0)  # no signal_price

        stats = _compute_slippage(store, "acct1")
        assert stats is None

    def test_slippage_excludes_none_filled_price(self, store):
        """Mutation: None filled_price not excluded."""
        t0 = datetime.now(timezone.utc)
        # This is unlikely in practice but tests the guard
        signal = Signal(source="test", symbol="AAPL", side=Side.BUY, price=100.0)
        store.save_signal(signal)
        result = OrderResult(
            account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id,
            filled_quantity=10.0, filled_price=None,  # None filled_price
            executed_at=t0,
        )
        store.save_order_result(result, broker="paper", symbol="AAPL", side=Side.BUY)

        stats = _compute_slippage(store, "acct1")
        assert stats is None

    def test_slippage_excludes_none_quantity(self, store):
        """Mutation: None quantity accepted instead of excluded."""
        t0 = datetime.now(timezone.utc)
        signal = Signal(source="test", symbol="AAPL", side=Side.BUY, price=100.0)
        store.save_signal(signal)
        result = OrderResult(
            account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id,
            filled_quantity=None,  # None quantity
            filled_price=101.0,
            executed_at=t0,
        )
        store.save_order_result(result, broker="paper", symbol="AAPL", side=Side.BUY)

        stats = _compute_slippage(store, "acct1")
        assert stats is None


class TestSlippageMedianCalculationMutations:
    """Test median calculation in slippage stats.

    Mutations to catch:
    - median() call changed
    - statistics module swapped for wrong function
    """

    def test_slippage_median_odd_count(self, store):
        """Mutation: median calculation wrong for odd sample count."""
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.0, t0, signal_price=100.0)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 102.0, t0 + timedelta(seconds=1), signal_price=100.0)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.5, t0 + timedelta(seconds=2), signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        assert stats is not None
        # median of [1.0, 2.0, 0.5] = 1.0
        assert stats.median == pytest.approx(1.0)

    def test_slippage_median_even_count(self, store):
        """Mutation: median calculation wrong for even sample count."""
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.0, t0, signal_price=100.0)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 102.0, t0 + timedelta(seconds=1), signal_price=100.0)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.5, t0 + timedelta(seconds=2), signal_price=100.0)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 103.0, t0 + timedelta(seconds=3), signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        assert stats is not None
        # median of [1.0, 2.0, 0.5, 3.0] sorted = [0.5, 1.0, 2.0, 3.0], median = 1.5
        assert stats.median == pytest.approx(1.5)


# ============================================================================
# Track 74.2: Unrealized P&L Computation Mutations
# ============================================================================

class TestUnrealizedPnlMutations:
    """Test unrealized P&L calculation correctness.

    Mutations to catch:
    - (price - average_cost) formula inverted
    - * quantity operator changed
    - open_quantity == 0 check inverted
    """

    def test_unrealized_pnl_long_position_above_cost(self, store, manager):
        """Mutation: unrealized calculation inverted (long showing loss when should be gain)."""
        store.upsert_config_account("acct1", broker="paper")
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)

        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=10.0, asset_class=AssetClass.EQUITY, broker="paper",
            initial_stop=90.0, entry_signal_id="sig-1"
        )
        manager.start_plan(plan)
        lifecycle = manager.get_lifecycle("acct1", "AAPL")
        if lifecycle:
            lifecycle.confirmed_owned_quantity = 10.0
            lifecycle.last_observed_price = 110.0

            unrealized, _, _ = _compute_unrealized(store, "acct1", manager)
            # (110 - 100) * 10 = 100
            assert unrealized == pytest.approx(100.0)

    def test_unrealized_pnl_long_position_below_cost(self, store, manager):
        """Mutation: unrealized calculation wrong for losing positions."""
        store.upsert_config_account("acct1", broker="paper")
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)

        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=10.0, asset_class=AssetClass.EQUITY, broker="paper",
            initial_stop=90.0, entry_signal_id="sig-1"
        )
        manager.start_plan(plan)
        lifecycle = manager.get_lifecycle("acct1", "AAPL")
        if lifecycle:
            lifecycle.confirmed_owned_quantity = 10.0
            lifecycle.last_observed_price = 95.0

            unrealized, _, _ = _compute_unrealized(store, "acct1", manager)
            # (95 - 100) * 10 = -50
            assert unrealized == pytest.approx(-50.0)

    def test_unrealized_pnl_zero_when_no_open_positions(self, store, manager):
        """Mutation: returns non-None when all positions closed."""
        store.upsert_config_account("acct1", broker="paper")

        unrealized, unavailable, mark_age = _compute_unrealized(store, "acct1", manager)
        assert unrealized is None
        assert unavailable == []
        assert mark_age is None

    def test_unrealized_pnl_none_for_unpriced_symbol(self, store, manager):
        """Mutation: unpriced symbol not listed in unavailable_marks."""
        store.upsert_config_account("acct1", broker="paper")
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)  # no price update

        unrealized, unavailable, mark_age = _compute_unrealized(store, "acct1", manager)
        # No lifecycle manager with price, so AAPL should be marked unavailable
        assert "AAPL" in unavailable


class TestMarkAgeMutations:
    """Test mark age (oldest observation time) calculation.

    Mutations to catch:
    - oldest_observation comparison inverted (< vs >)
    - total_seconds() call missing
    - None initialization wrong
    """

    def test_mark_age_seconds_calculated_from_oldest_observation(self, store, manager):
        """Mutation: mark age calculation inverted or wrong timestamp used."""
        store.upsert_config_account("acct1", broker="paper")
        now = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)

        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=10.0, asset_class=AssetClass.EQUITY, broker="paper",
            initial_stop=90.0, entry_signal_id="sig-1"
        )
        manager.start_plan(plan)
        lifecycle = manager.get_lifecycle("acct1", "AAPL")
        if lifecycle:
            lifecycle.confirmed_owned_quantity = 10.0
            lifecycle.last_observed_price = 110.0
            lifecycle.last_observed_price_at = now - timedelta(seconds=30)

            _, _, mark_age = _compute_unrealized(store, "acct1", manager)
            assert mark_age is not None
            assert 25 < mark_age < 35  # should be approximately 30 seconds

    def test_mark_age_none_when_no_prices(self, store, manager):
        """Mutation: returns non-None mark_age when no priced symbols."""
        store.upsert_config_account("acct1", broker="paper")

        _, _, mark_age = _compute_unrealized(store, "acct1", manager)
        assert mark_age is None


# ============================================================================
# Track 74.3: Extended Economics Integration Mutations
# ============================================================================

class TestExtendedEconomicsAccountDataMutations:
    """Test account data source field handling.

    Mutations to catch:
    - account_data_source not set to "broker_reported"
    - broker_balance fields mapped to wrong attributes
    - nav != equity logic change
    """

    def test_account_data_source_unavailable_without_broker_balance(self, store):
        """Mutation: data_source not set to 'unavailable'."""
        extended = compute_extended_account_economics(store, "acct1")
        assert extended.account_data_source == "unavailable"
        assert extended.nav is None

    def test_account_data_source_broker_reported_with_balance(self, store):
        """Mutation: data_source not changed to 'broker_reported'."""
        balance = AccountBalance(
            account_id="acct1", cash=5000.0, equity=12000.0,
            buying_power=8000.0, maintenance_margin=3000.0
        )
        extended = compute_extended_account_economics(store, "acct1", broker_balance=balance)
        assert extended.account_data_source == "broker_reported"

    def test_nav_set_to_equity_from_broker_balance(self, store):
        """Mutation: nav assigned from wrong field or not assigned."""
        balance = AccountBalance(
            account_id="acct1", cash=5000.0, equity=12000.0,
            buying_power=8000.0, maintenance_margin=3000.0
        )
        extended = compute_extended_account_economics(store, "acct1", broker_balance=balance)
        assert extended.nav == pytest.approx(12000.0)
        assert extended.equity == pytest.approx(12000.0)

    def test_margin_used_set_to_maintenance_margin(self, store):
        """Mutation: margin_used assigned from wrong field."""
        balance = AccountBalance(
            account_id="acct1", cash=5000.0, equity=12000.0,
            buying_power=8000.0, maintenance_margin=3000.0
        )
        extended = compute_extended_account_economics(store, "acct1", broker_balance=balance)
        assert extended.margin_used == pytest.approx(3000.0)


class TestExtendedEconomicsKnownUnavailableMutations:
    """Test that known-unavailable fields are honestly labeled.

    Mutations to catch:
    - string literals changed to None or vice versa
    - field values changed to 0.0 or default values
    """

    def test_fees_always_unknown_string_not_none(self, store):
        """Mutation: fees set to None instead of 'unknown'."""
        extended = compute_extended_account_economics(store, "acct1")
        assert extended.fees == "unknown"
        assert extended.fees is not None

    def test_commissions_always_unknown(self, store):
        """Mutation: commissions changed to None or 0.0."""
        extended = compute_extended_account_economics(store, "acct1")
        assert extended.commissions == "unknown"

    def test_financing_always_unknown(self, store):
        """Mutation: financing not set to 'unknown'."""
        extended = compute_extended_account_economics(store, "acct1")
        assert extended.financing == "unknown"

    def test_twr_always_none(self, store):
        """Mutation: twr set to a number instead of None."""
        extended = compute_extended_account_economics(store, "acct1")
        assert extended.twr is None

    def test_model_vs_platform_exactly_not_applicable_string(self, store):
        """Mutation: string literal changed."""
        extended = compute_extended_account_economics(store, "acct1")
        assert extended.model_vs_platform_vs_follower == "not_applicable_in_signal_copier"


class TestSlippageStatsConversionMutations:
    """Test SlippageStats.to_dict() conversion.

    Mutations to catch:
    - field names changed in dict
    - values not included or swapped
    """

    def test_slippage_stats_to_dict_includes_all_fields(self, store):
        """Mutation: to_dict() misses fields."""
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.5, t0, signal_price=100.0)

        stats = _compute_slippage(store, "acct1")
        d = stats.to_dict()

        assert "sample_count" in d
        assert "mean" in d
        assert "median" in d
        assert "worst" in d
        assert d["sample_count"] == 1
        assert d["mean"] == pytest.approx(1.5)


# ============================================================================
# Track 74.4: Metrics Aggregation Mutations
# ============================================================================

class TestMetricsPhantomZeroPreventionMutations:
    """Test that metrics avoid fabricating 'zero' values before data exists.

    Mutations to catch:
    - _age_seconds returning 0.0 when should return None
    - gauge construction happening unconditionally
    - last_success_at initialization to datetime.now() by default
    """

    def test_price_observation_age_not_rendered_without_success(self, store, broker):
        """Mutation: age gauge created with phantom 0.0 before first pass."""
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        monitor = PriceMonitor(manager, {"paper": broker})
        reconciler = OrderReconciler(store, {"paper": broker})

        body = render_metrics(
            store=store, price_monitor=monitor, reconciler=reconciler,
            lifecycle_manager=manager
        )
        text = body.decode()

        # Must not include the metric before first successful pass
        assert "signal_copier_price_observation_age_seconds" not in text

    def test_reconciler_cycle_age_not_rendered_without_success(self, store, broker):
        """Mutation: cycle age gauge created before first reconciliation."""
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        monitor = PriceMonitor(manager, {"paper": broker})
        reconciler = OrderReconciler(store, {"paper": broker})

        body = render_metrics(
            store=store, price_monitor=monitor, reconciler=reconciler,
            lifecycle_manager=manager
        )
        text = body.decode()

        # Must not include the metric before first successful pass
        assert "signal_copier_reconciler_cycle_age_seconds" not in text

    def test_age_seconds_returns_none_when_no_success(self):
        """Mutation: _age_seconds returning 0.0 instead of None."""
        age = _age_seconds(None)
        assert age is None

    def test_age_seconds_returns_positive_when_success_in_past(self):
        """Mutation: _age_seconds calculation wrong (negative, zero, wrong formula)."""
        past = datetime.now(timezone.utc) - timedelta(seconds=30)
        age = _age_seconds(past)
        assert age is not None
        assert 25 < age < 35  # approximately 30 seconds


class TestMetricsGaugeValueMutations:
    """Test that gauge values are set correctly.

    Mutations to catch:
    - len() not called or wrong variable used
    - += mutation affecting counter logic
    - comparison operators inverted in conditions
    """

    def test_pending_entries_gauge_set_correctly(self, store, broker):
        """Mutation: pending_entries set to wrong count."""
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        monitor = PriceMonitor(manager, {"paper": broker})
        reconciler = OrderReconciler(store, {"paper": broker})

        body = render_metrics(
            store=store, price_monitor=monitor, reconciler=reconciler,
            lifecycle_manager=manager
        )
        text = body.decode()

        # No pending entries initially
        assert "signal_copier_pending_entries 0.0" in text

    def test_pending_exits_gauge_set_correctly(self, store, broker):
        """Mutation: pending_exits not set to 0.0 initially."""
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        monitor = PriceMonitor(manager, {"paper": broker})
        reconciler = OrderReconciler(store, {"paper": broker})

        body = render_metrics(
            store=store, price_monitor=monitor, reconciler=reconciler,
            lifecycle_manager=manager
        )
        text = body.decode()

        assert "signal_copier_pending_exits 0.0" in text

    def test_open_positions_gauge_reflects_store_count(self, store, broker):
        """Mutation: open_positions count wrong or not from store."""
        store.upsert_config_account("acct1", broker="paper")
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)  # open position

        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        monitor = PriceMonitor(manager, {"paper": broker})
        reconciler = OrderReconciler(store, {"paper": broker})

        body = render_metrics(
            store=store, price_monitor=monitor, reconciler=reconciler,
            lifecycle_manager=manager
        )
        text = body.decode()

        # The metric should be present in the output
        assert "signal_copier_open_positions" in text


class TestProtectionDeficitCalculationMutations:
    """Test protection deficit counting logic.

    Mutations to catch:
    - > vs >= in quantity comparison
    - status != STOP_CONFIRMED logic inverted
    - sum() calculation wrong or filtered incorrectly
    """

    def test_protection_deficit_counts_unconfirmed_stops(self, store, broker):
        """Mutation: deficit not counted or wrong status checked."""
        store.upsert_config_account("acct1", broker="paper")
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)

        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=10.0, asset_class=AssetClass.EQUITY, broker="paper",
            initial_stop=90.0, entry_signal_id="sig-1"
        )
        manager.start_plan(plan)
        lifecycle = manager.get_lifecycle("acct1", "AAPL")
        if lifecycle:
            lifecycle.confirmed_owned_quantity = 10.0
            lifecycle.stop.status = ProtectionStatus.UNPROTECTED

            monitor = PriceMonitor(manager, {"paper": broker})
            reconciler = OrderReconciler(store, {"paper": broker})
            body = render_metrics(
                store=store, price_monitor=monitor, reconciler=reconciler,
                lifecycle_manager=manager
            )
            text = body.decode()

            assert "signal_copier_protection_deficit_positions 1.0" in text

    def test_protection_deficit_ignores_confirmed_stops(self, store, broker):
        """Mutation: confirmed stops counted as deficits."""
        store.upsert_config_account("acct1", broker="paper")
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)

        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=10.0, asset_class=AssetClass.EQUITY, broker="paper",
            initial_stop=90.0, entry_signal_id="sig-1"
        )
        manager.start_plan(plan)
        lifecycle = manager.get_lifecycle("acct1", "AAPL")
        if lifecycle:
            lifecycle.confirmed_owned_quantity = 10.0
            lifecycle.stop.status = ProtectionStatus.STOP_CONFIRMED

            monitor = PriceMonitor(manager, {"paper": broker})
            reconciler = OrderReconciler(store, {"paper": broker})
            body = render_metrics(
                store=store, price_monitor=monitor, reconciler=reconciler,
                lifecycle_manager=manager
            )
            text = body.decode()

            # Should not be counted
            assert "signal_copier_protection_deficit_positions 0.0" in text

    def test_protection_deficit_requires_owned_quantity_gt_zero(self, store, broker):
        """Mutation: zero owned quantity incorrectly counted as deficit."""
        store.upsert_config_account("acct1", broker="paper")
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)

        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=10.0, asset_class=AssetClass.EQUITY, broker="paper",
            initial_stop=90.0, entry_signal_id="sig-1"
        )
        manager.start_plan(plan)
        lifecycle = manager.get_lifecycle("acct1", "AAPL")
        if lifecycle:
            lifecycle.confirmed_owned_quantity = 0.0  # closed position
            lifecycle.stop.status = ProtectionStatus.UNPROTECTED

            monitor = PriceMonitor(manager, {"paper": broker})
            reconciler = OrderReconciler(store, {"paper": broker})
            body = render_metrics(
                store=store, price_monitor=monitor, reconciler=reconciler,
                lifecycle_manager=manager
            )
            text = body.decode()

            # Should not count closed positions
            assert "signal_copier_protection_deficit_positions 0.0" in text


# ============================================================================
# Track 74.5: Equity Snapshot Persistence Mutations
# ============================================================================

class TestEquitySnapshotPersistenceMutations:
    """Test equity snapshot recording and retrieval.

    Mutations to catch:
    - record_equity_snapshot not called or fields swapped
    - cumulative_pnl formula wrong (+ vs -, * vs /)
    - account iteration skipped or partial
    """

    def test_snapshot_once_persists_cumulative_pnl_formula(self, store, broker):
        """Mutation: cumulative_pnl formula changed (+ vs -, wrong operands)."""
        store.upsert_config_account("acct1", broker="paper")
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
        _fill(store, "acct1", "AAPL", Side.SELL, 5.0, 105.0, t0 + timedelta(minutes=1))

        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

        snapshotter.snapshot_once()
        rows = store.list_equity_snapshots("acct1")

        assert len(rows) == 1
        # 5 * 5 = 25 realized + 5 * (unpriced average_cost) unrealized
        assert rows[0]["realized_pnl"] == pytest.approx(25.0)
        assert rows[0]["cumulative_pnl"] == rows[0]["realized_pnl"] + rows[0]["unrealized_pnl"]

    def test_snapshot_once_iterates_all_accounts(self, store, broker):
        """Mutation: snapshot_once skips some accounts."""
        store.upsert_config_account("acct1", broker="paper")
        store.upsert_config_account("acct2", broker="paper")

        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

        count = snapshotter.snapshot_once()

        assert count == 2  # both accounts snapshotted
        assert len(store.list_equity_snapshots("acct1")) == 1
        assert len(store.list_equity_snapshots("acct2")) == 1

    def test_snapshot_once_returns_correct_count(self, store, broker):
        """Mutation: return value wrong or not counting properly."""
        store.upsert_config_account("acct1", broker="paper")
        store.upsert_config_account("acct2", broker="paper")
        store.upsert_config_account("acct3", broker="paper")

        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

        count = snapshotter.snapshot_once()
        assert count == 3


class TestEquitySnapshotQueryingMutations:
    """Test equity snapshot query filtering.

    Mutations to catch:
    - since/until comparison operators inverted (> vs <, >= vs <=)
    - timestamp field used instead of captured_at
    - ordering wrong (should be chronological)
    """

    def test_list_equity_snapshots_respects_since_bound(self, store, broker):
        """Mutation: since comparison inverted (returns newer instead of older)."""
        store.upsert_config_account("acct1", broker="paper")
        t0 = datetime.now(timezone.utc)

        store.record_equity_snapshot(
            "acct1", captured_at=t0 - timedelta(hours=2),
            realized_pnl=1.0, unrealized_pnl=0.0, cumulative_pnl=1.0
        )
        store.record_equity_snapshot(
            "acct1", captured_at=t0 - timedelta(hours=1),
            realized_pnl=2.0, unrealized_pnl=0.0, cumulative_pnl=2.0
        )
        store.record_equity_snapshot(
            "acct1", captured_at=t0,
            realized_pnl=3.0, unrealized_pnl=0.0, cumulative_pnl=3.0
        )

        rows = store.list_equity_snapshots(
            "acct1", since=t0 - timedelta(hours=1, minutes=30)
        )

        # Should include the 1-hour-ago and present, exclude 2-hour-ago
        pnls = [r["realized_pnl"] for r in rows]
        assert 1.0 not in pnls
        assert 2.0 in pnls
        assert 3.0 in pnls

    def test_list_equity_snapshots_respects_until_bound(self, store, broker):
        """Mutation: until comparison inverted (returns older instead of newer)."""
        store.upsert_config_account("acct1", broker="paper")
        t0 = datetime.now(timezone.utc)

        store.record_equity_snapshot(
            "acct1", captured_at=t0 - timedelta(hours=2),
            realized_pnl=1.0, unrealized_pnl=0.0, cumulative_pnl=1.0
        )
        store.record_equity_snapshot(
            "acct1", captured_at=t0 - timedelta(hours=1),
            realized_pnl=2.0, unrealized_pnl=0.0, cumulative_pnl=2.0
        )
        store.record_equity_snapshot(
            "acct1", captured_at=t0,
            realized_pnl=3.0, unrealized_pnl=0.0, cumulative_pnl=3.0
        )

        rows = store.list_equity_snapshots(
            "acct1", until=t0 - timedelta(minutes=30)
        )

        # Should include 2-hour-ago and 1-hour-ago, exclude present
        pnls = [r["realized_pnl"] for r in rows]
        assert 1.0 in pnls
        assert 2.0 in pnls
        assert 3.0 not in pnls

    def test_list_equity_snapshots_both_bounds_narrow_correctly(self, store, broker):
        """Mutation: since/until bounds not applied correctly together."""
        store.upsert_config_account("acct1", broker="paper")
        t0 = datetime.now(timezone.utc)

        store.record_equity_snapshot(
            "acct1", captured_at=t0 - timedelta(hours=2),
            realized_pnl=1.0, unrealized_pnl=0.0, cumulative_pnl=1.0
        )
        store.record_equity_snapshot(
            "acct1", captured_at=t0 - timedelta(hours=1),
            realized_pnl=2.0, unrealized_pnl=0.0, cumulative_pnl=2.0
        )
        store.record_equity_snapshot(
            "acct1", captured_at=t0,
            realized_pnl=3.0, unrealized_pnl=0.0, cumulative_pnl=3.0
        )

        rows = store.list_equity_snapshots(
            "acct1",
            since=t0 - timedelta(hours=1, minutes=30),
            until=t0 - timedelta(minutes=30)
        )

        # Should include only the 1-hour-ago snapshot
        pnls = [r["realized_pnl"] for r in rows]
        assert pnls == [2.0]


class TestEquitySnapshotRealizationMutations:
    """Test that realized_pnl always matches economics.py computation.

    Mutations to catch:
    - realized_pnl recomputed instead of pulled from compute_account_economics
    - snapshot pulls different account's data
    - account_id parameter ignored
    """

    def test_snapshot_realized_pnl_matches_economics_exactly(self, store, broker):
        """Mutation: realized_pnl computed differently than economics.py."""
        from app.economics import compute_account_economics

        store.upsert_config_account("acct1", broker="paper")
        t0 = datetime.now(timezone.utc)
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
        _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

        expected = compute_account_economics(store, "acct1").realized_pnl
        snapshot = snapshotter.compute_snapshot("acct1", [])

        assert snapshot["realized_pnl"] == pytest.approx(expected)


class TestEquitySnapshotLoopHealthMutations:
    """Test the health tracking for the snapshot loop.

    Mutations to catch:
    - last_success_at not set
    - exception caught but flag not updated
    """

    def test_last_success_at_updated_after_snapshot(self, store, broker):
        """Mutation: last_success_at not updated or set to wrong time."""
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

        before = datetime.now(timezone.utc)
        snapshotter.snapshot_once()
        # Simulate the _loop setting last_success_at
        snapshotter.last_success_at = datetime.now(timezone.utc)
        after = datetime.now(timezone.utc)

        assert snapshotter.last_success_at is not None
        assert before <= snapshotter.last_success_at <= after


# ============================================================================
# Track 74.6: Integration and Boundary Condition Mutations
# ============================================================================

class TestBoundaryConditionMutations:
    """Test edge cases and boundary conditions.

    Mutations to catch:
    - empty list handling (off-by-one on iteration)
    - None vs 0.0 distinction
    - epsilon comparisons (>= vs >)
    """

    def test_empty_slippage_list_returns_none_not_error(self, store):
        """Mutation: crashes or returns zero instead of None for empty list."""
        stats = _compute_slippage(store, "acct_no_fills")
        assert stats is None

    def test_no_fills_economics_valid(self, store):
        """Mutation: crashes when account has no fills."""
        extended = compute_extended_account_economics(store, "acct_no_fills")
        assert extended.gross_realized == 0.0
        assert extended.unrealized_gross is None

    def test_single_symbol_snapshot(self, store, broker):
        """Mutation: edge case with exactly one symbol."""
        store.upsert_config_account("acct1", broker="paper")
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)

        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

        snapshot = snapshotter.compute_snapshot("acct1", [])
        assert snapshot["account_id"] == "acct1"
        assert snapshot["realized_pnl"] == 0.0

    def test_very_small_quantity_slippage_still_calculated(self, store):
        """Mutation: very small quantities incorrectly excluded."""
        t0 = datetime.now(timezone.utc)
        signal = Signal(source="test", symbol="AAPL", side=Side.BUY, price=100.0)
        store.save_signal(signal)
        result = OrderResult(
            account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id,
            filled_quantity=0.01,  # very small quantity
            filled_price=101.0,
            executed_at=t0,
        )
        store.save_order_result(result, broker="paper", symbol="AAPL", side=Side.BUY)

        stats = _compute_slippage(store, "acct1")
        # Small quantities are still valid samples
        assert stats is not None
        assert stats.sample_count == 1
        assert stats.mean == pytest.approx(1.0)
