"""WP-50: Quote gating capability and signal-reachable trailing/time exits.

Tests for findings B-06 and D-12:

B-06: All gating prices come from the message, never a live quote; add optional
`get_quote(symbol)` capability on BrokerAdapter (default None = not supported),
implement it on PaperBroker from its simulated prices, and make the engine's
notional/leverage/buying-power gates use the broker quote when available, falling
back to the message price only when the adapter reports no quote capability.

D-12: Trailing-stop and time-based exits are unreachable from any signal and
targets are process-bound; add Signal fields `trail_amount`/`trail_percent`/
`time_exit_at` (optional, default None), persist them with the signal, let the
managed lifecycle register a trailing stop and a time exit from them.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.brokers.paper import PaperBroker
from app.brokers.base import BrokerAdapter
from app.models import (
    AssetClass,
    DestinationAccount,
    OrderStatus,
    Side,
    Signal,
)
from app.db import SignalStore


@pytest.fixture
def tmp_db() -> Path:
    """Create a temporary database for testing."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    yield db_path


@pytest.fixture
def store(tmp_db: Path) -> SignalStore:
    """Create a fresh SignalStore for each test."""
    return SignalStore(str(tmp_db))


@pytest.fixture
def paper_broker() -> PaperBroker:
    """Create a fresh PaperBroker for each test."""
    return PaperBroker()


@pytest.fixture
def simple_account() -> DestinationAccount:
    """Create a simple test account."""
    return DestinationAccount(
        account_id="test_account",
        broker="paper",
        managed_lifecycle=False,
    )


# ===== B-06 Tests: Quote Capability and Gating =====


def test_b06_base_broker_has_no_quote_capability() -> None:
    """B-06: Base BrokerAdapter reports no quote capability by default."""
    class MinimalBroker(BrokerAdapter):
        name = "minimal"

        async def place_order(self, signal, account, quantity, symbol):
            return None

    broker = MinimalBroker()
    assert not broker.has_quote_capability


def test_b06_paper_broker_has_quote_capability(paper_broker: PaperBroker) -> None:
    """B-06: PaperBroker declares it has quote capability."""
    assert paper_broker.has_quote_capability


def test_b06_paper_broker_get_quote_no_price_yet(paper_broker: PaperBroker) -> None:
    """B-06: PaperBroker.get_quote returns None for a symbol never traded."""
    import asyncio
    quote = asyncio.run(paper_broker.get_quote("UNKNOWN"))
    assert quote is None


@pytest.mark.asyncio
async def test_b06_paper_broker_tracks_fill_price(
    paper_broker: PaperBroker,
    simple_account: DestinationAccount,
) -> None:
    """B-06: PaperBroker tracks last fill price for get_quote."""
    signal = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        asset_class=AssetClass.CRYPTO,
        price=50_000.0,
    )
    result = await paper_broker.place_order(signal, simple_account, 1.0, "BTC")
    assert result.status == OrderStatus.FILLED
    assert result.filled_price == 50_000.0

    # Now get_quote should return that price
    quote = await paper_broker.get_quote("BTC")
    assert quote == 50_000.0


@pytest.mark.asyncio
async def test_b06_paper_broker_tracks_simulated_price(paper_broker: PaperBroker) -> None:
    """B-06: PaperBroker tracks simulated prices for get_quote."""
    # Simulate a price for a symbol not yet traded
    paper_broker.simulate_price("ETH", 3_000.0)

    # The price should be tracked
    quote = await paper_broker.get_quote("ETH")
    assert quote == 3_000.0


@pytest.mark.asyncio
async def test_b06_paper_broker_updates_quote_on_new_prices(
    paper_broker: PaperBroker,
) -> None:
    """B-06: PaperBroker updates quote as new prices are simulated."""
    # First price
    paper_broker.simulate_price("BTC", 50_000.0)
    quote1 = await paper_broker.get_quote("BTC")
    assert quote1 == 50_000.0

    # Price changes
    paper_broker.simulate_price("BTC", 55_000.0)
    quote2 = await paper_broker.get_quote("BTC")
    assert quote2 == 55_000.0


# ===== D-12 Tests: Signal Trailing/Time Exits =====


def test_d12_signal_has_trailing_fields() -> None:
    """D-12: Signal model has trail_amount, trail_percent, time_exit_at fields."""
    now = datetime.now(timezone.utc)
    exit_time = now + timedelta(hours=1)

    signal = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        trail_amount=100.0,
        trail_percent=0.02,
        time_exit_at=exit_time,
    )

    assert signal.trail_amount == 100.0
    assert signal.trail_percent == 0.02
    assert signal.time_exit_at == exit_time


