"""WP-27: E-07/E-08 - Real fill timestamps and excluded synthetic latency.

E-07: Reconciliation-confirmed fills now preserve broker's actual executed_at
timestamp rather than overwriting with the poll time. A separate confirmed_at
column tracks when the fill was reconciled.

E-08: Synthetic lifecycle signals (stop_exit, target_exit, time_exit) and
lifecycle_manager-sourced signals are excluded from latency calculations to
prevent managed exit latency from diluting provider-signal metrics.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.execution_quality import compute_execution_quality
from app.models import Signal, OrderResult, OrderStatus, DestinationAccount, Side


@pytest.fixture
def db(tmp_path: Path) -> SignalStore:
    """Create a real SignalStore on tmp_path."""
    db_path = tmp_path / "test.db"
    store = SignalStore(str(db_path))
    return store


@pytest.fixture
def account(db: SignalStore) -> DestinationAccount:
    """Create a test account."""
    account = DestinationAccount(
        account_id="test_account",
        broker="paper",
    )
    return account


@pytest.fixture
def broker() -> PaperBroker:
    """Create a PaperBroker for testing."""
    return PaperBroker()


def test_e07_preserves_broker_executed_at(db: SignalStore, account: DestinationAccount, broker: PaperBroker):
    """E-07: executed_at should preserve broker's actual fill timestamp."""
    # Create a signal with a known received_at time
    received_at = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    signal = Signal(
        id="sig_001",
        source="webhook",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        received_at=received_at,
    )
    db.save_signal(signal)
    signal_id = signal.id

    # Create an OrderResult with a specific executed_at (broker's actual fill time)
    # This simulates what an adapter would return when polling for a fill
    executed_at = datetime(2026, 10, 1, 10, 0, 5, tzinfo=timezone.utc)  # 5 seconds after received
    result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=signal_id,
        broker_order_id="order_123",
        filled_quantity=100.0,
        filled_price=150.25,
        message="Filled",
        executed_at=executed_at,  # Real broker timestamp
    )

    # Save the order result
    order_id = db.save_order_result(
        result,
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        requested_quantity=100.0,
    )

    # Verify that executed_at was saved correctly
    with db._connect() as conn:
        row = conn.execute(
            "SELECT executed_at, confirmed_at FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

    assert row is not None
    assert row[0] == executed_at.isoformat()
    # confirmed_at should NOT be set at insertion time (only during reconciliation)
    assert row[1] is None


def test_e07_preserves_executed_at_on_reconciliation_update(db: SignalStore, account: DestinationAccount):
    """E-07: Reconciliation should preserve existing executed_at and set confirmed_at."""
    # Create and save a signal
    signal = Signal(
        id="sig_002",
        source="webhook",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
    )
    db.save_signal(signal)
    signal_id = signal.id

    # Create initial order result without executed_at (PENDING order)
    initial_result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.PENDING,
        signal_id=signal_id,
        broker_order_id="order_456",
        message="Submitted",
    )

    order_id = db.save_order_result(
        initial_result,
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        requested_quantity=100.0,
    )

    # Later, reconciliation polls and gets a FILLED response with broker's actual fill time
    broker_executed_at = datetime(2026, 10, 1, 10, 0, 12, tzinfo=timezone.utc)
    filled_result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=signal_id,
        broker_order_id="order_456",
        filled_quantity=100.0,
        filled_price=150.50,
        message="Filled",
        executed_at=broker_executed_at,  # Real broker fill time
    )

    # Update order status with the filled result
    with db._connect() as conn:
        db._update_order_status_locked(
            conn,
            order_id,
            filled_result,
        )

    # Verify that executed_at was set to the broker's timestamp
    with db._connect() as conn:
        row = conn.execute(
            "SELECT executed_at, confirmed_at FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

    assert row is not None
    assert row[0] == broker_executed_at.isoformat()
    # confirmed_at should be set during reconciliation
    assert row[1] is not None


def test_e07_does_not_overwrite_preserved_executed_at(db: SignalStore, account: DestinationAccount):
    """E-07: Reconciliation should not overwrite an already-set executed_at."""
    # Create and save a signal
    signal = Signal(
        id="sig_003",
        source="webhook",
        symbol="BTC/USD",
        side=Side.BUY,
        quantity=1.0,
    )
    db.save_signal(signal)
    signal_id = signal.id

    # First reconciliation sets the executed_at from broker
    initial_executed_at = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    initial_result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=signal_id,
        broker_order_id="order_789",
        filled_quantity=1.0,
        filled_price=42000.0,
        message="Filled",
        executed_at=initial_executed_at,
    )

    order_id = db.save_order_result(
        initial_result,
        broker="paper",
        symbol="BTC/USD",
        side=Side.BUY,
        requested_quantity=1.0,
    )

    # Second reconciliation poll (at a different time) should preserve the original executed_at
    second_result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=signal_id,
        broker_order_id="order_789",
        filled_quantity=1.0,
        filled_price=42000.0,
        message="Filled",
        executed_at=initial_executed_at,  # Same as original (broker sends same timestamp)
    )

    # This simulates a reconciliation poll at a later time
    with db._connect() as conn:
        db._update_order_status_locked(conn, order_id, second_result)

    # Verify that executed_at wasn't overwritten with a new poll time
    with db._connect() as conn:
        row = conn.execute(
            "SELECT executed_at FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

    assert row is not None
    assert row[0] == initial_executed_at.isoformat()


def test_e08_excludes_synthetic_lifecycle_signals_from_latency(db: SignalStore, account: DestinationAccount):
    """E-08: Synthetic lifecycle signals should not be included in latency calculations."""
    # Create a provider signal (should be included in latency)
    provider_signal = Signal(
        id="sig_provider_001",
        source="webhook",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        received_at=datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.save_signal(provider_signal)
    provider_signal_id = provider_signal.id

    # Create an entry order from the provider signal
    provider_result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=provider_signal_id,
        broker_order_id="entry_001",
        filled_quantity=100.0,
        filled_price=150.0,
        executed_at=datetime(2026, 10, 1, 10, 0, 2, tzinfo=timezone.utc),
    )

    db.save_order_result(
        provider_result,
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        requested_quantity=100.0,
        purpose="entry",
    )

    # Create a synthetic stop_exit signal (should be EXCLUDED from latency)
    stop_signal = Signal(
        id="sig_stop_001",
        source="lifecycle_manager",
        symbol="AAPL",
        side=Side.SELL,
        quantity=100.0,
        received_at=datetime(2026, 10, 1, 10, 0, 2, tzinfo=timezone.utc),  # microseconds before exit
    )
    db.save_signal(stop_signal)
    stop_signal_id = stop_signal.id

    # Create a stop exit order
    stop_result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=stop_signal_id,
        broker_order_id="stop_001",
        filled_quantity=100.0,
        filled_price=149.50,
        executed_at=datetime(2026, 10, 1, 10, 0, 2, tzinfo=timezone.utc),
    )

    db.save_order_result(
        stop_result,
        broker="paper",
        symbol="AAPL",
        side=Side.SELL,
        requested_quantity=100.0,
        purpose="stop_exit",
    )

    # Compute execution quality
    quality = compute_execution_quality(db, account.account_id)

    # The latency should only include the provider signal, not the synthetic stop_exit
    assert "AAPL" in quality.per_symbol
    latency_info = quality.per_symbol["AAPL"]

    # Should have exactly 1 sample (the provider signal), not 2
    assert latency_info.sample_count == 1
    # Latency should be ~2 seconds (10:00:02 - 10:00:00)
    assert 1.5 < latency_info.mean_seconds < 2.5


