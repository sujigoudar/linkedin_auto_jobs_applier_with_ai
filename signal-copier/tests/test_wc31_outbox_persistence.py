"""WC-31: Durable order intents and outbox on SignalStore.

Tests the persistence of OrderIntent and OutboxItem to the database,
and the recovery mechanisms per spec §6.3.

Implements:
- insert_order_intent_and_outbox atomicity and uniqueness
- claim_next_outbox_item FIFO ordering and concurrency
- record_outbox_response state classification
- recover_outbox_on_restart handling of ambiguous items
- Crash scenarios: before claim, after claim before dispatch, after dispatch
"""
import json
import sqlite3
import threading
from datetime import datetime
import pytest

from app.db import SignalStore
from app.workflow.intents import (
    IntentState,
    OrderIntent,
    OutboxItem,
    Outbox,
)


class TestInsertOrderIntentAndOutbox:
    """Test insert_order_intent_and_outbox atomicity and uniqueness."""

    def test_insert_order_intent_and_outbox_persists_both_rows(self, tmp_path):
        """Verify both rows are inserted in one atomic transaction."""
        store = SignalStore(tmp_path / "test.db")

        intent = OrderIntent.create(
            opportunity_id="opp_001",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            price_constraints={"entry": 50.0},
            protection_recipe={"stop_loss_pct": 10},
            reservation_id="res_001",
        )

        outbox = Outbox(store)
        item = outbox.enqueue(intent)

        assert isinstance(item, OutboxItem)
        assert item.intent_id == intent.intent_id
        assert item.state == IntentState.OUTBOXED

        # Verify order_intents row was persisted
        fetched_intent = store.get_order_intent(intent.intent_id)
        assert fetched_intent is not None
        assert fetched_intent.intent_id == intent.intent_id
        assert fetched_intent.opportunity_id == "opp_001"
        assert fetched_intent.quantity == 100
        assert fetched_intent.price_constraints == {"entry": 50.0}
        assert fetched_intent.protection_recipe == {"stop_loss_pct": 10}

        # Verify outbox row was persisted
        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item is not None
        assert fetched_item.state == IntentState.OUTBOXED
        assert fetched_item.claimed_at is None
        assert fetched_item.claimed_by is None

    def test_insert_order_intent_and_outbox_price_constraints_none(self, tmp_path):
        """Verify None price_constraints are stored as NULL."""
        store = SignalStore(tmp_path / "test.db")

        intent = OrderIntent.create(
            opportunity_id="opp_002",
            physical_account_id="acc_002",
            binding_id="bind_002",
            client_correlation_id="corr_002",
            policy_hash="hash_002",
            quantity=50,
            price_constraints=None,
            protection_recipe=None,
            reservation_id="res_002",
        )

        outbox = Outbox(store)
        outbox.enqueue(intent)

        fetched_intent = store.get_order_intent(intent.intent_id)
        assert fetched_intent.price_constraints is None
        assert fetched_intent.protection_recipe is None

    def test_insert_order_intent_and_outbox_duplicate_opportunity_raises(self, tmp_path):
        """Verify duplicate opportunity_id raises IntegrityError."""
        store = SignalStore(tmp_path / "test.db")

        intent1 = OrderIntent.create(
            opportunity_id="opp_duplicate",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )

        outbox = Outbox(store)
        outbox.enqueue(intent1)

        # Try to insert second intent with same opportunity_id
        intent2 = OrderIntent.create(
            opportunity_id="opp_duplicate",
            physical_account_id="acc_002",
            binding_id="bind_002",
            client_correlation_id="corr_002",
            policy_hash="hash_002",
            quantity=200,
            reservation_id="res_002",
        )

        with pytest.raises(sqlite3.IntegrityError):
            outbox.enqueue(intent2)

        # Verify only one intent exists
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM order_intents WHERE opportunity_id = ?",
                ("opp_duplicate",),
            ).fetchone()
            assert rows[0] == 1

    def test_insert_order_intent_and_outbox_quantity_zero_allowed(self, tmp_path):
        """Verify quantity=0 is accepted (zero is a valid position)."""
        store = SignalStore(tmp_path / "test.db")

        intent = OrderIntent.create(
            opportunity_id="opp_zero",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=0,
            reservation_id="res_001",
        )

        outbox = Outbox(store)
        item = outbox.enqueue(intent)
        assert item.intent_id == intent.intent_id

        fetched_intent = store.get_order_intent(intent.intent_id)
        assert fetched_intent.quantity == 0


