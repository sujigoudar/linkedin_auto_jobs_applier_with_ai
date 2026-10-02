"""
WP-13: stop/target updates and default target sizing.

Tests for:
1. Target sizing defaults: equal-split with last level taking remainder
2. STOP_UPDATE intent: replace stop price on managed lifecycles
3. TARGET_UPDATE intent: replace targets on managed lifecycles
"""
import pytest
from app.models import (
    Signal, Intent, Side, AssetClass, OrderStatus, ProfitTarget, DestinationAccount
)
from app.brokers.paper import PaperBroker
from app.engine import SignalCopierEngine
from app.routing import RoutingConfig, RoutingRule
from app.db import SignalStore


@pytest.mark.asyncio
async def test_target_sizing_equal_split_three_levels(tmp_path):
    """
    Test equal-split default sizing for 3 targets.
    Input: targets with no fraction
    Expected: 1/3, 1/3, 1/3
    """
    # Setup store and brokers
    store = SignalStore(tmp_path / "store.db")
    paper = PaperBroker()

    # Set up account
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    # Create routing
    rule = RoutingRule(source="test", destinations=["acct1"])
    routing = RoutingConfig(rules=[rule], accounts={"acct1": account})

    engine = SignalCopierEngine(store=store, brokers={"paper": paper}, routing=routing)

    # Create entry signal: BUY 100 AAPL with 3 targets
    entry_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        quantity=100.0,
        price=150.0,
        stop_loss=145.0,
        targets=[
            ProfitTarget(price=155.0, label="TP1"),
            ProfitTarget(price=160.0, label="TP2"),
            ProfitTarget(price=165.0, label="TP3"),
        ],
    )

    # Submit entry
    results = await engine.handle_signal(entry_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED

    # Get the lifecycle and check targets
    lifecycle = engine.lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert len(lifecycle.plan.targets) == 3

    # Check equal-split sizing: 1/3, 1/3, 1/3
    assert lifecycle.plan.targets[0].reduce_fraction == pytest.approx(1/3)
    assert lifecycle.plan.targets[1].reduce_fraction == pytest.approx(1/3)
    assert lifecycle.plan.targets[2].reduce_fraction == pytest.approx(1/3)


@pytest.mark.asyncio
async def test_target_sizing_single_tp_becomes_one(tmp_path):
    """
    Test that a single take_profit becomes one target at 1.0 fraction.
    """
    # Setup store and brokers
    store = SignalStore(tmp_path / "store.db")
    paper = PaperBroker()

    # Set up account
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    # Create routing
    rule = RoutingRule(source="test", destinations=["acct1"])
    routing = RoutingConfig(rules=[rule], accounts={"acct1": account})

    engine = SignalCopierEngine(store=store, brokers={"paper": paper}, routing=routing)

    # Entry with single take_profit
    entry_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        quantity=100.0,
        price=150.0,
        stop_loss=145.0,
        take_profit=155.0,
        targets=[],  # No explicit targets list
    )

    results = await engine.handle_signal(entry_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED

    lifecycle = engine.lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert len(lifecycle.plan.targets) == 1
    assert lifecycle.plan.targets[0].reduce_fraction == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_stop_update_plain_account_rejected(tmp_path):
    """
    Test STOP_UPDATE intent on plain account is rejected.
    """
    # Setup store and brokers
    store = SignalStore(tmp_path / "store.db")
    paper = PaperBroker()

    # Set up plain account (managed_lifecycle=False)
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=False)

    # Create routing
    rule = RoutingRule(source="test", destinations=["acct1"])
    routing = RoutingConfig(rules=[rule], accounts={"acct1": account})

    engine = SignalCopierEngine(store=store, brokers={"paper": paper}, routing=routing)

    # STOP_UPDATE signal
    update_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        intent=Intent.STOP_UPDATE,
        stop_loss=147.0,
        quantity=None,
    )

    results = await engine.handle_signal(update_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.REJECTED
    assert "managed lifecycle" in results[0].message.lower()


@pytest.mark.asyncio
async def test_stop_update_on_managed_account(tmp_path):
    """
    Test STOP_UPDATE intent on managed account updates the stop price.
    """
    # Setup store and brokers
    store = SignalStore(tmp_path / "store.db")
    paper = PaperBroker()

    # Set up managed account
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    # Create routing
    rule = RoutingRule(source="test", destinations=["acct1"])
    routing = RoutingConfig(rules=[rule], accounts={"acct1": account})

    engine = SignalCopierEngine(store=store, brokers={"paper": paper}, routing=routing)

    # Entry signal: BUY 100 AAPL
    entry_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        quantity=100.0,
        price=150.0,
        stop_loss=145.0,
        take_profit=155.0,
    )

    results = await engine.handle_signal(entry_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED

    # Verify lifecycle exists
    lifecycle = engine.lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.stop.desired_price == 145.0

    # Send STOP_UPDATE
    update_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        intent=Intent.STOP_UPDATE,
        stop_loss=147.0,
        quantity=None,
    )

    results = await engine.handle_signal(update_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED

    # Verify stop price was updated
    lifecycle = engine.lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.stop.desired_price == 147.0