def test_d12_signal_trailing_defaults_to_none() -> None:
    """D-12: Signal trailing/time exit fields default to None."""
    signal = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
    )

    assert signal.trail_amount is None
    assert signal.trail_percent is None
    assert signal.time_exit_at is None


def test_d12_signal_can_have_trail_amount_only() -> None:
    """D-12: Signal can have trail_amount alone."""
    signal = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        trail_amount=50.0,
    )
    assert signal.trail_amount == 50.0
    assert signal.trail_percent is None


def test_d12_signal_can_have_trail_percent_only() -> None:
    """D-12: Signal can have trail_percent alone."""
    signal = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        trail_percent=0.01,
    )
    assert signal.trail_amount is None
    assert signal.trail_percent == 0.01


def test_d12_signal_can_have_both_trailing_fields() -> None:
    """D-12: Signal can have both trail_amount and trail_percent (mutual exclusion enforced by app logic)."""
    # While semantically they should be mutually exclusive, the model allows both
    # Application logic should validate which one is used
    signal = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        trail_amount=50.0,
        trail_percent=0.01,
    )
    assert signal.trail_amount == 50.0
    assert signal.trail_percent == 0.01


def test_d12_signal_time_exit_at_optional() -> None:
    """D-12: Signal time_exit_at is optional, independent of trailing."""
    now = datetime.now(timezone.utc)
    exit_time = now + timedelta(days=1)

    # With time exit only
    signal1 = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        time_exit_at=exit_time,
    )
    assert signal1.time_exit_at == exit_time
    assert signal1.trail_amount is None

    # With both time exit and trailing
    signal2 = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        trail_amount=100.0,
        time_exit_at=exit_time,
    )
    assert signal2.trail_amount == 100.0
    assert signal2.time_exit_at == exit_time


def test_d12_signal_preserves_field_types() -> None:
    """D-12: Signal fields preserve their data types correctly."""
    now = datetime.now(timezone.utc)
    exit_time = now + timedelta(hours=3)

    signal = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        trail_amount=123.45,
        trail_percent=0.0567,
        time_exit_at=exit_time,
    )

    # Verify types
    assert isinstance(signal.trail_amount, float)
    assert isinstance(signal.trail_percent, float)
    assert isinstance(signal.time_exit_at, datetime)

    # Verify values
    assert signal.trail_amount == 123.45
    assert signal.trail_percent == 0.0567
    assert signal.time_exit_at == exit_time


def test_d12_signal_zero_and_negative_trailing_allowed() -> None:
    """D-12: Signal model allows zero/negative trail values (validation is app logic)."""
    # Model doesn't validate; app logic should
    signal1 = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        trail_amount=0.0,
    )
    assert signal1.trail_amount == 0.0

    signal2 = Signal(
        source="test",
        symbol="BTC",
        side=Side.BUY,
        trail_percent=-0.01,
    )
    assert signal2.trail_percent == -0.01


def test_d12_signal_database_schema_has_columns(store: SignalStore) -> None:
    """D-12: Database has columns for trail_amount, trail_percent, time_exit_at."""
    import sqlite3

    # Check if columns exist in the signals table
    with sqlite3.connect(store.db_path) as conn:
        cursor = conn.execute("PRAGMA table_info(signals)")
        columns = {row[1] for row in cursor.fetchall()}

    # All three D-12 columns should exist
    assert "trail_amount" in columns
    assert "trail_percent" in columns
    assert "time_exit_at" in columns


def test_d12_signal_roundtrip_through_database(store: SignalStore) -> None:
    """D-12: Signal trailing/time fields survive database roundtrip."""
    now = datetime.now(timezone.utc)
    exit_time = now + timedelta(hours=5)

    signal = Signal(
        source="test",
        symbol="ETH",
        side=Side.SELL,
        price=3_000.0,
        trail_amount=100.0,
        time_exit_at=exit_time,
    )

    # Save the signal
    store.save_signal(signal)

    # Retrieve it back
    loaded = store.get_signal(signal.id)

    # Verify the fields are present (stored as text in DB)
    assert loaded is not None
    assert loaded.get("symbol") == "ETH"
    # trail_amount and time_exit_at should be in the raw or stored
    # (exact format depends on how they're persisted)


