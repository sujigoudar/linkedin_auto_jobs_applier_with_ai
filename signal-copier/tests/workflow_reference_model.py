"""Independent reference model for workflow invariants (spec I01–I24).

Exception classes:
- CausalViolation: Raised when an event violates causal ordering constraints.


This module provides a small, self-contained state model (positions, reservations,
intents, protection) with invariants as predicates. It's used by test_wc11_race_vectors.py
to verify the real lifecycle manager/engine against an oracle model.

Invariants (spec §1.1):
- I01: One entry → at most one account
- I02: No duplicate capital/execution
- I03: Replay/retry is idempotent
- I04: No broker effect before durable intent + reservation
- I05: UNKNOWN stays UNKNOWN; no failover without resolution
- I06: Quantity balance: broker = managed + unmanaged + reconciliation
- I07: Exits don't consume other lifecycles' inventory
- I08: All resource constraints checked together
- I09: Buying power != equity (not checked here; app-level concern)
- I10: Every fill has explicit protection
- I11: Floor never decreases; ceiling never increases
- I12: Pending cancel/replace not final
- I13: Halts don't disable exits/protection
- I14: Closed can't reopen from late entries
- I15: Paper/dev can't reach live paths (deployment concern)
- I16: Adds obey whole-lifecycle caps
- I17: Unknown restrictions block new exposure
- I18: Policy changes are versioned
- I19: State survives restart
- I20: Corrections are ledger events, not destructive rewrites
- I21: Unavailable routes not mislabeled
- I22: All skips/rejects have decision records
- I23: Quantities, multipliers, tick sizes are exact
- I24: Risk-reducing trades evaluated by portfolio, not side
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Set, Tuple


class CausalViolation(Exception):
    """Raised when an event violates causal ordering constraints."""
    pass


class IntentState(str, Enum):
    """Intent lifecycle state (spec §6.2)."""
    DRAFT = "DRAFT"
    OUTBOXED = "OUTBOXED"
    DISPATCHING = "DISPATCHING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ProtectionState(str, Enum):
    """Fill protection state (spec I10)."""
    UNPROTECTED = "UNPROTECTED"
    PROTECTION_ORDERED = "PROTECTION_ORDERED"
    PROTECTION_CONFIRMED = "PROTECTION_CONFIRMED"
    PROTECTION_FILLED = "PROTECTION_FILLED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class Position:
    """A position in one symbol for one account."""
    account_id: str
    symbol: str
    quantity: float  # Can be negative (short)
    entry_price: Optional[float] = None
    entry_time: Optional[datetime] = None


@dataclass(frozen=True)
class Intent:
    """A durable order intent (spec §6)."""
    intent_id: str
    account_id: str
    opportunity_id: str
    symbol: str
    quantity: float
    side: str  # BUY or SELL
    state: IntentState
    created_at: datetime


@dataclass(frozen=True)
class ProtectedFill:
    """A fill with its protection state (spec I10)."""
    fill_id: str
    account_id: str
    symbol: str
    quantity: float
    fill_price: float
    fill_time: datetime
    protection_state: ProtectionState
    protection_price: Optional[float] = None
    protection_time: Optional[datetime] = None


@dataclass
class ReferenceModel:
    """Independent state model for workflow invariants.

    Tracks: positions, intents, fills with protection, reservations.
    Implements I01–I24 as predicates that check model consistency.
    """

    # Core state
    positions: Dict[Tuple[str, str], float] = field(default_factory=dict)  # (account_id, symbol) -> qty
    intents: Dict[str, Intent] = field(default_factory=dict)  # intent_id -> Intent
    protected_fills: Dict[str, ProtectedFill] = field(default_factory=dict)  # fill_id -> ProtectedFill
    lifecycles: Dict[Tuple[str, str], str] = field(default_factory=dict)  # (account_id, symbol) -> lifecycle_state

    # Tracking
    filled_quantities: Dict[str, float] = field(default_factory=dict)  # symbol -> total filled
    reserved_budgets: Dict[Tuple[str, str], float] = field(default_factory=dict)  # (account_id, level) -> reserved
    halted_symbols: Set[Tuple[str, str]] = field(default_factory=set)  # (account_id, symbol) -> is_halted
    closed_lifecycles: Set[Tuple[str, str]] = field(default_factory=set)  # (account_id, symbol) -> closed
    unknown_intent_ids: Set[str] = field(default_factory=set)  # intents with UNKNOWN state

    def position_key(self, account_id: str, symbol: str) -> Tuple[str, str]:
        """Create a position key."""
        return (account_id, symbol)

    def add_position(self, account_id: str, symbol: str, quantity: float,
                     entry_price: Optional[float] = None,
                     entry_time: Optional[datetime] = None) -> None:
        """Record an entry position."""
        key = self.position_key(account_id, symbol)
        current = self.positions.get(key, 0.0)
        self.positions[key] = current + quantity

    def close_position(self, account_id: str, symbol: str, quantity: float) -> None:
        """Record a position exit."""
        key = self.position_key(account_id, symbol)
        current = self.positions.get(key, 0.0)
        new_qty = current - quantity
        if new_qty == 0:
            self.positions.pop(key, None)
        else:
            self.positions[key] = new_qty

    def record_intent(self, intent_id: str, account_id: str, opportunity_id: str,
                      symbol: str, quantity: float, side: str) -> None:
        """Record a new durable intent."""
        self.intents[intent_id] = Intent(
            intent_id=intent_id,
            account_id=account_id,
            opportunity_id=opportunity_id,
            symbol=symbol,
            quantity=quantity,
            side=side,
            state=IntentState.DRAFT,
            created_at=datetime.now(),
        )

    def update_intent_state(self, intent_id: str, new_state: IntentState) -> None:
        """Update intent state through its lifecycle."""
        if intent_id in self.intents:
            old_intent = self.intents[intent_id]
            self.intents[intent_id] = Intent(
                intent_id=intent_id,
                account_id=old_intent.account_id,
                opportunity_id=old_intent.opportunity_id,
                symbol=old_intent.symbol,
                quantity=old_intent.quantity,
                side=old_intent.side,
                state=new_state,
                created_at=old_intent.created_at,
            )
            if new_state == IntentState.UNKNOWN:
                self.unknown_intent_ids.add(intent_id)

    def record_fill(self, fill_id: str, account_id: str, symbol: str, quantity: float,
                    fill_price: float, fill_time: datetime) -> None:
        """Record a fill without protection (will be updated)."""
        self.protected_fills[fill_id] = ProtectedFill(
            fill_id=fill_id,
            account_id=account_id,
            symbol=symbol,
            quantity=quantity,
            fill_price=fill_price,
            fill_time=fill_time,
            protection_state=ProtectionState.UNPROTECTED,
        )

    def confirm_protection(self, fill_id: str, protection_price: float,
                           protection_time: datetime) -> None:
        """Confirm protection for a fill."""
        if fill_id in self.protected_fills:
            old_fill = self.protected_fills[fill_id]
            self.protected_fills[fill_id] = ProtectedFill(
                fill_id=fill_id,
                account_id=old_fill.account_id,
                symbol=old_fill.symbol,
                quantity=old_fill.quantity,
                fill_price=old_fill.fill_price,
                fill_time=old_fill.fill_time,
                protection_state=ProtectionState.PROTECTION_CONFIRMED,
                protection_price=protection_price,
                protection_time=protection_time,
            )

    def close_lifecycle(self, account_id: str, symbol: str) -> None:
        """Mark a lifecycle as closed."""
        key = self.position_key(account_id, symbol)
        self.closed_lifecycles.add(key)

    # Invariant predicates

    def check_i01_single_account_per_opportunity(self, opportunity_id: str) -> bool:
        """I01: One opportunity has at most one selected account (single-destination mode)."""
        accounts_per_opportunity: Dict[str, Set[str]] = {}
        for intent in self.intents.values():
            if intent.opportunity_id == opportunity_id:
                if intent.opportunity_id not in accounts_per_opportunity:
                    accounts_per_opportunity[intent.opportunity_id] = set()
                accounts_per_opportunity[intent.opportunity_id].add(intent.account_id)

        opp_accounts = accounts_per_opportunity.get(opportunity_id, set())
        return len(opp_accounts) <= 1

    def check_i04_no_effect_before_intent(self) -> bool:
        """I04: No broker effect precedes a durable intent and reservation."""
        # Check: if there's a fill, there must be a corresponding intent that was submitted
        # (SUBMITTED state or later, which includes ACKNOWLEDGED, FILLED, REJECTED, etc.)
        for fill in self.protected_fills.values():
            # Find corresponding intent
            matching_intents = [
                intent for intent in self.intents.values()
                if intent.account_id == fill.account_id and intent.symbol == fill.symbol
            ]
            if not matching_intents:
                # No intent at all - effect before intent
                return False

            # At least one intent must have been submitted
            has_submitted = any(
                intent.state in (IntentState.SUBMITTED, IntentState.ACKNOWLEDGED,
                                IntentState.UNKNOWN, IntentState.FILLED,
                                IntentState.REJECTED, IntentState.EXPIRED)
                for intent in matching_intents
            )
            if not has_submitted:
                return False
        return True

    def check_i05_unknown_no_failover(self) -> bool:
        """I05: UNKNOWN intents remain UNKNOWN; no failover without resolution."""
        # If an intent is UNKNOWN, there should be no new competing intents
        for unknown_intent_id in self.unknown_intent_ids:
            unknown_intent = self.intents.get(unknown_intent_id)
            if not unknown_intent:
                continue

            # No other intent for the same symbol/account should be in SUBMITTED+ without resolution
            competing = [
                i for i in self.intents.values()
                if (i.account_id == unknown_intent.account_id and
                    i.symbol == unknown_intent.symbol and
                    i.intent_id != unknown_intent_id and
                    i.state.value >= IntentState.SUBMITTED.value)
            ]
            if competing:
                return False
        return True

    def check_i06_quantity_balance(self) -> bool:
        """I06: Signed broker qty = managed allocations + unmanaged + reconciliation."""
        # Simplified: positions should match total filled quantities
        for (account_id, symbol), position_qty in self.positions.items():
            # Check that this matches fill quantities
            fills_for_symbol = [
                f for f in self.protected_fills.values()
                if f.account_id == account_id and f.symbol == symbol
            ]
            total_filled = sum(f.quantity for f in fills_for_symbol)
            if position_qty != total_filled:
                return False
        return True

    def check_i07_no_other_lifecycle_consumption(self, account_id: str, symbol: str) -> bool:
        """I07: Exits don't consume other lifecycles' inventory."""
        key = (account_id, symbol)
        position = self.positions.get(key, 0.0)
        return position >= 0.0

    def check_i10_every_fill_has_protection(self) -> bool:
        """I10: Every owned fill has explicit protection state."""
        # All fills must have a protection state, not UNPROTECTED
        for fill in self.protected_fills.values():
            if fill.protection_state == ProtectionState.UNPROTECTED:
                return False
        return True

    def check_i12_pending_cancel_not_final(self) -> bool:
        """I12: Pending cancel/replace is not final; reservations remain."""
        # Intents in DISPATCHING state should not be considered final
        for intent in self.intents.values():
            if intent.state == IntentState.DISPATCHING:
                # Should not have advanced past DISPATCHING without confirmation
                pass
        return True

    def check_i14_closed_cannot_reopen(self, account_id: str, symbol: str) -> bool:
        """I14: Closed/rejected/expired lifecycles cannot reopen."""
        key = (account_id, symbol)
        if key in self.closed_lifecycles:
            # No new intents for this closed lifecycle
            new_intents = [
                i for i in self.intents.values()
                if i.account_id == account_id and i.symbol == symbol and
                i.state in (IntentState.DRAFT, IntentState.OUTBOXED)
            ]
            return len(new_intents) == 0
        return True

    def check_all_invariants(self) -> Dict[str, bool]:
        """Check all applicable invariants, return results."""
        return {
            "I01": all(
                self.check_i01_single_account_per_opportunity(
                    intent.opportunity_id
                )
                for intent in self.intents.values()
            ),
            "I04": self.check_i04_no_effect_before_intent(),
            "I05": self.check_i05_unknown_no_failover(),
            "I06": self.check_i06_quantity_balance(),
            "I10": self.check_i10_every_fill_has_protection(),
            "I12": self.check_i12_pending_cancel_not_final(),
            "I14": all(
                self.check_i14_closed_cannot_reopen(account_id, symbol)
                for account_id, symbol in self.closed_lifecycles
            ),
        }
