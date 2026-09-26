"""Data model for managed-lifecycle position protection — the fallback path
for brokers/accounts that can't submit entry + stop + take-profit as one
atomic bracket/OCO order (see app/lifecycle/manager.py's module docstring
for the full design and why).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

from app.models import AssetClass, Side


class ProtectionStatus(str, enum.Enum):
    UNPROTECTED = "unprotected"
    STOP_PENDING = "stop_pending"
    STOP_CONFIRMED = "stop_confirmed"


class TransferPhase(str, enum.Enum):
    """Where a single exit (a logical target, trailing ratchet, or provider
    close) currently stands in the protection-transfer sequence. Modeled
    explicitly — not inferred from a couple of unrelated booleans — because
    the point where the stop was reduced/cancelled but the exit's own
    remainder hasn't been confirmed done is exactly where a naive
    implementation either leaves shares uncovered indefinitely or restores
    the stop too early (see PendingExit's docstring)."""

    STOP_REDUCED = "stop_reduced"  # old protective stop cancelled/confirmed gone
    EXIT_SUBMITTED = "exit_submitted"  # the reducing sell order is with the broker
    AWAITING_REMAINDER_RESOLUTION = "awaiting_remainder_resolution"  # broker reported PENDING; some of the requested quantity may still fill
    RESTORING = "restoring"  # remainder resolved (filled or confirmed cancelled); resizing the stop to the true remaining owned quantity
    COMPLETE = "complete"


class TargetAction(str, enum.Enum):
    SELL = "sell"
    TIGHTEN_STOP = "tighten_stop"
    ACTIVATE_TRAIL = "activate_trail"


@dataclass
class Target:
    """A logical, app-managed profit action — not necessarily a standing broker order.

    `reduce_fraction` is a fraction of the *originally planned* quantity (not
    whatever happens to be owned when it fires), matching the worked example
    in the design: "Target 1: sell 25% of the original copied allocation."
    """

    trigger_price: float
    action: TargetAction = TargetAction.SELL
    reduce_fraction: float | None = None  # required when action == SELL
    fired: bool = False


@dataclass
class TrailingPolicy:
    activate_at_price: float | None = None
    trail_distance: float = 0.0
    active: bool = False
    floor_price: float | None = None  # current desired floor once active


@dataclass
class PositionPlan:
    """Computed before the entry is submitted. Sizing here is a plan, not a
    guarantee — the lifecycle tracks what actually filled separately."""

    account_id: str
    symbol: str
    side: Side  # the entry side: BUY for long, SELL for short
    planned_quantity: float
    asset_class: AssetClass = AssetClass.CRYPTO
    #: Which broker adapter (app/brokers/*.py's `name`) this plan trades
    #: against — needed by app/reconciliation.py to poll a pending exit's
    #: order status, since a persisted/resumed lifecycle has no live
    #: DestinationAccount to read it from otherwise.
    broker: str = ""
    initial_stop: float | None = None
    targets: list[Target] = field(default_factory=list)
    trailing: TrailingPolicy | None = None
    time_exit: datetime | None = None
    max_risk: float | None = None


@dataclass
class StopRecord:
    desired_price: float | None = None
    submitted_price: float | None = None
    broker_confirmed_price: float | None = None
    broker_order_id: str | None = None
    protected_quantity: float = 0.0
    status: ProtectionStatus = ProtectionStatus.UNPROTECTED


@dataclass
class PendingExit:
    """An exit (target fill, trailing ratchet, provider close) whose broker
    order reported PENDING rather than a synchronous, final fill — so the
    old protective stop has already been cancelled to free these shares,
    but how many of them will actually leave the position is still
    unknown.

    This is deliberately not folded into `StopRecord`: the deficit it
    describes ("N shares have no covering stop, up to `requested_quantity
    - confirmed_filled_quantity` of them may still sell") is a distinct,
    separately-trackable fact from the stop's own state, per the design's
    requirement to track coverage by quantity rather than a single
    protected=true flag.

    The stop is NOT resized/restored while this is open (`phase !=
    COMPLETE`, i.e. `remainder_resolved` is False) — see
    PositionLifecycleManager.resolve_pending_exit for why: restoring against
    `confirmed_owned_quantity - confirmed_filled_quantity` while some of
    `requested_quantity` might still fill would recreate exactly the
    multi-order oversell this whole subsystem exists to prevent.
    """

    broker_order_id: str | None
    requested_quantity: float
    confirmed_filled_quantity: float = 0.0
    remainder_resolved: bool = False  # True once the broker confirms no more of `requested_quantity` can fill (fully filled, or the remainder's cancellation is confirmed)
    phase: TransferPhase = TransferPhase.EXIT_SUBMITTED
    source: str = ""
    reason: str = ""
    #: PRO-06: True when the old protective stop was shrunk in place
    #: (broker.replace_stop_quantity) rather than cancelled outright before
    #: this exit was submitted -- `stop.broker_order_id` is still the SAME
    #: resting order, just sized down. resolve_pending_exit's terminal
    #: branch must correct that same order's quantity to the true
    #: remainder rather than submit a brand new stop on top of it.
    stop_amended: bool = False

    @property
    def unresolved_remainder(self) -> float:
        """How much of this exit could still fill — the uncertainty a
        restore must not ignore."""
        if self.remainder_resolved:
            return 0.0
        return max(0.0, self.requested_quantity - self.confirmed_filled_quantity)


@dataclass
class PendingEntry:
    """An entry order whose broker response reported PENDING rather than a
    synchronous, final fill — so how much (if any) of `requested_quantity`
    will actually be owned is still unknown. Until this resolves,
    `on_entry_fill` must NOT be called (there is nothing confirmed yet to
    protect), but the entry must also not be treated as a definite
    rejection — the broker may already have accepted it and the response
    was merely delayed or lost. `PositionLifecycleManager.resolve_pending_entry`
    (driven by app/reconciliation.py polling `broker_order_id` via
    `get_order_status()`) is the only thing allowed to settle this: a
    zero-fill, confirmed-cancelled outcome unregisters the plan (nothing
    to protect); any positive confirmed fill calls `on_entry_fill` with
    exactly that amount — even if less than `requested_quantity` (a
    partial fill whose remainder was then cancelled), never the naive
    "assume the whole request filled" guess.
    """

    broker_order_id: str | None
    requested_quantity: float
    confirmed_filled_quantity: float = 0.0
    remainder_resolved: bool = False


@dataclass
class PositionLifecycle:
    plan: PositionPlan
    confirmed_owned_quantity: float = 0.0
    stop: StopRecord = field(default_factory=StopRecord)
    closed: bool = False
    pending_exit: PendingExit | None = None
    pending_entry: PendingEntry | None = None
    # Halt state lives on CloseArbiter, not here — it's the single source of
    # truth (app/lifecycle/close_arbiter.py's is_halted()/halt_reason()), so
    # this lifecycle and the arbiter's ledger can never disagree about it.

    @property
    def exit_side(self) -> Side:
        return Side.SELL if self.plan.side == Side.BUY else Side.BUY

    @property
    def key(self) -> tuple[str, str]:
        return (self.plan.account_id, self.plan.symbol)

    @property
    def covered_quantity(self) -> float:
        """How much of the position currently sits behind a broker-confirmed
        stop — distinct from `confirmed_owned_quantity` whenever a
        `pending_exit` has freed shares from the old stop that aren't
        resolved yet (see PendingExit's docstring)."""
        if self.stop.status == ProtectionStatus.STOP_CONFIRMED:
            return self.stop.protected_quantity
        return 0.0

    @property
    def uncovered_quantity(self) -> float:
        return max(0.0, self.confirmed_owned_quantity - self.covered_quantity)

    @property
    def has_unresolved_entry(self) -> bool:
        """A still-working entry order (e.g. 30 of a 100-unit buy confirmed
        so far, 70 still out) whose eventual remaining fill is not yet
        known. `closed` must never become true while this is -- being flat
        RIGHT NOW is not the same as having no remaining obligation (EXE-07:
        selling everything confirmed so far used to close and delete the
        lifecycle outright, discarding the record of the still-outstanding
        70 units; a later fill for them then had nothing tracking it,
        unprotected and invisible to monitoring)."""
        return self.pending_entry is not None and not self.pending_entry.remainder_resolved
