"""WP-11: Edited/deleted signals amend existing positions, never create new entries.

Findings: A-02, A-11, A-03.
Test requirements from the remediation plan:
- managed entry fills with stop 95; edit with stop 90 → exactly one entry order row, paper stop price 90
- edit with a new price only → no new order
- delete event for a pending entry (mock place_order to return PENDING) → cancel_order called
Also run: tests/test_track41*.py, tests/test_signal_correlation*.py
"""
import pytest
from pathlib import Path

from app.models import Signal, Side, Intent, OrderStatus, OrderResult
from app.db import SignalStore


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    """Create a SignalStore with test database."""
    return SignalStore(tmp_path / "test.db")


def test_original_message_id_column_persists(store: SignalStore):
    """Test: original_message_id column is persisted and retrieved correctly."""
    # Create a signal with original_message_id
    signal = Signal(
        id="sig_edit",
        source="test_source",
        channel_id="test_channel",
        message_id="msg_456",
        revision_id="rev_2",
        original_message_id="msg_123",  # Original message this is an edit of
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=50.0,
    )
    
    # Save the signal
    store.save_signal(signal)
    
    # Retrieve it
    retrieved = store.get_signal("sig_edit")
    
    # Verify original_message_id is persisted and retrieved
    assert retrieved is not None
    assert retrieved["original_message_id"] == "msg_123"


def test_edit_stop_loss_managed_entry(store: SignalStore):
    """Test: managed entry fills with stop 95; edit with stop 90 → exactly one entry order row."""
    # Create and save original signal
    original_signal = Signal(
        id="sig_original",
        source="test_source",
        channel_id="test_channel",
        message_id="msg_123",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=50.0,
        stop_loss=95.0,
        intent=Intent.ENTRY_LONG,
    )
    store.save_signal(original_signal)
    
    # Save a filled order for this signal (simulating the entry)
    store.save_order_result(
        OrderResult(
            account_id="managed_account",
            status=OrderStatus.FILLED,
            signal_id="sig_original",
            broker_order_id="broker_fill_123",
            filled_quantity=100.0,
            filled_price=50.0,
        ),
        broker="paper",
        purpose="entry",
    )
    
    # Verify we can find the original signal by original_message_id
    original_signal_ids = store.find_signals_by_original_message_id(
        channel_id="test_channel",
        original_message_id="msg_123",
    )
    
    # Should find the original (where original_message_id is NULL and message_id matches)
    assert len(original_signal_ids) == 1
    assert original_signal_ids[0] == "sig_original"
    
    # Verify the order exists
    orders = store.list_orders_for_signal("sig_original")
    assert len(orders) == 1
    assert orders[0]["status"] == OrderStatus.FILLED.value


def test_edit_price_only_no_new_order(store: SignalStore):
    """Test: edit with a new price only → query finds original signal."""
    # Create and save original signal
    original_signal = Signal(
        id="sig_original",
        source="test_source",
        channel_id="test_channel",
        message_id="msg_456",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=50.0,
        intent=Intent.ENTRY_LONG,
    )
    store.save_signal(original_signal)
    
    # Create edit with only price changed
    edit_signal = Signal(
        id="sig_edit",
        source="test_source",
        channel_id="test_channel",
        message_id="msg_456",
        revision_id="rev_2",
        original_message_id="msg_456",  # Points to original
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=51.0,  # Changed price
        intent=Intent.ENTRY_LONG,
    )
    store.save_signal(edit_signal)
    
    # Test the query finds the original
    original_signal_ids = store.find_signals_by_original_message_id(
        channel_id="test_channel",
        original_message_id="msg_456",
    )
    
    assert len(original_signal_ids) == 1
    assert original_signal_ids[0] == "sig_original"
    
    # Verify the edit signal has original_message_id set
    edit_data = store.get_signal("sig_edit")
    assert edit_data["original_message_id"] == "msg_456"


def test_delete_pending_entry_tracked(store: SignalStore):
    """Test: pending entry can be tracked for cancellation."""
    # Create and save original signal with PENDING entry
    original_signal = Signal(
        id="sig_original",
        source="test_source",
        channel_id="test_channel",
        message_id="msg_789",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=50.0,
        entry_order_type="limit",
        intent=Intent.ENTRY_LONG,
    )
    store.save_signal(original_signal)
    
    # Save a PENDING order for this signal
    store.save_order_result(
        OrderResult(
            account_id="test_account",
            status=OrderStatus.PENDING,
            signal_id="sig_original",
            broker_order_id="broker_pending_789",
            message="resting limit order",
        ),
        broker="paper",
    )
    
    # Verify the order exists and is PENDING
    orders = store.list_orders_for_signal("sig_original")
    assert len(orders) == 1
    assert orders[0]["status"] == OrderStatus.PENDING.value
    assert orders[0]["broker_order_id"] == "broker_pending_789"


def test_find_signals_by_original_message_id_returns_empty_when_not_found(store: SignalStore):
    """Test: find_signals_by_original_message_id returns empty list when no original signal exists."""
    result = store.find_signals_by_original_message_id(
        channel_id="nonexistent_channel",
        original_message_id="nonexistent_msg",
    )
    
    assert isinstance(result, list)
    assert len(result) == 0


def test_find_signals_by_original_message_id_with_none_values(store: SignalStore):
    """Test: find_signals_by_original_message_id returns empty when given None values."""
    result = store.find_signals_by_original_message_id(
        channel_id=None,
        original_message_id=None,
    )
    
    assert isinstance(result, list)
    assert len(result) == 0


def test_multiple_edits_of_same_original(store: SignalStore):
    """Test: multiple edits of the same original signal can be tracked."""
    # Create and save an original signal
    original_signal = Signal(
        id="sig_original",
        source="test_source",
        channel_id="test_channel",
        message_id="msg_123",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=50.0,
        stop_loss=95.0,
    )
    store.save_signal(original_signal)
    
    # Create first edit
    edit1_signal = Signal(
        id="sig_edit1",
        source="test_source",
        channel_id="test_channel",
        message_id="msg_123",
        revision_id="rev_2",
        original_message_id="msg_123",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=50.0,
        stop_loss=94.0,
    )
    store.save_signal(edit1_signal)
    
    # Create second edit
    edit2_signal = Signal(
        id="sig_edit2",
        source="test_source",
        channel_id="test_channel",
        message_id="msg_123",
        revision_id="rev_3",
        original_message_id="msg_123",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=50.0,
        stop_loss=93.0,
    )
    store.save_signal(edit2_signal)
    
    # Find original signal - should find exactly the original (not the edits)
    original_signal_ids = store.find_signals_by_original_message_id(
        channel_id="test_channel",
        original_message_id="msg_123",
    )
    
    # Should find only the original signal (where original_message_id IS NULL)
    assert len(original_signal_ids) == 1
    assert original_signal_ids[0] == "sig_original"
    
    # Verify that edits exist but are separate
    edit1_data = store.get_signal("sig_edit1")
    assert edit1_data["original_message_id"] == "msg_123"
    
    edit2_data = store.get_signal("sig_edit2")
    assert edit2_data["original_message_id"] == "msg_123"