def test_e08_excludes_lifecycle_manager_source_signals(db: SignalStore, account: DestinationAccount):
    """E-08: Signals from lifecycle_manager source should be excluded from latency."""
    # Create a real provider signal
    provider_signal = Signal(
        id="sig_real_001",
        source="webhook",
        symbol="MSFT",
        side=Side.BUY,
        quantity=50.0,
        received_at=datetime(2026, 10, 1, 11, 0, 0, tzinfo=timezone.utc),
    )
    db.save_signal(provider_signal)
    provider_signal_id = provider_signal.id

    provider_result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=provider_signal_id,
        broker_order_id="order_msft_001",
        filled_quantity=50.0,
        filled_price=300.0,
        executed_at=datetime(2026, 10, 1, 11, 0, 3, tzinfo=timezone.utc),
    )

    db.save_order_result(
        provider_result,
        broker="paper",
        symbol="MSFT",
        side=Side.BUY,
        requested_quantity=50.0,
        purpose="entry",
    )

    # Create a target_exit signal from lifecycle_manager
    target_signal = Signal(
        id="sig_target_001",
        source="lifecycle_manager",
        symbol="MSFT",
        side=Side.SELL,
        quantity=50.0,
        received_at=datetime(2026, 10, 1, 11, 0, 10, tzinfo=timezone.utc),
    )
    db.save_signal(target_signal)
    target_signal_id = target_signal.id

    target_result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=target_signal_id,
        broker_order_id="order_msft_target_001",
        filled_quantity=50.0,
        filled_price=305.0,
        executed_at=datetime(2026, 10, 1, 11, 0, 11, tzinfo=timezone.utc),
    )

    db.save_order_result(
        target_result,
        broker="paper",
        symbol="MSFT",
        side=Side.SELL,
        requested_quantity=50.0,
        purpose="target_exit",
    )

    # Compute execution quality
    quality = compute_execution_quality(db, account.account_id)

    # Should only count the real provider signal
    assert "MSFT" in quality.per_symbol
    latency_info = quality.per_symbol["MSFT"]
    assert latency_info.sample_count == 1
    # Latency should be ~3 seconds
    assert 2.5 < latency_info.mean_seconds < 3.5