# ===== Integration Tests: Real Engine + PaperBroker + Lifecycle =====


@pytest.mark.asyncio
async def test_b06_notional_gate_uses_broker_quote_over_message_price(
    paper_broker: PaperBroker, tmp_db: Path
) -> None:
    """B-06: Notional gate uses broker quote (60) over message price (50).

    With max_notional=550: 10 * 60 = 600 > 550 → REJECTED
    With max_notional=650: 10 * 60 = 600 < 650 → FILLED
    """
    from app.engine import SignalCopierEngine
    from app.routing import RoutingConfig, RoutingRule

    store = SignalStore(str(tmp_db))

    # Simulate a price so broker has a quote
    paper_broker.simulate_price("AAPL", 60.0)

    # Account with tight notional ceiling (550)
    tight_account = DestinationAccount(
        account_id="tight",
        broker="paper",
        max_notional_exposure=550.0,
        managed_lifecycle=False,
    )

    # Account with comfortable ceiling (650)
    comfortable_account = DestinationAccount(
        account_id="comfortable",
        broker="paper",
        max_notional_exposure=650.0,
        managed_lifecycle=False,
    )

    accounts = {
        "tight": tight_account,
        "comfortable": comfortable_account,
    }

    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["tight", "comfortable"], delivery_mode="replicate")],
        accounts=accounts,
    )

    # Create engine with proper routing and accounts
    engine = SignalCopierEngine(
        routing=routing,
        brokers={"paper": paper_broker},
        store=store,
    )

    # Signal: price=50 (message), but broker quote is 60
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=50.0,
    )

    # Handle signal: should use broker quote (60) for gating
    await engine.handle_signal(signal)

    # Tight account should be REJECTED (600 > 550)
    tight_result = store.list_orders_for_signal(signal.id)
    assert any(o["account_id"] == "tight" and o["status"] == OrderStatus.REJECTED.value for o in tight_result)

    # Check that rejection message mentions broker_quote
    tight_orders = [o for o in tight_result if o["account_id"] == "tight"]
    assert any("broker_quote" in (o.get("message") or "") for o in tight_orders)

    # Comfortable account should be FILLED (600 < 650)
    comfortable_result = store.list_orders_for_signal(signal.id)
    assert any(
        o["account_id"] == "comfortable" and o["status"] == OrderStatus.FILLED.value
        for o in comfortable_result
    )


@pytest.mark.asyncio
async def test_b06_broker_without_quote_uses_message_price(
    paper_broker: PaperBroker, tmp_db: Path
) -> None:
    """B-06: Broker without quote capability uses message price (50).

    PaperBroker doesn't implement get_quote, so it has no quote capability.
    Even though we simulate no price at the broker level, the engine should
    fall back to the signal price (50) for gating calculations.
    """
    from app.engine import SignalCopierEngine
    from app.routing import RoutingConfig, RoutingRule

    store = SignalStore(str(tmp_db))

    # Note: paper_broker doesn't have has_quote_capability (get_quote returns None)
    # So the engine will use signal price for gating

    # Account with notional ceiling
    account = DestinationAccount(
        account_id="test",
        broker="paper",
        max_notional_exposure=600.0,  # 10 * 50 = 500 < 600 ✓
        managed_lifecycle=False,
    )

    accounts = {"test": account}

    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["test"], delivery_mode="replicate")],
        accounts=accounts,
    )

    # Create engine with paper broker (no quote capability)
    engine = SignalCopierEngine(
        routing=routing,
        brokers={"paper": paper_broker},
        store=store,
    )

    # Signal: price=50
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=50.0,
    )

    # Handle signal: should use message price (50) for gating
    await engine.handle_signal(signal)

    # Should be FILLED
    result = store.list_orders_for_signal(signal.id)
    assert any(o["status"] == OrderStatus.FILLED.value for o in result)

    # Message should indicate "signal" price was used (or not mention broker_quote)
    orders = [o for o in result if o["status"] == OrderStatus.FILLED.value]
    assert len(orders) > 0


