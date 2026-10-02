"""Durable intents and outbox for SUBMISSION_UNKNOWN recovery (WC-06).

WC-06 creates this module with IntentState, OrderIntent, and Outbox.

Implements §6.3 (SQLite transaction and effect boundary): the transaction claims
the opportunity, validates versions, writes selection, reserves resources,
creates the immutable order intent and outbox item, then commits. A coordinated
account writer marks dispatching in a second transaction, performs the broker
call outside any transaction, then persists the response.

A crash at any boundary enters idempotent recovery:
- Before outbox claim: new submission on restart
- After outbox but before dispatch mark: restart skips broker call, holds reservation
- After dispatch mark but before response: restart skips broker call, waits for fill
- After response recorded: no action needed

SUBMISSION_UNKNOWN keeps the reservation and blocks conflicting new exposure on
that account/underlying (I05).
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


class IntentState(str, enum.Enum):
    """Order intent state machine from §6.3.

    A fresh intent starts at DRAFT. When enqueued to the outbox for dispatch,
    it moves to OUTBOXED. When claimed for dispatch, it becomes DISPATCHING.
    After the broker call completes (successfully or not), it transitions to
    SUBMITTED or UNKNOWN. As broker acknowledgements and fills arrive, it
    moves through ACKNOWLEDGED, FILLED, or REJECTED/EXPIRED.
    """

    DRAFT = "draft"
    OUTBOXED = "outboxed"
    DISPATCHING = "dispatching"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    UNKNOWN = "unknown"
    FILLED = "filled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class OrderIntent:
    """Immutable logical operation identifying an account/binding and its resource reservation.

    Fields:
        intent_id: Unique identifier for this intent (UUID)
        opportunity_id: Signal/scenario identifier (from signal or opportunity context)
        physical_account_id: The actual broker account this order targets
        binding_id: The config-account binding that authorized this physical account
        client_correlation_id: Broker-level idempotency key for the order
        policy_hash: Hash of the policy/limits under which sizing was computed
        quantity: Exact quantity in the instrument's step (never rounded up)
        price_constraints: dict with entry/exit/stop price specs (or None for market)
        protection_recipe: dict describing protective stops/targets (or None if none)
        reservation_id: Foreign key into budget_reservations (I08 constraint check)
    """

    intent_id: str
    opportunity_id: str
    physical_account_id: str
    binding_id: str
    client_correlation_id: str
    policy_hash: str
    quantity: int
    price_constraints: Optional[dict[str, Any]]
    protection_recipe: Optional[dict[str, Any]]
    reservation_id: str

    @classmethod
    def create(
        cls,
        opportunity_id: str,
        physical_account_id: str,
        binding_id: str,
        client_correlation_id: str,
        policy_hash: str,
        quantity: int,
        price_constraints: Optional[dict[str, Any]] = None,
        protection_recipe: Optional[dict[str, Any]] = None,
        reservation_id: Optional[str] = None,
        intent_id: Optional[str] = None,
    ) -> OrderIntent:
        """Create a fresh OrderIntent with a unique ID.

        Args:
            opportunity_id: Signal/scenario identifier
            physical_account_id: The actual broker account
            binding_id: Config-account binding identifier
            client_correlation_id: Broker idempotency key
            policy_hash: Hash of sizing policy
            quantity: Quantity in instrument step
            price_constraints: Price spec dict or None
            protection_recipe: Stop/target dict or None
            reservation_id: Budget reservation ID (optional at creation)
            intent_id: Existing intent ID (optional, for recovery)

        Returns:
            A new frozen OrderIntent instance.
        """
        return cls(
            intent_id=intent_id or str(uuid.uuid4()),
            opportunity_id=opportunity_id,
            physical_account_id=physical_account_id,
            binding_id=binding_id,
            client_correlation_id=client_correlation_id,
            policy_hash=policy_hash,
            quantity=quantity,
            price_constraints=price_constraints,
            protection_recipe=protection_recipe,
            reservation_id=reservation_id or "",
        )


@dataclass(frozen=True)
class OutboxItem:
    """An entry in the outbox queue for dispatch.

    Fields:
        item_id: Unique outbox entry identifier (UUID)
        intent_id: Foreign key to order_intents.intent_id
        state: Current state (OUTBOXED, DISPATCHING, etc.)
        created_at: When this item was enqueued
        claimed_at: When a worker claimed it for dispatch (NULL if not yet claimed)
        claimed_by: Worker lease ID that claimed it (NULL if not claimed)
        response: JSON-serialized broker response (NULL until persisted)
        response_recorded_at: When the response was recorded (NULL if pending)
    """

    item_id: str
    intent_id: str
    state: IntentState
    created_at: datetime
    claimed_at: Optional[datetime]
    claimed_by: Optional[str]
    response: Optional[str]
    response_recorded_at: Optional[datetime]

    @classmethod
    def create(cls, intent_id: str, item_id: Optional[str] = None) -> OutboxItem:
        """Create a fresh outbox item for a new intent.

        Args:
            intent_id: Foreign key to the intent
            item_id: Existing item ID (optional, for recovery)

        Returns:
            A new frozen OutboxItem in OUTBOXED state.
        """
        now = datetime.now()
        return cls(
            item_id=item_id or str(uuid.uuid4()),
            intent_id=intent_id,
            state=IntentState.OUTBOXED,
            created_at=now,
            claimed_at=None,
            claimed_by=None,
            response=None,
            response_recorded_at=None,
        )


class Outbox:
    """Transactional outbox for durable order dispatch and SUBMISSION_UNKNOWN recovery.

    The outbox decouples intent creation from broker dispatch, allowing crash
    recovery at fine boundaries:

    1. enqueue(intent) - writes intent and outbox item in same transaction,
       atomically claiming the opportunity_id for mutual exclusion.
    2. claim_next(worker_lease) - in a second short transaction, marks one
       outbox item as DISPATCHING under the given lease.
    3. record_response(intent_id, response) - after the broker call (outside
       any transaction), persists the response and marks the item SUBMITTED
       or UNKNOWN.

    A restart finds unclaimed items and reclaims them. Claimed but unresponded
    items are skipped (ambiguous: the broker call may or may not have happened).
    """

    def __init__(self, store: Any):
        """Initialize the outbox for a given SignalStore.

        Args:
            store: A SignalStore instance (app/db.py).
        """
        self.store = store

    def enqueue(self, intent: OrderIntent) -> OutboxItem:
        """Write an order intent and outbox entry in one atomic transaction.

        Atomically:
        1. Claim the opportunity_id (unique constraint: never two intents for same signal)
        2. Write the order intent row
        3. Write the outbox entry
        4. Commit

        Args:
            intent: The OrderIntent to enqueue.

        Returns:
            A fresh OutboxItem in OUTBOXED state, ready to be claimed.

        Raises:
            sqlite3.IntegrityError: If opportunity_id is already claimed
              (another worker already admitted this signal).
        """
        return self.store.insert_order_intent_and_outbox(intent)

    def claim_next(self, worker_lease_id: str) -> Optional[OutboxItem]:
        """Claim the next unclaimed outbox item for dispatch.

        In a short, separate transaction:
        1. Find the oldest OUTBOXED item
        2. Mark it DISPATCHING under the given lease
        3. Return it (or None if queue is empty)

        A worker that loses its lease before calling record_response is
        fenced (WriterLeaseGuard.require_active fails on the next check).

        Args:
            worker_lease_id: The current writer lease ID.

        Returns:
            The next OutboxItem to dispatch, or None if queue is empty.

        Raises:
            FencedOutError: If the caller's lease is no longer current.
        """
        return self.store.claim_next_outbox_item(worker_lease_id)

    def record_response(self, intent_id: str, response: dict[str, Any]) -> None:
        """Persist the broker response and transition the intent state.

        After the broker call completes (successfully or not):
        1. Classify the response (success → SUBMITTED, exception → UNKNOWN)
        2. Write the response JSON and new state to the outbox
        3. Commit

        This call is outside any transaction; the caller has already made
        the broker call and obtained the result (or caught the exception).

        Args:
            intent_id: The intent's ID.
            response: The broker response dict or error details.

        Raises:
            ValueError: If the intent is not found.
        """
        # Classify the response based on error/exception/status fields
        if "error" in response or "exception" in response:
            state = "unknown"
        elif response.get("status") in ("rejected",):
            state = "rejected"
        else:
            state = "submitted"

        self.store.record_outbox_response(intent_id, response, state=state)
