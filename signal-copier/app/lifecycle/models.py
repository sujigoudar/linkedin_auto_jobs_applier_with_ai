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


class StopTargetEventType(str, enum.Enum):
    """PU-A4: every REAL, already-occurring state-changing moment this
    codebase's own `PositionLifecycleManager` produces for a position's
    stop/target lifecycle — see app/db.py's `stop_target_events` table and
    `PositionLifecycleManager`'s own call sites for exactly where each of
    these is appended. Deliberately does NOT include a "breakeven" or
    "trail_activated" event type: as of this pass, nothing in this
    branch's live signal path (`app/engine.py`'s `_handle_managed_entry`)
    ever constructs a `Target(action=ACTIVATE_TRAIL)`, a non-null
    `TrailingPolicy`, or a move-to-breakeven command (that capability
    exists only on the sibling `claude/signal-copier-safety-features`
    branch, as `app/signal_commands.py`'s `MOVE_STOP`/"breakeven" handling
    and `app/protection_auditor.py`'s `ProtectionAuditor` — neither file
    exists here). Adding a distinct event type for a trigger this branch's
    code can't actually reach would be a fabricated event, not a real one.
    """

    #: A protective stop got broker-confirmed resting (STOP_CONFIRMED) --
    #: whether that's the very first stop for this position or a fresh
    #: resubmission after a cancel (see
    #: `PositionLifecycleManager._place_stop_locked`'s single success
    #: branch, which is exactly Phase A2's `StopRecord.confirmed_at`
    #: moment).
    STOP_PLACED = "stop_placed"
    #: An already-resting stop's PRICE changed in place (a logical
    #: `TIGHTEN_STOP` target firing, or a trailing-stop ratchet) -- see
    #: `PositionLifecycleManager._replace_stop_price`'s in-place-amend
    #: success branch. Never emitted for a same-price resize (e.g.
    #: `retry_unprotected_positions`' periodic re-attempt, or a
    #: quantity-only resize after a partial exit) -- those aren't a
    #: tightening, they're the same price still resting or a difference
    #: this event type doesn't describe.
    STOP_TIGHTENED = "stop_tightened"
    #: A stop submission came back rejected/errored, or in the ambiguous
    #: "FILLED-on-submission or no broker_order_id" state
    #: `_place_stop_locked` treats as unprotected rather than fabricate
    #: confirmed coverage -- see that method's two failure branches.
    PROTECTION_FAILED = "protection_failed"
    #: A logical profit target (a real `Target(action=SELL)`, e.g. the
    #: single take-profit target `app/engine.py`'s `_handle_managed_entry`
    #: builds from `signal.take_profit`) actually fired -- its exit order
    #: reached FILLED or PENDING and `target.fired` was set True -- see
    #: `PositionLifecycleManager.on_price_update`'s `TargetAction.SELL`
    #: branch.
    TARGET_HIT = "target_hit"


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
    #: DB-0X (order purpose/family): the id of the real `Signal` that
    #: started THIS position episode (set once, at `app/engine.py`'s
    #: `_handle_managed_entry`, from the entry signal it's building this
    #: plan from) -- carried for the lifetime of the position so a later
    #: CLOSE for the same (account_id, symbol), which has no real signal
    #: linking it back to its own entry otherwise, can still be recorded
    #: under the same `orders.family_id` as its entry. `""` (never
    #: fabricated) for a plan built with no real entry signal to attribute
    #: (shouldn't happen on the real entry path, but a test/direct
    #: construction may still omit it) -- see `_lifecycle_to_state`/
    #: `_lifecycle_from_state`'s `.get(..., "")` for why a lifecycle
    #: persisted before this field existed restores safely instead of
    #: raising.
    entry_signal_id: str = ""