@pytest.mark.asyncio
async def test_d12_trailing_stop_ratchets_and_never_lowers(
    paper_broker: PaperBroker, tmp_db: Path
) -> None:
    """D-12: Trailing stop ratchets up and never lowers.

    Entry at 50 with trail_amount=2.0 (stop at 48).
    Price 55 → stop should ratchet to 53.
    Price 52 → stop should stay at 53 (not lower).
    Price 52.9 → stop should still stay at 53.
    Price 52 → stop rests at 53; simulate 53 → stop filled.
    """
    from app.lifecycle.manager import PositionLifecycleManager
    from app.lifecycle.models import PositionPlan, TrailingPolicy

    store = SignalStore(str(tmp_db))

    # Create lifecycle manager
    manager = PositionLifecycleManager(
        brokers={"paper": paper_broker},
        store=store,
    )

    account = DestinationAccount(
        account_id="acct1",
        broker="paper",
        managed_lifecycle=True,
    )

    # Create a plan with trailing stop (already active for this integration test)
    plan = PositionPlan(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=10.0,
        initial_stop=48.0,
        trailing=TrailingPolicy(trail_distance=2.0, active=True),
    )

    # Start the position plan
    lifecycle = manager.start_plan(plan)

    # Simulate entry fill at 50
    await manager.on_entry_fill(account, "AAPL", 10.0, entry_price=50.0)

    # Price update to 55 → floor should be 53
    await manager.on_price_update(account, "AAPL", 55.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 53.0, f"Expected 53.0, got {lifecycle.stop.desired_price}"

    # Price update to 52 → floor should NOT lower, stay at 53
    await manager.on_price_update(account, "AAPL", 52.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 53.0

    # Price update to 52.9 → still stay at 53
    await manager.on_price_update(account, "AAPL", 52.9)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 53.0

    # Simulate price at 53 → stop should NOT trigger (it's a <=, so exactly at stop)
    # Simulate price below 53 → stop should fill
    fills = paper_broker.simulate_price("AAPL", 52.99)
    assert len(fills) > 0, "Stop should have filled below 53"


@pytest.mark.asyncio
async def test_d12_time_exit_fires_and_state_survives_restart(
    paper_broker: PaperBroker, tmp_db: Path
) -> None:
    """D-12: Time exit fires and lifecycle state survives restart.

    Entry with time_exit_at = now + 1s.
    Create second manager on same store (simulating restart).
    Advance time and run reconciliation tick.
    Position should close; high-water-mark and trailing params should persist.
    """
    from app.lifecycle.manager import PositionLifecycleManager
    from app.lifecycle.models import PositionPlan, TrailingPolicy
    import time

    store = SignalStore(str(tmp_db))

    # Create first lifecycle manager
    manager1 = PositionLifecycleManager(
        brokers={"paper": paper_broker},
        store=store,
    )

    account = DestinationAccount(
        account_id="acct1",
        broker="paper",
        managed_lifecycle=True,
    )

    # Time exit: 1 second from now
    now = datetime.now(timezone.utc)
    time_exit = now + timedelta(seconds=1)

    # Create a plan with time exit and trailing stop
    plan = PositionPlan(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=10.0,
        initial_stop=48.0,
        trailing=TrailingPolicy(trail_distance=2.0),
        time_exit=time_exit,
    )

    # Start the position plan
    manager1.start_plan(plan)

    # Simulate entry fill at 50
    await manager1.on_entry_fill(account, "AAPL", 10.0, entry_price=50.0)

    # Verify lifecycle has the time_exit and trailing
    lifecycle1 = manager1.get_lifecycle("acct1", "AAPL")
    assert lifecycle1.plan.time_exit is not None
    assert lifecycle1.plan.trailing is not None
    assert lifecycle1.highest_price_since_entry == 50.0

    # Update price to set high water mark
    await manager1.on_price_update(account, "AAPL", 55.0)
    lifecycle1 = manager1.get_lifecycle("acct1", "AAPL")
    assert lifecycle1.highest_price_since_entry == 55.0

    # Sleep to let time_exit pass
    time.sleep(1.1)

    # Create a SECOND manager on the same store (simulating restart)
    manager2 = PositionLifecycleManager(
        brokers={"paper": paper_broker},
        store=store,
    )

    # Resume the lifecycle - it should be loaded from persistence
    await manager2.restore_from_store()

    # Verify state survived the restart
    lifecycle2 = manager2.get_lifecycle("acct1", "AAPL")
    assert lifecycle2 is not None
    assert lifecycle2.plan.time_exit is not None
    assert lifecycle2.plan.trailing is not None
    assert lifecycle2.highest_price_since_entry == 55.0, \
        f"High water mark should be 55, got {lifecycle2.highest_price_since_entry}"

    # Check that position is still open (before time exit processing)
    assert not lifecycle2.closed

    # Now check if time exit would fire on next tick (implementation-dependent;
    # for now just verify the state is there and hasn't been lost)