def test_e07_confirmed_at_tracks_reconciliation_poll_time(db: SignalStore, account: DestinationAccount):
    """E-07: confirmed_at should track when the fill was confirmed during reconciliation."""
    # Create a signal
    signal = Signal(
        id="sig_004",
        source="webhook",
        symbol="GOOG",
        side=Side.BUY,
        quantity=50.0,
    )
    db.save_signal(signal)
    signal_id = signal.id

    # Create order result with real executed_at (from broker)
    actual_fill_time = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
    result = OrderResult(
        account_id=account.account_id,
        status=OrderStatus.FILLED,
        signal_id=signal_id,
        broker_order_id="order_goog_001",
        filled_quantity=50.0,
        filled_price=140.5,
        executed_at=actual_fill_time,
    )

    order_id = db.save_order_result(
        result,
        broker="paper",
        symbol="GOOG",
        side=Side.BUY,
        requested_quantity=50.0,
    )

    # Now simulate reconciliation happening at a different time
    # Reconciliation will be checked via confirmed_at timestamp
    # Mock time for confirmed_at (in real code this would be datetime.now())
    with db._connect() as conn:
        # Get current row
        row = conn.execute(
            "SELECT executed_at FROM orders WHERE id = ?", (order_id,)
        ).fetchone()

        # Verify initial state
        assert row[0] == actual_fill_time.isoformat()

        # Update to set confirmed_at
        reconciliation_result = OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal_id,
            broker_order_id="order_goog_001",
            filled_quantity=50.0,
            filled_price=140.5,
            executed_at=actual_fill_time,  # Same as broker reported
        )

        db._update_order_status_locked(conn, order_id, reconciliation_result)

    # Verify confirmed_at was set
    with db._connect() as conn:
        row = conn.execute(
            "SELECT executed_at, confirmed_at FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

    assert row is not None
    # executed_at should remain the broker's actual time
    assert row[0] == actual_fill_time.isoformat()
    # confirmed_at should be set to the reconciliation poll time (approximately now)
    assert row[1] is not None
    confirmed_at = datetime.fromisoformat(row[1])
    # Should be approximately now (within a second due to test execution time)
    assert abs((datetime.now(timezone.utc) - confirmed_at).total_seconds()) < 1