@dataclass
class StopRecord:
    desired_price: float | None = None
    submitted_price: float | None = None
    broker_confirmed_price: float | None = None
    broker_order_id: str | None = None
    protected_quantity: float = 0.0
    status: ProtectionStatus = ProtectionStatus.UNPROTECTED
    #: PU-A2: the real moment the broker confirmed this stop is actually
    #: resting (i.e. the instant `status` last became STOP_CONFIRMED) --
    #: app/execution_quality.py's "protection acknowledgment" stage. Reset
    #: to None whenever protection stops being confirmed (rejected, errored,
    #: or an ambiguous submission -- see PositionLifecycleManager._place_stop_locked),
    #: so a stale confirmation timestamp never survives a later loss of
    #: coverage. Never backfilled/guessed -- only set at the exact call
    #: site that sets status = STOP_CONFIRMED.
    confirmed_at: datetime | None = None


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
    #: E03 (bounded): the app/capital_allocator.py notional this entry
    #: reserved, carried forward from admission time ONLY when
    #: `broker_order_id` is set (so app/reconciliation.py's
    #: `_reconcile_pending_entries` is guaranteed to eventually poll this
    #: and call `resolve_pending_entry`, which releases it). 0.0 for a
    #: `broker_order_id=None` entry (an ambiguous lost/errored response) --
    #: that case releases immediately at the call site instead, since
    #: nothing guarantees this will ever be polled to a terminal state.
    reserved_notional: float = 0.0

    @property
    def unresolved_remainder(self) -> float:
        """How much of `requested_quantity` could still fill for this entry
        — mirrors `PendingExit.unresolved_remainder`. This is exactly the
        per-entry contribution to `PositionLifecycleManager.get_outstanding_possible_fill`:
        genuine uncertain exposure (the broker could still confirm more of
        this fill) that must be surfaced, never silently treated as zero
        and never silently treated as already-owned."""
        if self.remainder_resolved:
            return 0.0
        return max(0.0, self.requested_quantity - self.confirmed_filled_quantity)


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

    # --- PU-A1: real MAE/MFE (maximum adverse/favorable excursion) tracking ---
    #
    # `entry_price` is the actual confirmed fill price for this position's
    # entry (set by `PositionLifecycleManager.on_entry_fill`'s `entry_price`
    # argument) — None when that price is unknown (e.g. a caller that
    # doesn't have one, or a position whose entry resolved through the
    # PENDING-entry path, which doesn't yet thread a price through). `mae`/
    # `mfe` below are deliberately None (not 0.0) whenever there's no entry
    # price to measure from — "unknown" and "zero excursion" are different
    # facts and must not be conflated.
    #
    # `highest_price_since_entry`/`lowest_price_since_entry` are updated
    # ONLY from real price observations (see `observe_price`) — a genuine
    # fill price at entry, or a real feed tick via
    # `PositionLifecycleManager.on_price_update` (itself only ever called
    # with a real broker-reported price — see app/pricing.py's
    # `PriceMonitor`). A broker/account with no live-price capability at
    # all (`BrokerAdapter.has_last_price_capability` False) simply never
    # calls `on_price_update` for this position, so these two fields never
    # move past the entry price — see `has_price_data`, which distinguishes
    # "we have observed real prices and they never moved" from "no
    # observation has ever come in beyond the entry fill itself." Neither
    # case is ever a fabricated value.
    entry_price: float | None = None
    highest_price_since_entry: float | None = None
    highest_price_at: datetime | None = None
    lowest_price_since_entry: float | None = None
    lowest_price_at: datetime | None = None

    # --- PU-A3: real last-known price, for equity/unrealized-P&L snapshots ---
    #
    # `highest_price_since_entry`/`lowest_price_since_entry` above are
    # EXTREMES (for MAE/MFE), not "what price is this position at right
    # now" -- app/equity_history.py needs the latter to value an open
    # position against its real cost basis, and must not misuse an extreme
    # for that. `last_observed_price` is simply the most recent real price
    # `observe_price` was called with (a genuine entry fill or feed tick --
    # same provenance guarantee as the two fields above), so it moves with
    # every observation instead of only ratcheting outward. None until at
    # least one real observation has arrived -- never fabricated.
    last_observed_price: float | None = None
    last_observed_price_at: datetime | None = None

    def observe_price(self, price: float, at: datetime) -> None:
        """Record one real price observation for MAE/MFE tracking (and,
        PU-A3, for the last-known-price snapshot consumers like
        app/equity_history.py read). Callers must only ever pass a
        genuine, broker/feed-reported price (or a confirmed fill price) --
        never an estimated or synthetic one; see this dataclass's field
        docstrings above for why."""
        if self.highest_price_since_entry is None or price > self.highest_price_since_entry:
            self.highest_price_since_entry = price
            self.highest_price_at = at
        if self.lowest_price_since_entry is None or price < self.lowest_price_since_entry:
            self.lowest_price_since_entry = price
            self.lowest_price_at = at
        self.last_observed_price = price
        self.last_observed_price_at = at

    @property
    def has_price_data(self) -> bool:
        """True once at least one real price observation has been recorded
        (including the entry fill itself, if `entry_price` was known) --
        False means there is honestly nothing to report MAE/MFE from yet
        (e.g. a broker with no live-price capability and no entry price
        either), as distinct from a real observation that simply hasn't
        moved."""
        return self.highest_price_since_entry is not None

    @property
    def mae(self) -> float | None:
        """Maximum adverse excursion, in price terms: how far price moved
        AGAINST this position from its entry price, at the worst point
        observed so far. Always >= 0 (0.0 means no adverse move has been
        observed yet, not that none is possible). None when there's no
        entry price or no price observation at all to compute it from —
        never fabricated as 0.0 in that case.

        Direction is side-dependent: a LONG's adverse move is a price
        DECREASE (entry minus the lowest price seen); a SHORT's adverse
        move is a price INCREASE (the highest price seen minus entry) —
        being short and having the price rise against you is the loss
        side, not the reverse."""
        if self.entry_price is None or not self.has_price_data:
            return None
        if self.plan.side == Side.BUY:
            assert self.lowest_price_since_entry is not None  # has_price_data guarantees this
            return max(0.0, self.entry_price - self.lowest_price_since_entry)
        assert self.highest_price_since_entry is not None  # has_price_data guarantees this
        return max(0.0, self.highest_price_since_entry - self.entry_price)

    @property
    def mfe(self) -> float | None:
        """Maximum favorable excursion, in price terms: how far price moved
        IN THIS POSITION'S FAVOR from its entry price, at the best point
        observed so far. Always >= 0. None under the same conditions as
        `mae` above.

        Mirror of `mae`'s side handling: a LONG's favorable move is a price
        INCREASE (the highest price seen minus entry); a SHORT's favorable
        move is a price DECREASE (entry minus the lowest price seen)."""
        if self.entry_price is None or not self.has_price_data:
            return None
        if self.plan.side == Side.BUY:
            assert self.highest_price_since_entry is not None  # has_price_data guarantees this
            return max(0.0, self.highest_price_since_entry - self.entry_price)
        assert self.lowest_price_since_entry is not None  # has_price_data guarantees this
        return max(0.0, self.entry_price - self.lowest_price_since_entry)

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