class TestClaimNextOutboxItem:
    """Test claim_next_outbox_item FIFO ordering and concurrency."""

    def test_claim_next_empty_queue_returns_none(self, tmp_path):
        """Verify claim_next returns None on empty queue."""
        store = SignalStore(tmp_path / "test.db")
        outbox = Outbox(store)

        claimed = outbox.claim_next("worker_001")
        assert claimed is None

    def test_claim_next_fifo_by_created_at(self, tmp_path):
        """Verify claim_next returns oldest item by created_at."""
        store = SignalStore(tmp_path / "test.db")

        # Enqueue three items
        outbox = Outbox(store)
        intent1 = OrderIntent.create(
            opportunity_id="opp_1",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        item1 = outbox.enqueue(intent1)

        intent2 = OrderIntent.create(
            opportunity_id="opp_2",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_002",
        )
        item2 = outbox.enqueue(intent2)

        intent3 = OrderIntent.create(
            opportunity_id="opp_3",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_003",
        )
        item3 = outbox.enqueue(intent3)

        # Claim the first one (oldest by created_at)
        claimed1 = outbox.claim_next("worker_001")
        assert claimed1 is not None
        assert claimed1.item_id == item1.item_id
        assert claimed1.state == IntentState.DISPATCHING
        assert claimed1.claimed_by == "worker_001"
        assert claimed1.claimed_at is not None

        # Claim the second one
        claimed2 = outbox.claim_next("worker_002")
        assert claimed2 is not None
        assert claimed2.item_id == item2.item_id
        assert claimed2.state == IntentState.DISPATCHING
        assert claimed2.claimed_by == "worker_002"

        # Claim the third one
        claimed3 = outbox.claim_next("worker_003")
        assert claimed3 is not None
        assert claimed3.item_id == item3.item_id

        # Queue is now empty
        claimed4 = outbox.claim_next("worker_004")
        assert claimed4 is None

    def test_claim_next_concurrent_access_exactly_one_winner(self, tmp_path):
        """Verify two threads claiming the same item only one gets it."""
        store = SignalStore(tmp_path / "test.db")

        # Enqueue one item
        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_concurrent",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        # Two threads race to claim the same item
        results = []

        def claim_from_thread(worker_id: str) -> None:
            claimed = outbox.claim_next(worker_id)
            if claimed is not None:
                results.append((worker_id, claimed.item_id))

        t1 = threading.Thread(target=claim_from_thread, args=("worker_1",))
        t2 = threading.Thread(target=claim_from_thread, args=("worker_2",))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Only one thread should have claimed it
        assert len(results) == 1, f"Expected 1 winner, got {len(results)}: {results}"

    def test_claim_next_updates_timestamp_and_lease(self, tmp_path):
        """Verify claimed_at and claimed_by are set correctly."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_timestamp",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        before_claim = datetime.utcnow()
        claimed = outbox.claim_next("worker_lease_123")
        after_claim = datetime.utcnow()

        assert claimed.claimed_by == "worker_lease_123"
        assert claimed.claimed_at is not None
        # Note: comparing UTC times, allow some tolerance
        assert before_claim <= claimed.claimed_at.replace(tzinfo=None) <= after_claim


class TestRecordOutboxResponse:
    """Test record_outbox_response state classification."""

    def test_record_response_success_becomes_submitted(self, tmp_path):
        """Verify successful response is classified as 'submitted'."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_response",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        response = {"status": "submitted", "order_id": "ord_123"}
        outbox.record_response(intent.intent_id, response)

        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state == "submitted"
        assert fetched_item.response is not None
        assert json.loads(fetched_item.response) == response
        assert fetched_item.response_recorded_at is not None

    def test_record_response_error_becomes_unknown(self, tmp_path):
        """Verify error response is classified as 'unknown'."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_error",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        response = {"error": "Connection timeout", "code": 500}
        outbox.record_response(intent.intent_id, response)

        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state == "unknown"
        assert json.loads(fetched_item.response) == response

    def test_record_response_exception_becomes_unknown(self, tmp_path):
        """Verify exception response is classified as 'unknown'."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_exception",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        response = {"exception": "NetworkError", "message": "Broker unreachable"}
        outbox.record_response(intent.intent_id, response)

        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state == "unknown"

    def test_record_response_rejected_status(self, tmp_path):
        """Verify rejected status is classified as 'rejected'."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_rejected",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        response = {"status": "rejected", "reason": "Insufficient funds"}
        outbox.record_response(intent.intent_id, response)

        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state == "rejected"

    def test_record_response_nonexistent_intent_raises(self, tmp_path):
        """Verify recording response for nonexistent intent raises KeyError."""
        store = SignalStore(tmp_path / "test.db")
        outbox = Outbox(store)

        response = {"status": "submitted"}

        with pytest.raises(KeyError):
            outbox.record_response("nonexistent_intent_id", response)


class TestRecoverOutboxOnRestart:
    """Test recover_outbox_on_restart handling of ambiguous items."""

    def test_recover_marks_claimed_unresponded_as_unknown(self, tmp_path):
        """Verify claimed-but-unresponded items are marked 'unknown' on restart."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_ambiguous",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        # Claim the item (sets state to 'dispatching')
        claimed = outbox.claim_next("worker_001")
        assert claimed.state == IntentState.DISPATCHING
        assert claimed.response_recorded_at is None

        # Simulate a restart (without recording response)
        recovery = store.recover_outbox_on_restart()

        # Should mark the claimed-unresponded item as 'unknown'
        assert claimed.item_id in recovery["marked_unknown"]

        # Verify it was marked
        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state == "unknown"
        assert json.loads(fetched_item.response) == {"reason": "restart_before_response"}

    def test_recover_does_not_touch_outboxed_items(self, tmp_path):
        """Verify unclaimed OUTBOXED items are left alone."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent1 = OrderIntent.create(
            opportunity_id="opp_unclaimed_1",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        item1 = outbox.enqueue(intent1)

        intent2 = OrderIntent.create(
            opportunity_id="opp_unclaimed_2",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_002",
        )
        item2 = outbox.enqueue(intent2)

        recovery = store.recover_outbox_on_restart()

        # Both should still be in outboxed_pending
        assert item1.item_id in recovery["outboxed_pending"]
        assert item2.item_id in recovery["outboxed_pending"]

        # Neither should be in marked_unknown
        assert item1.item_id not in recovery["marked_unknown"]
        assert item2.item_id not in recovery["marked_unknown"]

    def test_recover_does_not_touch_responded_items(self, tmp_path):
        """Verify items with recorded responses are left alone."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_responded",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        # Claim and then record response
        outbox.claim_next("worker_001")
        response = {"status": "submitted", "order_id": "ord_456"}
        outbox.record_response(intent.intent_id, response)

        recovery = store.recover_outbox_on_restart()

        # Should NOT be marked as unknown
        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state != "unknown"
        assert intent.intent_id not in recovery["marked_unknown"]

    def test_recover_returns_both_lists(self, tmp_path):
        """Verify recover returns both marked_unknown and outboxed_pending lists."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)

        # Create three items
        intent1 = OrderIntent.create(
            opportunity_id="opp_1",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent1)

        intent2 = OrderIntent.create(
            opportunity_id="opp_2",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_002",
        )
        outbox.enqueue(intent2)

        intent3 = OrderIntent.create(
            opportunity_id="opp_3",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_003",
        )
        outbox.enqueue(intent3)

        # Claim oldest (intent1) but don't respond
        claimed_oldest = outbox.claim_next("worker_001")

        # Claim next oldest (intent2) and respond to it
        claimed_second = outbox.claim_next("worker_002")
        outbox.record_response(claimed_second.intent_id, {"status": "submitted"})

        # intent3 remains unclaimed (outboxed)

        recovery = store.recover_outbox_on_restart()

        # claimed_oldest should be marked_unknown (claimed but not responded)
        assert claimed_oldest.item_id in recovery["marked_unknown"]

        # intent3 should be in outboxed_pending (never claimed)
        intent3_item = store.get_outbox_item_for_intent(intent3.intent_id)
        assert intent3_item.item_id in recovery["outboxed_pending"]

        # claimed_second should not be in marked_unknown (response was recorded)
        assert claimed_second.item_id not in recovery["marked_unknown"]


class TestCrashScenarios:
    """Test crash recovery scenarios per spec §6.3."""

    def test_crash_scenario_a_before_claim_item_still_outboxed(self, tmp_path):
        """Scenario (a): crash before claim → item still OUTBOXED and claimable."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_scenario_a",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        # Simulate crash (no claim made, no response recorded)
        # On restart, the item should still be OUTBOXED
        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state == IntentState.OUTBOXED
        assert fetched_item.claimed_at is None

        # Should be claimable on restart
        recovery = store.recover_outbox_on_restart()
        assert intent.intent_id not in recovery["marked_unknown"]
        assert fetched_item.item_id in recovery["outboxed_pending"]

    def test_crash_scenario_b_after_claim_before_dispatch_marked_unknown(self, tmp_path):
        """Scenario (b): crash after claim before dispatch → marked UNKNOWN on restart."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_scenario_b",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        # Claim but don't record response (simulates crash after claim)
        claimed = outbox.claim_next("worker_001")
        assert claimed.state == IntentState.DISPATCHING

        # Simulate restart
        recovery = store.recover_outbox_on_restart()

        # Item should be marked UNKNOWN (ambiguous: broker call may have happened)
        assert claimed.item_id in recovery["marked_unknown"]
        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state == "unknown"

    def test_crash_scenario_c_after_dispatch_response_recorded(self, tmp_path):
        """Scenario (c): after dispatch, response recorded → no recovery action."""
        store = SignalStore(tmp_path / "test.db")

        outbox = Outbox(store)
        intent = OrderIntent.create(
            opportunity_id="opp_scenario_c",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            reservation_id="res_001",
        )
        outbox.enqueue(intent)

        # Claim, record response (complete workflow)
        outbox.claim_next("worker_001")
        response = {"status": "submitted", "order_id": "ord_789"}
        outbox.record_response(intent.intent_id, response)

        # Simulate restart
        recovery = store.recover_outbox_on_restart()

        # Item should NOT be marked unknown (response was recorded)
        assert intent.intent_id not in recovery["marked_unknown"]
        fetched_item = store.get_outbox_item_for_intent(intent.intent_id)
        assert fetched_item.state == "submitted"


class TestBoundaryConditions:
    """Test boundary conditions and edge cases."""

    def test_large_json_price_constraints(self, tmp_path):
        """Verify large JSON objects in price_constraints are handled."""
        store = SignalStore(tmp_path / "test.db")

        large_constraints = {
            "entry": 50.0,
            "stop": 45.0,
            "take_profit": 60.0,
            "scale_in": [{"level": 0.1, "price": 48.0}, {"level": 0.2, "price": 46.0}],
        }

        intent = OrderIntent.create(
            opportunity_id="opp_large_json",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            price_constraints=large_constraints,
            reservation_id="res_001",
        )

        outbox = Outbox(store)
        outbox.enqueue(intent)

        fetched_intent = store.get_order_intent(intent.intent_id)
        assert fetched_intent.price_constraints == large_constraints

    def test_empty_dict_price_constraints(self, tmp_path):
        """Verify empty dict price_constraints are preserved."""
        store = SignalStore(tmp_path / "test.db")

        intent = OrderIntent.create(
            opportunity_id="opp_empty_dict",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=100,
            price_constraints={},
            reservation_id="res_001",
        )

        outbox = Outbox(store)
        outbox.enqueue(intent)

        fetched_intent = store.get_order_intent(intent.intent_id)
        assert fetched_intent.price_constraints == {}

    def test_negative_quantity_persisted(self, tmp_path):
        """Verify negative quantities can be persisted (validation is upstream)."""
        store = SignalStore(tmp_path / "test.db")

        intent = OrderIntent.create(
            opportunity_id="opp_negative_qty",
            physical_account_id="acc_001",
            binding_id="bind_001",
            client_correlation_id="corr_001",
            policy_hash="hash_001",
            quantity=-50,
            reservation_id="res_001",
        )

        outbox = Outbox(store)
        outbox.enqueue(intent)

        fetched_intent = store.get_order_intent(intent.intent_id)
        assert fetched_intent.quantity == -50

    def test_special_characters_in_strings(self, tmp_path):
        """Verify special characters in string fields are escaped properly."""
        store = SignalStore(tmp_path / "test.db")

        intent = OrderIntent.create(
            opportunity_id="opp_'special'\"chars",
            physical_account_id="acc_with_'quotes",
            binding_id="bind_with_\"double",
            client_correlation_id="corr_with_\\backslash",
            policy_hash="hash_with_😀_emoji",
            quantity=100,
            reservation_id="res_001",
        )

        outbox = Outbox(store)
        outbox.enqueue(intent)

        fetched_intent = store.get_order_intent(intent.intent_id)
        assert fetched_intent.opportunity_id == "opp_'special'\"chars"
        assert fetched_intent.physical_account_id == "acc_with_'quotes"
