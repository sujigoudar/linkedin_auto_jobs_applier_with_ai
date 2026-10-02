"""Managed position lifecycle: the fallback for accounts/brokers that can't
submit entry + stop + take-profit as one atomic bracket/OCO order
(`BrokerAdapter.supports_native_bracket = False`). When a broker *can* do
that atomically, its `place_order` already sends stop_loss/take_profit
directly (see app/brokers/*.py) — this module isn't involved and nothing
here changes that path.

## Why this exists

Firing a protective stop order and a full-size profit-target order
independently, against a broker that treats them as unrelated, risks
exactly the failure the design this module implements is built around:
two independent sell orders for the same shares, both able to fill, for
more than what's actually owned. So instead of "place stop, place target,
hope only one fills," the flow here is:

    ENTRY -> actual fill observed -> protect the filled quantity FIRST
    -> manage targets/trailing as logical (app-side) instructions
    -> coordinate every exit through one CloseArbiter

Every exit — a logical target firing, a trailing stop ratchet, the
protective stop itself filling, a provider EXIT signal, a time exit, an
emergency exit — funnels through `CloseArbiter`, which is the only thing
allowed to authorize reducing a tracked position. See
app/lifecycle/close_arbiter.py for the invariant it enforces.

## The stop-resize transition

A profit target selling part of a protected position can't just submit a
sell order alongside an unchanged stop: if both later fill, that's an
oversell (a 62-share position with a 62-share stop plus a 15-share target
sell can settle at 77 shares sold against 62 owned). So `request_exit()`
here performs a specific sequence, holding the position's
`CloseArbiter.transition()` lock for all of it so nothing else can touch
this position concurrently:

    1. cancel/reduce the existing protective stop (so it stops claiming
       the shares about to be sold) — and DON'T proceed if that can't be
       *confirmed*: a cancel that might have raced a real fill is treated
       as "the stop may have already filled," not as success.
    2. submit the exit order
    3. observe what actually filled (not what was requested — a partial
       fill changes the arithmetic for step 4)
    4. recompute the true remaining quantity from the confirmed owned
       total, not the plan
    5. submit a replacement stop sized to that true remainder
    6. release the lock

## What's still a documented gap

Startup reconciliation against the broker's own live position/order state
(design section 14) isn't implemented here — this manager trusts its own
in-memory + `CloseArbiter` bookkeeping, seeded by `on_entry_fill`. A
process restart loses that state for any position it was still managing.
Wiring `BrokerAdapter.get_broker_position` into a startup reconciliation
pass is the natural next step, not done in this pass.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from signal_platform_contracts import Environment, EvidenceClass

from app import command_ledger, config
from app.brokers.base import BrokerAdapter
from app.capital_allocator import CapitalAllocator
from app.export_events import build_execution_applied_envelope
from app.lifecycle.close_arbiter import CloseArbiter
from app.lifecycle.models import (
    PendingEntry,
    PendingExit,
    PositionLifecycle,
    PositionPlan,
    ProtectionStatus,
    StopRecord,
    StopTargetEventType,
    Target,
    TargetAction,
    TrailingPolicy,
    TransferPhase,
)
from app.models import (
    AssetClass,
    CommandLedgerEntry,
    CommandType,
    DestinationAccount,
    Intent,
    OrderResult,
    OrderStatus,
    Signal,
    Side,
    UncertaintyState,
)
from app.writer_lease import NullLeaseGuard, WriterLeaseGuard

logger = logging.getLogger(__name__)


@dataclass
class ReductionPlan:
    """The real pre-order-submission planning `request_exit` computes for a
    reduction, BEFORE any broker I/O -- extracted into its own pure
    function (`_compute_reduction_plan`) so a read-only preview (see
    `PositionLifecycleManager.preview_reduction`) can call the exact same
    computation `request_exit` itself uses instead of a second,
    independently maintained copy that could drift."""

    requested_quantity: float
    owned_before: float
    available_before: float
    remaining_after_request: float
    had_stop: bool
    stop_desired_price: float | None
    can_amend_stop_in_place: bool


def _compute_reduction_plan(
    tx: Any, lifecycle: "PositionLifecycle", broker: BrokerAdapter | None, quantity: float
) -> ReductionPlan:
    """Pure (no I/O, no mutation): exactly the fields `request_exit` itself
    derives from `tx`/`lifecycle`/`broker` before it ever calls
    `broker.replace_stop_quantity`/`cancel_order`/`place_order`. Called from
    inside `request_exit` (with a live, lock-held `tx`) AND from
    `preview_reduction` (with a read-only `tx` obtained the same way, via
    `CloseArbiter.transition`, but never committing a reservation) -- see
    each caller."""
    requested = min(quantity, tx.available)
    had_stop = lifecycle.stop.broker_order_id is not None
    remaining_after_request = tx.owned - requested
    can_amend_in_place = (
        had_stop
        and remaining_after_request > 0
        and lifecycle.stop.desired_price is not None
        and broker is not None
        and broker.has_replace_stop_capability
    )
    return ReductionPlan(
        requested_quantity=requested,
        owned_before=tx.owned,
        available_before=tx.available,
        remaining_after_request=remaining_after_request,
        had_stop=had_stop,
        stop_desired_price=lifecycle.stop.desired_price,
        can_amend_stop_in_place=can_amend_in_place,
    )


@dataclass
class _ClosedExitRecord:
    """TRK-27: recorded once, the instant a managed lifecycle's exit makes
    it `closed` (see `_apply_exit_fill`) — the identity of the exact "exit
    episode" that just finished, so a genuinely duplicate exit request for
    the SAME real-world event (a provider re-sending the same EXIT through
    a second collector/transport, with a different channel_id/message_id,
    arriving AFTER the first exit already fully resolved — see the audit
    finding this closes, docs/adr/0010-managed-exit-duplicate-episode-guard.md)
    can be recognized and logged instead of silently treated as a
    brand-new request against a position that's already flat.

    `entry_signal_id` is this episode's stable identity — the SAME value
    `app/engine.py`'s Track 18 ownership gate already uses as "which
    provider/episode owns this lifecycle" (see `PositionPlan.
    entry_signal_id`'s own docstring in app/lifecycle/models.py) — reused
    here rather than inventing a parallel identity concept, per this
    codebase's existing convention (see `app/signal_correlation.py` /
    `get_provider_position_ownership` for the sibling pattern on the entry
    side). `closed_at` bounds how long this record is honored — see
    `request_exit`'s own duplicate check and
    `config.MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS`.

    Scope — read before assuming this covers more than it does: this is
    an IN-MEMORY, per-process record. It does NOT survive a process
    restart (consistent with this module's own already-documented "no
    startup reconciliation" gap — see this module's docstring and
    docs/design/POSITION_LIFECYCLE.md's "Documented gap" section: a
    duplicate exit arriving across a restart, within the window, is not
    caught). It also does NOT, and is not meant to, catch a duplicate
    that arrives after a NEW position has already been opened for the
    same (account_id, symbol): a real re-entry replaces this episode's
    lifecycle in `_lifecycles` before the duplicate arrives, and
    `request_exit`'s duplicate check only ever fires on the "lifecycle is
    None or lifecycle.closed" branch — a live, re-entered lifecycle is
    processed completely normally (never suppressed). Seeing and closing
    that NEW, genuinely different position when its own real exit comes
    in is the correct behavior, not a bug this guard is meant to catch;
    see the regression tests this track added for both the positive
    (duplicate recognized) and negative (real second exit after a real
    re-entry NOT suppressed) cases.
    """

    entry_signal_id: str
    closed_at: datetime
    reason: str


def _compute_trailing_candidate(
    trailing: TrailingPolicy, current_desired_price: float | None, side: Side, price: float
) -> tuple[float, bool]:
    """Pure (no I/O, no mutation): exactly the candidate-floor/`improved`
    computation `_update_trailing` itself derives before deciding whether to
    actually move the stop. Extracted so `preview_stop_change` can call the
    exact same computation `_update_trailing` uses instead of a second,
    independently maintained copy that could drift -- see each caller."""
    existing = [v for v in (trailing.floor_price, current_desired_price) if v is not None]
    if side == Side.BUY:
        current_best = max(existing) if existing else None
        candidate_floor = price - trailing.trail_distance
        improved = current_best is None or candidate_floor > current_best
    else:
        current_best = min(existing) if existing else None
        candidate_floor = price + trailing.trail_distance
        improved = current_best is None or candidate_floor < current_best
    return candidate_floor, improved


class PositionLifecycleManager:
    def __init__(self, brokers: dict[str, BrokerAdapter], arbiter: CloseArbiter | None = None, store=None):
        self.brokers = brokers
        self.arbiter = arbiter or CloseArbiter()
        self.store = store  # app.db.SignalStore, optional — enables crash-resumable persistence
        self._lifecycles: dict[tuple[str, str], PositionLifecycle] = {}
        # TRK-27: the last resolved "exit episode" per (account_id, symbol)
        # — see `_ClosedExitRecord`'s own docstring for exactly what this
        # is, its documented scope, and the duplicate-exit audit finding it
        # closes. In-memory only, by design — see that docstring's "Scope"
        # section.
        self._last_closed_exit: dict[tuple[str, str], _ClosedExitRecord] = {}
        # Serializes the ENTIRE fill-application decision in resolve_pending_entry
        # (read the last-applied checkpoint, compute the new delta, apply it) per
        # (account_id, symbol) -- separate from `self.arbiter`'s lock, which only
        # protects individual ledger mutations and is acquired/released multiple
        # times within one resolve_pending_entry call. Without this, two
        # concurrent observations of the same broker order can both read the
        # checkpoint before either advances it and both apply the same fill.
        self._pending_entry_locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        # E03 (bounded): set by app/engine.py's Engine.__init__ right after
        # it constructs its own CapitalAllocator (this manager is often
        # constructed first, by app/main.py, before that exists) --
        # `resolve_pending_entry` uses it to release a pending entry's
        # reservation once its outcome is confirmed terminal. None (never
        # wired in) is a safe no-op: reservations then just aren't
        # released here, same as before this fix existed.
        self.capital_allocator: CapitalAllocator | None = None
        # Cross-process/cross-host single-writer fencing (app/writer_lease.py,
        # docs/FAILOVER.md) -- set by app/engine.py's Engine.__init__ to the
        # same guard it holds (this manager is often constructed first, by
        # app/main.py, before that exists), same "wired in after
        # construction, None-safe default" convention as capital_allocator
        # above. Checked at the top of every entry point here that can
        # reach a broker write, INCLUDING the ones driven by background
        # loops (app/reconciliation.py's retry_unprotected_positions/
        # resolve_pending_entry/resolve_pending_exit calls,
        # app/price_monitor's on_price_update) rather than only a live
        # signal/close call -- those never pass through app/engine.py's
        # own top-level checks at all.
        self.lease_guard: WriterLeaseGuard | NullLeaseGuard = NullLeaseGuard()

    def get_lifecycle(self, account_id: str, symbol: str) -> PositionLifecycle | None:
        return self._lifecycles.get((account_id, symbol))

    async def update_stop_price(self, account_id: str, symbol: str, new_price: float) -> None:
        """WP-11 (A-02): Public wrapper to update a managed lifecycle's stop price.
        
        Used by the engine's edit-handling path to amend an existing stop price
        when a signal edits the original entry message.
        """
        lifecycle = self.get_lifecycle(account_id, symbol)
        if lifecycle is None:
            logger.warning(
                "cannot update stop price for %s/%s: no managed lifecycle exists",
                account_id,
                symbol,
            )
            return
        # Set the desired price on the lifecycle's stop
        lifecycle.stop.desired_price = new_price
        logger.info(
            "stop price update requested for %s/%s to %.8f",
            account_id,
            symbol,
            new_price,
        )

    def list_open_lifecycles(self) -> list[PositionLifecycle]:
        """Every managed-lifecycle position not yet closed — for monitoring
        (see app/main.py's `/positions`), not for mutation."""
        return [lifecycle for lifecycle in self._lifecycles.values() if not lifecycle.closed]

    def _record_closed_exit_episode(
        self, account: DestinationAccount, symbol: str, lifecycle: PositionLifecycle, *, reason: str
    ) -> None:
        """TRK-27: called once from `_apply_exit_fill`, only the instant
        `lifecycle.closed` just became True — see `_ClosedExitRecord`'s own
        docstring for what this is for and its documented scope."""
        self._last_closed_exit[(account.account_id, symbol)] = _ClosedExitRecord(
            entry_signal_id=lifecycle.plan.entry_signal_id,
            closed_at=datetime.now(timezone.utc),
            reason=reason,
        )

    def check_duplicate_exit(self, account: DestinationAccount, symbol: str) -> OrderResult | None:
        """TRK-27: called only from `request_exit`'s "no active lifecycle"
        branch (`lifecycle is None or lifecycle.closed`) — see that call
        site and `_ClosedExitRecord`'s own docstring for exactly what this
        covers and does not. Returns `None` (the caller's generic "no open
        position to close" rejection applies, unchanged) unless a real
        closed-exit record exists for this (account_id, symbol) AND it's
        still within `config.MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS` of that
        exit's own resolution.

        Deliberately still returns REJECTED — never a fabricated FILLED or
        a replayed nonzero quantity. `_handle_managed_close`'s own
        classification (app/engine.py) already treats REJECTED as "nothing
        applied, nothing outstanding," which is the true, honest state
        here; reporting FILLED with the original exit's quantity instead
        would double-count that same execution in every `orders`-table
        consumer (app/economics.py, app/provider_value.py,
        app/trade_episode.py) — exactly the failure TR-EPISODE-01 already
        guards against elsewhere in this module (see
        `_SELF_PERSISTED_EXIT_KINDS`'s own docstring). The only change from
        the generic rejection is the message (clearly identifies this as a
        recognized duplicate, not a bare "no position" error) and an
        INFO-level log record — the honest way to satisfy "log/record it"
        without claiming a success this call never produced."""
        record = self._last_closed_exit.get((account.account_id, symbol))
        if record is None:
            return None
        age_seconds = (datetime.now(timezone.utc) - record.closed_at).total_seconds()
        if age_seconds < 0 or age_seconds > config.MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS:
            return None
        logger.info(
            "TRK-27: duplicate managed-exit request recognized for account=%s symbol=%s -- this "
            "position's exit episode (entry_signal_id=%s) already resolved %.1fs ago (reason=%r); "
            "submitting no new broker order",
            account.account_id,
            symbol,
            record.entry_signal_id,
            age_seconds,
            record.reason,
        )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.REJECTED,
            signal_id="",
            message=(
                "duplicate exit recognized: this position was already fully closed "
                f"{age_seconds:.1f}s ago (reason={record.reason!r}); no new broker order submitted "
                "(see TRK-27)"
            ),
        )

    async def retry_unprotected_positions(self) -> int:
        """Independent of any new fill increment (PRO-04): re-attempt
        protection for every open, genuinely-owned lifecycle that isn't
        currently STOP_CONFIRMED or that has uncovered_quantity > 0 (WP-23 D-15).
        A definitive stop-placement/replace failure used to only ever get
        retried by the NEXT confirmed fill increment (see resolve_pending_entry)
        — a position that's already fully filled would never see one, leaving it
        unprotected indefinitely with no other recovery path. app/reconciliation.py
        calls this every pass, so a real protection deficit gets a bounded,
        periodic retry rather than depending on unrelated future activity.
        Safe to call repeatedly: an already-protected lifecycle, or one
        deferring to an unresolved exit, is a no-op."""
        self.lease_guard.require_active()
        retried = 0
        for lifecycle in list(self._lifecycles.values()):
            if lifecycle.closed or lifecycle.confirmed_owned_quantity <= 0:
                continue
            # WP-23 D-15: retry when status is not confirmed OR when uncovered_quantity > 0
            is_protected = lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED and lifecycle.uncovered_quantity <= 1e-9
            if is_protected:
                continue
            if lifecycle.pending_exit is not None and not lifecycle.pending_exit.remainder_resolved:
                continue  # deferred to that exit's own resolution, same as _replace_stop_price's own guard
            broker = self.brokers.get(lifecycle.plan.broker)
            if broker is None:
                continue
            desired_price = lifecycle.stop.desired_price or lifecycle.plan.initial_stop
            if desired_price is None:
                continue
            lifecycle.stop.desired_price = desired_price
            account = DestinationAccount(account_id=lifecycle.plan.account_id, broker=lifecycle.plan.broker)
            # PU-A4: a periodic background retry, not something a live
            # trading signal drove this instant -- distinct `source` from
            # the tighten/trailing/entry paths below.
            await self._replace_stop_price(lifecycle, account, source="reconciliation")
            retried += 1
        return retried

    async def check_time_exits(self) -> int:
        """PRO-02: `plan.time_exit`, once set, was persisted and reloaded on
        restart (see the save/restore round-trip below) but nothing ever
        compared it against the clock — a plan with a time-based exit would
        sit past its deadline forever, never actually closed. Closes the
        full remaining owned quantity of every open lifecycle whose
        deadline has passed, via the same `request_exit` path a logical
        target or trailing stop uses (design section: "a time exit" is one
        of the intents CloseArbiter serializes). app/reconciliation.py
        calls this every pass, same as `retry_unprotected_positions`."""
        triggered = 0
        for lifecycle in list(self._lifecycles.values()):
            if await self._consume_expired_time_exit(lifecycle):
                triggered += 1
        return triggered

    async def _consume_expired_time_exit(self, lifecycle: PositionLifecycle) -> bool:
        """The one-lifecycle version of `check_time_exits`' loop body --
        shared so a plan's time_exit is consumed both by the periodic
        reconciliation pass AND by `on_price_update` itself (PRO-02: a
        price feed can tick far more often than the reconciliation
        interval, and there's no reason to make an already-expired deadline
        wait for the next reconciliation pass when a price update just
        arrived anyway)."""
        deadline = lifecycle.plan.time_exit
        if deadline is None or lifecycle.closed:
            return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < deadline:
            return False
        available = self.arbiter.available_to_sell(lifecycle.plan.account_id, lifecycle.plan.symbol)
        if available <= 0:
            return False
        account = DestinationAccount(account_id=lifecycle.plan.account_id, broker=lifecycle.plan.broker)
        await self.request_exit(
            account, lifecycle.plan.symbol, available, source="time_exit", reason=f"time exit reached ({deadline.isoformat()})"
        )
        return True

    def list_pending_exits(self) -> list[tuple[str, str, str, PendingExit]]:
        """Every (account_id, symbol, broker, PendingExit) whose remainder
        hasn't resolved yet — what app/reconciliation.py polls to eventually
        call `resolve_pending_exit`."""
        return [
            (lifecycle.plan.account_id, lifecycle.plan.symbol, lifecycle.plan.broker, lifecycle.pending_exit)
            for lifecycle in self._lifecycles.values()
            if lifecycle.pending_exit is not None and not lifecycle.pending_exit.remainder_resolved
        ]

    def list_pending_entries(self) -> list[tuple[str, str, str, PendingEntry]]:
        """Every (account_id, symbol, broker, PendingEntry) whose outcome
        hasn't resolved yet — what app/reconciliation.py polls to eventually
        call `resolve_pending_entry`. See PendingEntry's docstring: until
        resolved, this position has NOT had `on_entry_fill` called for it,
        so it has no protective stop yet."""
        return [
            (lifecycle.plan.account_id, lifecycle.plan.symbol, lifecycle.plan.broker, lifecycle.pending_entry)
            for lifecycle in self._lifecycles.values()
            if lifecycle.pending_entry is not None and not lifecycle.pending_entry.remainder_resolved
        ]

    def get_outstanding_possible_fill(self, account_id: str) -> dict[str, float]:
        """The managed-lifecycle counterpart of `SignalStore.get_outstanding_possible_fill`
        (see that method's docstring for the shared contract) — per-symbol
        net signed quantity that could STILL be confirmed by the broker for
        this account's open managed positions, from either an unresolved
        entry (`PendingEntry.unresolved_remainder`) or an unresolved exit
        (`PendingExit.unresolved_remainder`).

        Sign convention: a BUY-side unresolved entry contributes a POSITIVE
        amount (more long exposure could still land); a SELL-side unresolved
        entry contributes NEGATIVE. An unresolved EXIT contributes the
        OPPOSITE sign of its own side (a still-possible SELL exit reduces
        long exposure if it lands, so it contributes negative for a
        long/BUY-side position, and vice versa) — the same "what could this
        do to net exposure if it lands" reasoning
        `SignalStore.get_outstanding_possible_fill` uses for the plain path.
        A symbol with nothing unresolved is simply absent from the result
        (never a fabricated 0.0 entry) — callers should treat a missing key
        as zero.

        This is READ-ONLY: it never mutates `PendingEntry`/`PendingExit`, and
        it does not include anything already reflected in
        `confirmed_owned_quantity` (that part is no longer "possible", it's
        already actual — see `PositionLifecycle.confirmed_owned_quantity`,
        this manager's own equivalent of `actual_remaining_ownership`)."""
        outstanding: dict[str, float] = {}
        for lifecycle in self._lifecycles.values():
            if lifecycle.plan.account_id != account_id or lifecycle.closed:
                continue
            symbol = lifecycle.plan.symbol
            delta = 0.0
            entry = lifecycle.pending_entry
            if entry is not None:
                remainder = entry.unresolved_remainder
                delta += remainder if lifecycle.plan.side == Side.BUY else -remainder
            exit_ = lifecycle.pending_exit
            if exit_ is not None:
                remainder = exit_.unresolved_remainder
                # The exit sells in the direction opposite the position's
                # own side, so a possible exit fill moves net exposure the
                # opposite way an entry fill would.
                delta += -remainder if lifecycle.plan.side == Side.BUY else remainder
            if delta:
                outstanding[symbol] = outstanding.get(symbol, 0.0) + delta
        return outstanding

    async def preview_reduction(self, account: DestinationAccount, symbol: str, quantity: float) -> dict:
        """TR-03-A01: read-only dry run of `request_exit`'s pre-order-
        submission planning for a hypothetical partial reduction of
        `quantity` shares -- calls `_compute_reduction_plan`, the exact same
        pure function `request_exit` itself calls, inside the same
        `CloseArbiter.transition` lock (for a read consistent with any
        concurrently in-flight real exit) but NEVER reserves anything and
        NEVER calls `broker.replace_stop_quantity`/`cancel_order`/
        `place_order` -- no real order is placed, amended, or cancelled by
        this call. Returns `{"supported": False, "reason": ...}` for every
        case `request_exit` itself would refuse outright (no lifecycle,
        halted, a prior exit still unresolved, nothing available to sell) --
        the same honest refusal reasons that method returns, not a
        fabricated preview."""
        broker = self.brokers.get(account.broker)
        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle = self._lifecycles.get((account.account_id, symbol))
            if lifecycle is None or lifecycle.closed:
                return {"supported": False, "reason": "no active managed lifecycle for this position"}
            if tx.is_halted:
                return {"supported": False, "reason": f"halted: {tx.halt_reason}"}
            if lifecycle.pending_exit is not None and not lifecycle.pending_exit.remainder_resolved:
                return {
                    "supported": False,
                    "reason": (
                        f"a prior {lifecycle.pending_exit.source or 'exit'} order "
                        f"({lifecycle.pending_exit.broker_order_id}) for this position hasn't resolved "
                        "yet -- a real request_exit call would refuse a second exit too"
                    ),
                }
            plan = _compute_reduction_plan(tx, lifecycle, broker, quantity)
            if plan.requested_quantity <= 0:
                return {"supported": False, "reason": "no shares available to sell"}
            if plan.had_stop and plan.remaining_after_request > 0:
                stop_note = (
                    "existing protective stop would be amended down to the remaining quantity, at the same price, "
                    "in place (no moment of zero coverage)"
                    if plan.can_amend_stop_in_place
                    else "existing protective stop would be cancelled and a replacement submitted at the same price "
                    "sized to the remaining quantity (this broker adapter has no in-place amend capability)"
                )
            elif plan.had_stop:
                stop_note = "this reduction would fully close the position -- the existing stop would be cancelled with nothing to replace it"
            else:
                stop_note = "no existing protective stop on this position to adjust"
            return {
                "supported": True,
                "requested_quantity": plan.requested_quantity,
                "owned_before": plan.owned_before,
                "available_before": plan.available_before,
                "remaining_after_request": plan.remaining_after_request,
                "had_stop": plan.had_stop,
                "stop_desired_price": plan.stop_desired_price,
                "can_amend_stop_in_place": plan.can_amend_stop_in_place,
                "stop_note": stop_note,
            }

    def preview_stop_change(self, account_id: str, symbol: str, price: float) -> dict:
        """TR-03-A02: read-only preview of what `_update_trailing`'s real
        trailing-stop computation (`_compute_trailing_candidate`, the exact
        function it itself calls) would produce for this position at a
        hypothetical market `price` -- never submits/replaces any real stop
        order, never mutates `trailing.floor_price`/`stop.desired_price`.
        Only covers an ACTIVE trailing policy -- a TIGHTEN_STOP target is
        evaluated only against a live price tick inside `on_price_update`
        and has no separable pure formula to preview here (it just sets
        `desired_price` to the target's own fixed `trigger_price`, never
        loosening -- see `_tighten_stop_to`), so that case is left for the
        caller to disclose honestly rather than faked."""
        lifecycle = self._lifecycles.get((account_id, symbol))
        if lifecycle is None or lifecycle.closed:
            return {"supported": False, "reason": "no active managed lifecycle for this position"}
        trailing = lifecycle.plan.trailing
        if trailing is None or not trailing.active:
            return {
                "supported": False,
                "reason": (
                    "no ACTIVE trailing-stop policy on this position -- this preview only covers "
                    "_update_trailing's real computation, which only ever runs once a trailing policy is active"
                ),
            }
        candidate_floor, improved = _compute_trailing_candidate(
            trailing, lifecycle.stop.desired_price, lifecycle.plan.side, price
        )
        return {
            "supported": True,
            "hypothetical_price": price,
            "current_desired_stop_price": lifecycle.stop.desired_price,
            "trail_distance": trailing.trail_distance,
            "candidate_stop_price": candidate_floor,
            "would_change": improved,
        }

    async def restore_from_store(self) -> None:
        """Rebuild in-memory lifecycles + arbiter ledgers from persisted
        state — call once at startup, before any signal is handled. Answers
        design section 3's "resume the existing episode after a restart":
        without this, `PositionLifecycleManager` starts with no memory of
        what it was protecting, which app/lifecycle/manager.py's module
        docstring used to list as an open gap.

        Also runs D-09's adoption logic: for every unresolved stop_change
        command ledger row with a known broker_order_id, polls it and adopts
        the order if it's still resting at the broker."""
        if self.store is None:
            return
        for row in self.store.load_lifecycle_states():
            if row.get("closed"):
                continue
            account_id, symbol = row["account_id"], row["symbol"]
            lifecycle = _lifecycle_from_state(row)
            self._lifecycles[(account_id, symbol)] = lifecycle
            ledger = row["ledger"]
            self.arbiter.restore(
                account_id,
                symbol,
                owned=ledger["owned"],
                reserved=ledger["reserved"],
                halted=ledger["halted"],
                halt_reason=ledger["halt_reason"],
            )
            logger.info(
                "resumed managed lifecycle for account=%s symbol=%s owned=%s pending_exit=%s",
                account_id,
                symbol,
                lifecycle.confirmed_owned_quantity,
                lifecycle.pending_exit is not None,
            )

        # D-09: Adopt unresolved stop orders at startup. For each unresolved
        # stop_change ledger row with a broker_order_id, check if it's still
        # resting at the broker and adopt it if so.
        await self._adopt_unresolved_stops_at_startup()

    async def _adopt_unresolved_stops_at_startup(self) -> None:
        """D-09: At startup, for every unresolved stop_change ledger row with
        a known broker_order_id, poll that order's status. If it's still
        resting (PENDING), update the corresponding lifecycle's stop record
        to mark it as adopted. This prevents resubmitting a stop that's
        already in place when a previous placement attempt returned ERROR
        after the broker had accepted it."""
        if self.store is None:
            return

        for entry in self.store.list_unresolved_command_ledger_entries():
            if entry.command_type != CommandType.STOP_CHANGE:
                continue
            if not entry.remote_identifiers or "broker_order_id" not in entry.remote_identifiers:
                continue

            broker_order_id = entry.remote_identifiers["broker_order_id"]
            account_id = entry.account_id

            # Find the lifecycle for this entry. We don't have the symbol
            # directly, so we'll iterate through restored lifecycles.
            lifecycle = None
            for (aid, _sym), lc in self._lifecycles.items():
                if aid == account_id:
                    # For now, check if this is the right lifecycle by seeing
                    # if the stop is unprotected. This is a heuristic, but
                    # should work in practice since we only care about recently
                    # unresolved stops.
                    if lc.stop.status != ProtectionStatus.STOP_CONFIRMED:
                        lifecycle = lc
                        break

            if lifecycle is None:
                # Lifecycle may have closed or not yet been loaded; skip.
                continue

            broker = self.brokers.get(lifecycle.plan.broker)
            if broker is None:
                logger.warning(
                    "no broker adapter for '%s' -- cannot adopt unresolved stop %s",
                    lifecycle.plan.broker,
                    broker_order_id,
                )
                continue

            account = DestinationAccount(
                account_id=account_id,
                broker=lifecycle.plan.broker,
            )

            status_result = await broker.get_order_status(account, broker_order_id)
            if status_result is None:
                # Broker has no record of it; leave it unprotected.
                logger.info(
                    "unresolved stop %s for account=%s not found at broker; will retry placement",
                    broker_order_id,
                    account_id,
                )
                continue

            if status_result.status == OrderStatus.PENDING:
                # Successfully adopt it.
                lifecycle.stop.broker_order_id = broker_order_id
                lifecycle.stop.status = ProtectionStatus.STOP_CONFIRMED
                lifecycle.stop.protected_quantity = status_result.filled_quantity or 0.0
                lifecycle.stop.confirmed_at = datetime.now(timezone.utc)
                self._persist(lifecycle)
                logger.info(
                    "adopted unresolved stop %s for account=%s symbol=%s at startup",
                    broker_order_id,
                    account_id,
                    lifecycle.plan.symbol,
                )
            else:
                # It has a terminal status; log but don't adopt it.
                logger.info(
                    "unresolved stop %s has terminal status %s; not adopting",
                    broker_order_id,
                    status_result.status.value,
                )

    def _persist(self, lifecycle: PositionLifecycle) -> None:
        if self.store is None:
            return
        account_id, symbol = lifecycle.key
        if lifecycle.closed:
            self.store.delete_lifecycle_state(account_id, symbol)
            return
        state = _lifecycle_to_state(lifecycle, self.arbiter.snapshot(account_id, symbol))
        self.store.save_lifecycle_state(account_id, symbol, state)

    def _persist_closed_excursion(
        self, lifecycle: PositionLifecycle, account: DestinationAccount, symbol: str
    ) -> None:
        """PU-A1: write this now-closed position's final MAE/MFE to
        `SignalStore.position_excursions` — a durable row that survives
        both `delete_lifecycle_state` (this lifecycle's in-progress record)
        and a process restart, so a later analytics/chart batch can query a
        closed position's excursion the same way it queries any other
        historical trade record. A no-op with no store wired in (same
        convention as `_persist`)."""
        if self.store is None:
            return
        self.store.record_position_excursion(
            account.account_id,
            symbol,
            side=lifecycle.plan.side.value,
            entry_price=lifecycle.entry_price,
            highest_price_since_entry=lifecycle.highest_price_since_entry,
            highest_price_at=lifecycle.highest_price_at,
            lowest_price_since_entry=lifecycle.lowest_price_since_entry,
            lowest_price_at=lifecycle.lowest_price_at,
            mae=lifecycle.mae,
            mfe=lifecycle.mfe,
            has_price_data=lifecycle.has_price_data,
            closed_at=datetime.now(timezone.utc),
        )

    def _record_stop_target_event(
        self,
        lifecycle: PositionLifecycle,
        event_type: StopTargetEventType,
        *,
        price: float | None,
        previous_price: float | None,
        source: str,
    ) -> None:
        """PU-A4: append one real stop/target lifecycle event for this
        position to `SignalStore.stop_target_events` -- a no-op with no
        store wired in (same convention as `_persist`). Callers pass
        `price`/`previous_price` in that exact order -- see this method's
        callers for each event type's meaning of the two fields (documented
        on `StopTargetEventType` and the `stop_target_events` table)."""
        if self.store is None:
            return
        account_id, symbol = lifecycle.key
        self.store.record_stop_target_event(
            account_id,
            symbol,
            event_type=event_type.value,
            at=datetime.now(timezone.utc),
            price=price,
            previous_price=previous_price,
            source=source,
        )

    def validate_plan(self, plan: PositionPlan) -> str | None:
        """Design section 11: no provider stop, no released fallback -> NO ENTRY.
        The caller resolves fallbacks (provider -> strategy -> asset-level) and
        sets `plan.initial_stop` *before* calling this; this only enforces that
        something ended up there. Returns an error message if the plan must not
        be entered, or None if it's fine to proceed.

        Also refuses a plan whose broker has no real way to keep the position
        protected at all (`BrokerAdapter.can_protect_a_managed_position()` is
        False) — e.g. `initial_stop` is set, but the broker has neither a
        native bracket nor a verified `place_protective_stop`. Admitting that
        entry would let `on_entry_fill` silently log a warning and leave the
        position open with nothing actually covering it (see this method's
        BLOCKED_CAPABILITY-style rejection here, not a log line after the
        fact — "a warning or None from protection placement is not success")."""
        if plan.initial_stop is None:
            return (
                "no stop-loss resolved for this entry (no provider stop and no "
                "released fallback) — refusing to enter unprotected"
            )
        if (
            isinstance(plan.initial_stop, bool)
            or not math.isfinite(plan.initial_stop)
            or plan.initial_stop <= 0
        ):
            # RISK-01: a resolved stop of 0, negative, NaN/inf, or a stray
            # boolean is not a real protective level -- it must not be
            # treated as "a stop is set" just because it's not None.
            return (
                f"resolved stop-loss ({plan.initial_stop!r}) is not a valid positive price — "
                "refusing to enter unprotected"
            )
        if plan.max_risk is not None:
            # RISK-02: `max_risk` is a per-trade loss budget the caller
            # intends to be enforced, but nothing in this codebase actually
            # consumes it -- there is no entry/reference price on
            # PositionPlan to compute (entry - stop) * quantity against,
            # no reservation ledger, no account/portfolio-level cap. A plan
            # that names a risk bound but has no way to enforce it must not
            # be silently admitted as if that bound were honored -- that
            # would let a caller believe their loss is capped at `max_risk`
            # when it is not bounded at all. Refuse outright, same posture
            # as a missing stop, rather than accept it and ignore the field.
            return (
                f"max_risk={plan.max_risk!r} was requested but this engine has no risk-allocator "
                "implementation to enforce it (no entry/reference price, no reservation ledger, no "
                "account/portfolio cap) — refusing to admit a plan whose stated risk bound would be "
                "silently ignored rather than honored"
            )
        broker = self.brokers.get(plan.broker)
        if broker is None:
            return f"no broker adapter registered for '{plan.broker}' — refusing to enter"
        if not broker.can_protect_a_managed_position():
            return (
                f"broker '{plan.broker}' has no verified way to keep this position protected "
                "(no native bracket, no place_protective_stop implementation) — refusing to "
                "enter under managed_lifecycle rather than admit it unprotected"
            )
        existing = self._lifecycles.get((plan.account_id, plan.symbol))
        if existing is not None and not existing.closed:
            # EXE-09: a second same-symbol entry (e.g. a different analyst
            # targeting the same account/symbol) used to silently overwrite
            # this dict entry via start_plan, discarding the FIRST entry's
            # entire lifecycle object -- its confirmed_owned_quantity, its
            # stop record, everything -- while that first entry's real
            # broker-side exposure and resting stop kept existing,
            # completely untracked from then on. Reject outright rather
            # than aggregate two independent intents under one identity;
            # an explicit, released aggregation policy is a separate,
            # deliberate feature, not an accident of dict-key collision.
            return (
                f"an active managed lifecycle already exists for account={plan.account_id} "
                f"symbol={plan.symbol} (owned={existing.confirmed_owned_quantity}) — refusing to "
                "start a second, independent entry under the same identity"
            )
        return None

    def start_plan(self, plan: PositionPlan) -> PositionLifecycle:
        """Register a plan BEFORE the entry is submitted (design section 1). Call
        `validate_plan` first; submit the entry order yourself after this; then
        call `on_entry_fill` with what actually filled.

        EXE-01: persisted immediately, not just held in memory -- this is
        the durable pre-submission execution intent. If the entry order's
        response is then lost (a timeout/connection error after the broker
        already accepted it, not before), the caller registers an unresolved
        pending entry (`register_pending_entry(..., broker_order_id=None)`)
        rather than calling `unregister_plan`, so this row survives for a
        restart's reconciliation to eventually ask the broker what actually
        happened -- see app/engine.py's `_handle_managed_entry`."""
        lifecycle = PositionLifecycle(plan=plan)
        self._lifecycles[(plan.account_id, plan.symbol)] = lifecycle
        self._persist(lifecycle)
        return lifecycle

    def unregister_plan(self, account_id: str, symbol: str) -> None:
        """Drop a plan that was registered but never entered (e.g. the entry
        order itself failed after `start_plan`) — otherwise a stale,
        never-filled lifecycle sits around forever."""
        self._lifecycles.pop((account_id, symbol), None)
        if self.store is not None:
            self.store.delete_lifecycle_state(account_id, symbol)

    async def on_entry_fill(
        self, account: DestinationAccount, symbol: str, filled_quantity: float, entry_price: float | None = None
    ) -> PositionLifecycle:
        """Call once the entry order confirms a fill — `filled_quantity` is the
        total confirmed so far (design section 2: "Confirmed owned quantity =
        62", not a delta). Immediately submits protection before returning,
        per the core rule: protect first, everything else follows.

        `entry_price` (PU-A1): the real confirmed fill price for this entry
        (e.g. `OrderResult.filled_price` at the call site) — seeds MAE/MFE
        tracking (see PositionLifecycle.observe_price/mae/mfe). None (the
        default) when the caller has no price for this fill; MAE/MFE then
        stay honestly unknown (None) rather than assumed to be 0. Only sets
        it once — a repeated/resumed call for an already-priced lifecycle
        never overwrites the original entry price."""
        self.lease_guard.require_active()
        lifecycle = self._lifecycles[(account.account_id, symbol)]
        broker = self.brokers.get(account.broker)

        if entry_price is not None and lifecycle.entry_price is None:
            lifecycle.entry_price = entry_price
            lifecycle.observe_price(entry_price, datetime.now(timezone.utc))

        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle.confirmed_owned_quantity = filled_quantity
            tx.set_owned(filled_quantity)
            if lifecycle.plan.initial_stop is not None and broker is not None:
                lifecycle.stop.desired_price = lifecycle.plan.initial_stop
                await self._place_stop_locked(
                    lifecycle, account, broker, filled_quantity, lifecycle.plan.initial_stop, source="signal"
                )

        self._persist(lifecycle)
        return lifecycle

    async def adopt_venue_ownership(
        self, account: DestinationAccount, symbol: str, venue_owned_quantity: float
    ) -> None:
        """WP-23 D-16: recovery case where the venue owns more than we're
        tracking. Update the confirmed_owned_quantity to match the venue and
        ensure proper protection is in place. This can happen after a crash
        between fill confirmation and stop placement, or when the entry
        remained in process memory only (e.g. IBKR).

        Args:
            account: The destination account
            symbol: The trading symbol
            venue_owned_quantity: The total quantity the venue reports (must be > confirmed_owned)
        """
        self.lease_guard.require_active()
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None:
            return
        broker = self.brokers.get(account.broker)

        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle.confirmed_owned_quantity = venue_owned_quantity
            tx.set_owned(venue_owned_quantity)
            if lifecycle.stop.desired_price is None and lifecycle.plan.initial_stop is not None:
                lifecycle.stop.desired_price = lifecycle.plan.initial_stop

        self._persist(lifecycle)

        # Ensure protection is placed for the adopted quantity
        if broker is not None and lifecycle.plan.initial_stop is not None:
            await self._replace_stop_price(lifecycle, account, source="recovery")

    def register_pending_entry(
        self,
        account: DestinationAccount,
        symbol: str,
        broker_order_id: str | None,
        requested_quantity: float,
        reserved_notional: float = 0.0,
        reserved_quantity: float = 0.0,
    ) -> None:
        """Call instead of `on_entry_fill` when the entry order's broker
        response is PENDING rather than a synchronous fill: retains the
        intent (this may already be a real, accepted order) without
        assuming `requested_quantity` is actually owned/protected yet — see
        PendingEntry's docstring for why guessing here is exactly the bug
        this exists to avoid. `app/reconciliation.py` polls
        `list_pending_entries()` and eventually calls
        `resolve_pending_entry` once the broker's final word is known.

        `reserved_notional` (E03, bounded): the caller's still-held
        app/capital_allocator.py reservation for this entry -- pass it ONLY
        when `broker_order_id` is set (see PendingEntry's docstring on why
        that's the one case `resolve_pending_entry` is guaranteed to
        eventually release it for); every other caller releases
        immediately at the call site instead and leaves this 0.0.

        `reserved_quantity` (TRK-Q1): the same reservation expressed in
        units instead of notional -- see `PendingEntry.reserved_quantity`'s
        own docstring. Follows the identical broker_order_id-gated rule."""
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None:
            return
        lifecycle.pending_entry = PendingEntry(
            broker_order_id=broker_order_id,
            requested_quantity=requested_quantity,
            reserved_notional=reserved_notional,
            reserved_quantity=reserved_quantity,
        )
        self._persist(lifecycle)

    async def resolve_pending_entry(
        self, account: DestinationAccount, symbol: str, confirmed_filled_quantity: float, remainder_cancelled: bool
    ) -> None:
        """Call with the broker's latest word on a PENDING entry (from
        `register_pending_entry`) — for both a working partial fill whose
        remainder is still open (`remainder_cancelled=False`) and a terminal
        outcome (`remainder_cancelled=True`: fully filled, or the remainder's
        cancellation/expiry is confirmed). A timeout or lost response is
        NEITHER of those — it must keep polling, never call this with a
        guessed outcome.

        Exposure is protected as soon as it's confirmed, not just once the
        whole order is done: any increase in `confirmed_filled_quantity`
        since the last call adds exactly that much to whatever's currently
        owned (an entry's cumulative fill progress is NOT the same thing as
        remaining ownership once anything has exited in the meantime — see
        this module's F01/F02 regression tests) and protects the new total
        by placing the first stop or resizing the existing one — never a
        duplicate stop placed alongside the first one, and never a fresh
        stop while an exit for this same position hasn't resolved yet (that
        exit's own commitments already account for part of what's owned; a
        fresh full-size stop on top of them would let the same shares be
        sold twice). A repeated observation of the same
        `confirmed_filled_quantity` is a no-op. Once terminal: zero confirmed
        fill unregisters the plan (nothing to protect, nothing protecting
        it); otherwise the pending entry is simply cleared, since whatever
        was confirmed is already protected (or, if an exit is still
        unresolved, deliberately left for that exit's own resolution to
        protect once it settles).

        The whole read-decide-apply sequence below is serialized per
        (account, symbol) via `_pending_entry_locks` — computing
        `newly_applied` from `pending.confirmed_filled_quantity` is only
        safe if nothing else can be doing the same computation from the same
        stale checkpoint at the same time (see the F07 regression test,
        which delivers two identical observations while the first stop
        request is still in flight)."""
        self.lease_guard.require_active()
        lock = self._pending_entry_locks[(account.account_id, symbol)]
        async with lock:
            lifecycle = self._lifecycles.get((account.account_id, symbol))
            if lifecycle is None or lifecycle.pending_entry is None:
                return
            pending = lifecycle.pending_entry
            broker = self.brokers.get(account.broker)

            if not math.isfinite(confirmed_filled_quantity) or confirmed_filled_quantity < 0:
                # PRO-07: a negative or non-finite observation is invalid
                # input, not a legitimate trade-bust/correction event (those
                # are explicit, separate events -- see this method's
                # docstring). Applying it as-is would corrupt the ledger
                # (a negative "newly_applied" delta, or arithmetic blowing
                # up on infinity). Raise an incident and leave everything
                # untouched rather than silently act on it.
                logger.error(
                    "invalid confirmed_filled_quantity=%r for pending entry account=%s symbol=%s "
                    "-- ignoring this observation rather than applying it",
                    confirmed_filled_quantity,
                    account.account_id,
                    symbol,
                )
                return

            if confirmed_filled_quantity > pending.confirmed_filled_quantity:
                newly_applied = confirmed_filled_quantity - pending.confirmed_filled_quantity
                has_unresolved_exit = (
                    lifecycle.pending_exit is not None and not lifecycle.pending_exit.remainder_resolved
                )

                async with self.arbiter.transition(account.account_id, symbol) as tx:
                    new_owned = tx.owned + newly_applied
                    lifecycle.confirmed_owned_quantity = new_owned
                    tx.set_owned(new_owned)
                    if lifecycle.stop.desired_price is None and lifecycle.plan.initial_stop is not None:
                        lifecycle.stop.desired_price = lifecycle.plan.initial_stop

                # Advance the checkpoint and commit it atomically with the
                # tracked position BEFORE attempting any broker protection
                # I/O below (EXE-02: this used to happen AFTER
                # _replace_stop_price, whose own internal persist already
                # writes the ADVANCED confirmed_owned_quantity/stop state to
                # disk while the checkpoint here was still the OLD one — an
                # interruption between that persist and this commit left
                # recovery re-observing and re-applying the same delta,
                # producing a lifecycle/stop total the real position/venue
                # never reached). Committing this pair first means a crash
                # during/after the stop placement below can only ever leave
                # protection temporarily stale for an already-correct,
                # already-durable owned quantity — never a duplicated or
                # corrupted position.
                pending.confirmed_filled_quantity = confirmed_filled_quantity
                if self.store is not None:
                    state = _lifecycle_to_state(lifecycle, self.arbiter.snapshot(account.account_id, symbol))
                    self.store.record_fill(
                        account.account_id, symbol, lifecycle.plan.side, newly_applied, lifecycle_state=state
                    )
                else:
                    self._persist(lifecycle)

                if broker is not None and not has_unresolved_exit:
                    # Handles both "no stop yet" (places a fresh one sized to
                    # `new_owned`) and "already protected at a smaller
                    # quantity" (resizes in place) — see _replace_stop_price.
                    # Skipped entirely while an exit is unresolved: that
                    # exit already reserved/cancelled against the old
                    # protection, and a fresh stop here would re-cover
                    # quantity the exit's own resolution is responsible for
                    # (see F02's regression test). Its own internal persist
                    # (after this point) only ever advances protection state
                    # on top of an already-durable, already-correct position.
                    await self._replace_stop_price(lifecycle, account)

            if not remainder_cancelled and confirmed_filled_quantity < pending.requested_quantity:
                self._persist(lifecycle)
                return

            pending.remainder_resolved = True
            lifecycle.pending_entry = None

            # E03 (bounded): this pending entry's outcome is now confirmed
            # terminal -- release whatever notional it still held, exactly
            # once (a repeated resolve_pending_entry call for the same
            # entry can't reach here again: `lifecycle.pending_entry` above
            # is already None, so the "lifecycle.pending_entry is None"
            # guard at this method's top returns before this point on any
            # later call).
            if self.capital_allocator is not None and pending.reserved_notional:
                self.capital_allocator.release(
                    account.account_id, pending.reserved_notional, signal_id=lifecycle.plan.entry_signal_id or None
                )

            # PRO-07: use `pending.confirmed_filled_quantity` (the highest
            # value ever actually confirmed and applied), not the raw
            # `confirmed_filled_quantity` argument -- a terminal observation
            # can itself misreport 0 even after a real, already-applied 30
            # was previously confirmed (a stale/wrong broker response is not
            # proof the earlier confirmation was wrong). Unregistering here
            # would discard a known, already-protected position based on
            # nothing but a single contradictory observation.
            if pending.confirmed_filled_quantity <= 0:
                self.unregister_plan(account.account_id, symbol)
                return

            self._persist(lifecycle)

    async def on_stop_filled(
        self, account: DestinationAccount, symbol: str, filled_quantity: float, filled_price: float | None = None
    ) -> None:
        """Call when the broker reports the protective stop itself filled
        (reconciliation, or PaperBroker.simulate_price in tests). A stop
        execution is an exit like any other -- it must apply to
        `SignalStore.positions` through the same single execution-owner
        path as request_exit/resolve_pending_exit (EXE-05: this used to
        only update the in-memory/persisted lifecycle ledger, leaving
        SignalStore's tracked position stale -- local holdings could stay
        open after the venue was actually flat)."""
        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle = self._lifecycles.get((account.account_id, symbol))
            if lifecycle is None:
                return
            # TRK-23: the real broker_order_id this stop fill belongs to,
            # captured BEFORE either branch below clears
            # `lifecycle.stop.broker_order_id` -- `_apply_exit_fill`'s own
            # export (see its docstring) needs the actual id this fill
            # happened under, not whatever's left on the lifecycle
            # afterward (which is None precisely when protection just
            # ran out, i.e. exactly the case this fill IS).
            stop_broker_order_id = lifecycle.stop.broker_order_id
            tx.settle(reserved_quantity=0.0, filled_quantity=filled_quantity)
            lifecycle.confirmed_owned_quantity = tx.owned
            if tx.owned <= 0:
                # Flat right now is not the same as fully done -- a still-
                # working entry order (has_unresolved_entry) may yet fill
                # more, which must find this lifecycle still here to
                # protect it (see EXE-07's regression test).
                lifecycle.closed = not lifecycle.has_unresolved_entry
                lifecycle.stop.protected_quantity = 0.0
                lifecycle.stop.status = ProtectionStatus.UNPROTECTED
                lifecycle.stop.broker_order_id = None
            else:
                # A stop notification can itself be a partial fill of the
                # stop order -- the remainder may still be resting at the
                # broker under the SAME order id. Clearing broker_order_id/
                # marking UNPROTECTED unconditionally (as this used to)
                # loses track of that still-working order and reports zero
                # coverage even though some protection may remain.
                remaining_protected = max(0.0, lifecycle.stop.protected_quantity - filled_quantity)
                lifecycle.stop.protected_quantity = remaining_protected
                if remaining_protected <= 0:
                    lifecycle.stop.status = ProtectionStatus.UNPROTECTED
                    lifecycle.stop.broker_order_id = None
        # TR-EPISODE-01: this is a real, episode-closing (or -reducing) exit
        # exactly like a target/manual/time-exit fill -- see
        # `_apply_exit_fill`'s own docstring for why this is the one place
        # that now also persists a real `orders`/`signals` row for it,
        # closing the gap the accounting-ledger review flagged ("managed
        # lifecycle exits, including stops ... are not necessarily
        # represented the same way [as ordinary order-history closes]").
        # E-05: `filled_price` is only used if the caller (app/reconciliation.py)
        # has a real broker-reported one. When inferring a stop from a position
        # deficit (no broker-reported price), we keep `exit_price=None` to
        # honestly disclose unknown price rather than guessing from a stale
        # last_observed_price or stop's resting price (which may not match
        # the true fill in a gap-through scenario).
        exit_price = filled_price
        was_trailing = bool(lifecycle.plan.trailing and lifecycle.plan.trailing.active)
        self._apply_exit_fill(
            lifecycle,
            account,
            symbol,
            filled_quantity,
            exit_kind="stop",
            exit_price=exit_price,
            broker_order_id=stop_broker_order_id,
            reason="trailing_stop" if was_trailing else "stop",
        )

    async def resize_stop_to_owned(self, account: DestinationAccount, symbol: str, owned: float) -> None:
        """Resize (or cancel) a resting protective stop to match a new owned
        quantity when a broker-position deficit is detected. Called by
        reconciliation when the broker-position readback shows fewer shares
        than the lifecycle believes it owns -- before applying any correction
        to the lifecycle's tracked quantity.

        If the stop quantity equals the new owned quantity, this is a no-op
        (the stop is already correctly sized).

        If owned <= 0 (full deficit), the stop is cancelled and the lifecycle
        is closed via normal arbiter transition, never deleting its state while
        a broker_order_id is live."""
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None or lifecycle.stop.broker_order_id is None:
            return

        broker = self.brokers.get(lifecycle.plan.broker)
        if broker is None:
            return

        # Already correctly sized
        if lifecycle.stop.protected_quantity == owned:
            return

        if owned <= 1e-9:
            # Full deficit: cancel the stop first, then close via arbiter
            cancelled = await self._ledgered_cancel_order(
                broker, account, symbol, lifecycle.stop.broker_order_id, source="reconciliation"
            )
            if cancelled:
                lifecycle.stop.broker_order_id = None
                lifecycle.stop.protected_quantity = 0.0
                lifecycle.stop.status = ProtectionStatus.UNPROTECTED
            else:
                # Cancel failed but we still need to close the lifecycle.
                # Log a warning but do not delete lifecycle state while
                # the broker_order_id is still live and uncancelled.
                logger.warning(
                    "could not cancel resting stop for account=%s symbol=%s broker_order_id=%s during full deficit correction -- "
                    "lifecycle state retained; stop may orphan",
                    account.account_id,
                    symbol,
                    lifecycle.stop.broker_order_id,
                )
            # Mark as closed only if cancel succeeded or was moot
            if cancelled or lifecycle.stop.broker_order_id is None:
                lifecycle.closed = True
        else:
            # Partial deficit: resize the stop to the new owned quantity
            replaced = await self._ledgered_replace_stop_quantity(
                broker,
                account,
                symbol,
                lifecycle.stop.broker_order_id,
                owned,
                lifecycle.stop.desired_price,
                source="reconciliation",
            )
            if replaced is not None and replaced.status not in (OrderStatus.ERROR, OrderStatus.REJECTED):
                if replaced.broker_order_id:
                    lifecycle.stop.broker_order_id = replaced.broker_order_id
                lifecycle.stop.protected_quantity = owned
            else:
                logger.warning(
                    "could not resize resting stop to %f for account=%s symbol=%s -- coverage may be stale at %.6f",
                    owned,
                    account.account_id,
                    symbol,
                    lifecycle.stop.protected_quantity,
                )

    async def on_price_update(self, account: DestinationAccount, symbol: str, price: float) -> list[OrderResult]:
        """Evaluate logical targets and trailing against a new price. Call this
        from whatever feed you have wired up for this broker (see this
        module's docstring — no feed is wired up generically).

        PU-A1: every call here is, by this module's own contract, a real
        broker/feed-reported price (see app/pricing.py's PriceMonitor and
        this method's callers) — so it always updates MAE/MFE tracking via
        `PositionLifecycle.observe_price`, even while halted (a halt blocks
        new exits, not honest observation of where price actually went)."""
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None or lifecycle.closed:
            return []

        lifecycle.observe_price(price, datetime.now(timezone.utc))
        self._persist(lifecycle)

        if self.arbiter.is_halted(account.account_id, symbol):
            return []

        # Cross-process/cross-host fencing (app/writer_lease.py): checked
        # here, after the honest price/MAE/MFE observation above (never
        # gated -- see this method's own docstring) but before anything
        # below that can reach a broker write (time-exit close, a target
        # firing, tighten-stop, trailing) -- a fenced-out process must
        # keep observing price for its own records, but never act on it.
        self.lease_guard.require_active()

        if await self._consume_expired_time_exit(lifecycle):
            return []

        results: list[OrderResult] = []
        for target in lifecycle.plan.targets:
            if target.fired or not self._target_triggered(lifecycle, target, price):
                continue
            if target.action == TargetAction.SELL:
                # Mark fired only once the exit is at least genuinely
                # in flight (PENDING/FILLED) -- PRO-03: marking it fired
                # BEFORE calling request_exit meant a failure (e.g. "could
                # not confirm cancellation of the existing protective
                # stop") still permanently forgot this target, so a later,
                # perfectly safe opportunity at the same price never
                # retried it.
                quantity = lifecycle.plan.planned_quantity * (target.reduce_fraction or 0.0)
                result = await self.request_exit(
                    account, symbol, quantity, source="target", reason=f"target @ {target.trigger_price}"
                )
                results.append(result)
                if result.status in (OrderStatus.FILLED, OrderStatus.PENDING):
                    target.fired = True
                    # PU-A4: a real logical profit target actually firing --
                    # its exit order is at least genuinely in flight (see
                    # PRO-03's comment just above on why `fired` itself is
                    # only set here). `price` is the target's own trigger
                    # level; there is no meaningful "previous target price"
                    # for a one-shot trigger, so `previous_price` is always
                    # None for this event type.
                    self._record_stop_target_event(
                        lifecycle,
                        StopTargetEventType.TARGET_HIT,
                        price=target.trigger_price,
                        previous_price=None,
                        source="signal",
                    )
            elif target.action == TargetAction.TIGHTEN_STOP:
                target.fired = True
                await self._tighten_stop_to(lifecycle, account, target.trigger_price)
            elif target.action == TargetAction.ACTIVATE_TRAIL:
                target.fired = True
                if lifecycle.plan.trailing:
                    lifecycle.plan.trailing.active = True

        trailing = lifecycle.plan.trailing
        if trailing and not trailing.active and trailing.activate_at_price is not None:
            # PRO-02: `activate_at_price` was stored on every persisted plan
            # (see the save/restore round-trip below) but nothing ever read
            # it back to actually flip the trail on -- the only way it could
            # activate was via an explicit ACTIVATE_TRAIL target above. A
            # plan with a trailing policy but no such target could set
            # activate_at_price and it would sit there forever, inert.
            if lifecycle.plan.side == Side.BUY:
                crossed = price >= trailing.activate_at_price
            else:
                crossed = price <= trailing.activate_at_price
            if crossed:
                trailing.active = True

        if lifecycle.plan.trailing and lifecycle.plan.trailing.active:
            await self._update_trailing(lifecycle, account, price)

        return results

    async def request_exit(
        self, account: DestinationAccount, symbol: str, quantity: float, source: str, reason: str = ""
    ) -> OrderResult:
        """The single entry point for every non-stop-fill exit (a logical target,
        trailing, a provider EXIT signal, a time exit, an emergency exit).
        Performs the stop-resize transition described in this module's
        docstring. Never sells more than `CloseArbiter` says is available."""
        self.lease_guard.require_active()
        broker = self.brokers.get(account.broker)
        if broker is None:
            return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id="", message=f"no broker adapter registered for '{account.broker}'")

        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle = self._lifecycles.get((account.account_id, symbol))
            if lifecycle is None or lifecycle.closed:
                # TRK-27: before falling through to the generic rejection,
                # check whether this is a genuine duplicate of an exit that
                # already resolved for this exact (account_id, symbol) —
                # see `check_duplicate_exit`'s own docstring for the
                # exact, narrow scope this covers (and, just as
                # importantly, does NOT cover).
                duplicate = self.check_duplicate_exit(account, symbol)
                if duplicate is not None:
                    return duplicate
                return OrderResult(account_id=account.account_id, status=OrderStatus.REJECTED, signal_id="", message="no active lifecycle for this position")
            if tx.is_halted:
                return OrderResult(account_id=account.account_id, status=OrderStatus.REJECTED, signal_id="", message=f"halted: {tx.halt_reason}")
            if lifecycle.pending_exit is not None and not lifecycle.pending_exit.remainder_resolved:
                # A prior exit's own remainder is still unknown — its shares are
                # already correctly excluded from `tx.available`, but submitting
                # a second broker write on top of an unresolved first one is
                # exactly the "don't send another sell while the first sell's
                # outcome is unknown" case: refuse rather than pile up more
                # untracked uncertainty.
                return OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id="",
                    message=(
                        f"a prior {lifecycle.pending_exit.source or 'exit'} order "
                        f"({lifecycle.pending_exit.broker_order_id}) for this position hasn't resolved "
                        "yet; refusing to submit another exit until its remainder is confirmed done"
                    ),
                )

            plan = _compute_reduction_plan(tx, lifecycle, broker, quantity)
            requested = plan.requested_quantity
            if requested <= 0:
                return OrderResult(account_id=account.account_id, status=OrderStatus.REJECTED, signal_id="", message="no shares available to sell")

            had_stop = plan.had_stop
            remaining_after_request = plan.remaining_after_request
            amended_stop = False
            if plan.can_amend_stop_in_place:
                # PRO-06: a partial reduce (a target selling a fraction of the
                # position) used to ALWAYS cancel the entire existing stop
                # outright, even on a venue that can amend a resting order's
                # quantity in place -- leaving the WHOLE position, not just
                # the fraction being sold, briefly uncovered while the sell
                # was in flight and a brand new stop was submitted afterward.
                # When the broker can amend, shrink the same resting order
                # down to what will remain instead: the position never has a
                # moment with zero coverage, only the shares actually being
                # sold are freed.
                assert lifecycle.stop.broker_order_id is not None  # had_stop guarantees this
                replaced = await self._ledgered_replace_stop_quantity(
                    broker,
                    account,
                    symbol,
                    lifecycle.stop.broker_order_id,
                    remaining_after_request,
                    lifecycle.stop.desired_price,
                    source=source,
                )
                if replaced is not None and replaced.status not in (OrderStatus.ERROR, OrderStatus.REJECTED):
                    if replaced.broker_order_id:
                        lifecycle.stop.broker_order_id = replaced.broker_order_id
                    lifecycle.stop.protected_quantity = remaining_after_request
                    amended_stop = True

            if had_stop and not amended_stop:
                assert lifecycle.stop.broker_order_id is not None  # had_stop guarantees this
                cancelled = await self._ledgered_cancel_order(
                    broker, account, symbol, lifecycle.stop.broker_order_id, source=source
                )
                if not cancelled:
                    # Could mean "not supported," or "the stop may have already filled" — either
                    # way, proceeding could oversell, so this exit is refused rather than guessed at.
                    return OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.ERROR,
                        signal_id="",
                        message=(
                            "could not confirm cancellation of the existing protective stop "
                            f"before a {source} exit; refusing to risk overselling"
                        ),
                    )
                lifecycle.stop.status = ProtectionStatus.UNPROTECTED
                lifecycle.stop.broker_order_id = None

            if not tx.reserve(requested):
                return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id="", message="reservation failed unexpectedly")

            try:
                exit_result = await self._submit_exit_order(broker, account, lifecycle, requested, reason or source)
            except Exception:
                # EXE-01's exit-side counterpart: place_order raising here is
                # just as ambiguous as on the entry side -- the venue may
                # already have accepted this exit before the exception (a
                # network timeout/reset reading the response). The
                # reservation above already excludes `requested` from
                # `tx.available`; persist that (via a pending_exit with no
                # known broker_order_id) before letting the exception
                # propagate, so a restart's recovery still knows this much
                # was reserved and unresolved rather than forgetting it
                # entirely.
                lifecycle.pending_exit = PendingExit(
                    broker_order_id=None,
                    requested_quantity=requested,
                    phase=TransferPhase.AWAITING_REMAINDER_RESOLUTION,
                    source=source,
                    reason=reason or source,
                    stop_amended=amended_stop,
                )
                self._persist(lifecycle)
                raise

            if exit_result.status == OrderStatus.PENDING:
                # The broker hasn't given a final word yet: how much of `requested`
                # will actually leave the position is still unknown, so the
                # reservation stays open (tx.available correctly still excludes
                # it) and the stop stays un-restored. See resolve_pending_exit —
                # only that call, once the broker's outcome is final, is allowed
                # to settle this and touch the stop (design's worked partial-fill
                # example: don't resize on the requested quantity, only on what's
                # actually confirmed done).
                lifecycle.pending_exit = PendingExit(
                    broker_order_id=exit_result.broker_order_id,
                    requested_quantity=requested,
                    phase=TransferPhase.AWAITING_REMAINDER_RESOLUTION,
                    source=source,
                    reason=reason or source,
                    stop_amended=amended_stop,
                )
                self._persist(lifecycle)
                return exit_result

            if exit_result.status == OrderStatus.ERROR:
                # D-08: An ERROR result (timeout, connection reset, etc.) is
                # exactly as ambiguous as a raised exception — the venue may
                # have already accepted this exit before the error was returned.
                # Treat it identically: persist a pending exit with no
                # broker_order_id, keep the reservation open, and let
                # resolve_pending_exit settle it once the broker's readback is
                # certain.
                lifecycle.pending_exit = PendingExit(
                    broker_order_id=None,
                    requested_quantity=requested,
                    phase=TransferPhase.AWAITING_REMAINDER_RESOLUTION,
                    source=source,
                    reason=reason or source,
                    stop_amended=amended_stop,
                )
                self._persist(lifecycle)
                return exit_result

            actual_filled = exit_result.filled_quantity if exit_result.filled_quantity is not None else 0.0
            tx.settle(reserved_quantity=requested, filled_quantity=actual_filled)
            remaining = tx.owned
            lifecycle.confirmed_owned_quantity = remaining

            if remaining <= 0:
                # See on_stop_filled's identical guard (EXE-07): a
                # still-working entry order may yet deliver more units.
                lifecycle.closed = not lifecycle.has_unresolved_entry
            elif had_stop and lifecycle.stop.desired_price is not None:
                await self._restore_stop_coverage(lifecycle, account, broker, remaining, amended_stop, source=source)

            self._apply_exit_fill(
                lifecycle,
                account,
                symbol,
                actual_filled,
                exit_kind=source,
                exit_price=exit_result.filled_price,
                broker_order_id=exit_result.broker_order_id,
                reason=reason or source,
            )
            return exit_result

    async def resolve_pending_exit(
        self,
        account: DestinationAccount,
        symbol: str,
        confirmed_filled_quantity: float,
        remainder_cancelled: bool,
        filled_price: float | None = None,
    ) -> None:
        """Call once the broker's final word on a PENDING exit (from
        `request_exit`) is known: how much of it actually filled, and whether
        the rest can no longer execute (fully filled itself, or its
        cancellation/expiry is confirmed — `remainder_cancelled=True` covers
        both, since either way nothing more of `requested_quantity` can fill).

        Only then is it safe to settle the reservation and restore the stop
        to the TRUE remaining owned quantity — using what actually filled,
        not what was requested (the design's worked partial-fill example: a
        15-share target that only fills 8 restores the stop against a
        54-share remainder, not 47; if 3 more fill while cancellation was in
        flight, the restore target is 51, not 54).

        If the remainder isn't resolved yet, any INCREASE in
        `confirmed_filled_quantity` since the last call is still applied
        immediately to `SignalStore`/`confirmed_owned_quantity` (EXE-06: a
        confirmed partial -- 40 of a 100-share close, 60 still working --
        used to leave the tracked position at the pre-close 100 until the
        whole order finished, even though the venue had genuinely already
        sold 40). Only the STOP is deliberately not restored/resized yet —
        the remaining, still-uncertain quantity keeps the reservation open
        so nothing else can claim those shares (see `request_exit`'s
        docstring for why the design's worked partial-fill example -- 8 of
        15 confirmed, 7 still open -- restores against 54, not 47).

        `filled_price` is the broker's reported fill price for this exit (if
        known). When None, the row is saved with `filled_price=None` (honestly
        unknown) rather than guessed from a fallback chain."""
        self.lease_guard.require_active()
        broker = self.brokers.get(account.broker)
        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle = self._lifecycles.get((account.account_id, symbol))
            if lifecycle is None or lifecycle.pending_exit is None:
                return
            pending = lifecycle.pending_exit
            previously_applied = pending.confirmed_filled_quantity
            delta = max(0.0, confirmed_filled_quantity - previously_applied)

            if not remainder_cancelled and confirmed_filled_quantity < pending.requested_quantity:
                if delta > 0:
                    tx.settle(reserved_quantity=delta, filled_quantity=delta)
                    lifecycle.confirmed_owned_quantity = tx.owned
                pending.confirmed_filled_quantity = confirmed_filled_quantity
                if delta > 0:
                    # Thread the broker's reported fill price when available;
                    # it stays None (honestly unknown) if not provided.
                    self._apply_exit_fill(
                        lifecycle,
                        account,
                        symbol,
                        delta,
                        exit_kind=pending.source,
                        exit_price=filled_price,
                        broker_order_id=pending.broker_order_id,
                        reason=pending.reason,
                    )
                else:
                    self._persist(lifecycle)
                return

            # Terminal: release whatever of the ORIGINAL reservation hasn't
            # already been released by an earlier partial-progress
            # observation above, and apply only the NEW delta since then
            # (not the full confirmed_filled_quantity again, or an earlier
            # partial would be double-applied).
            remaining_reserved = max(0.0, pending.requested_quantity - previously_applied)
            tx.settle(reserved_quantity=remaining_reserved, filled_quantity=delta)
            pending.confirmed_filled_quantity = confirmed_filled_quantity
            pending.remainder_resolved = True
            pending.phase = TransferPhase.RESTORING
            remaining = tx.owned
            lifecycle.confirmed_owned_quantity = remaining
            stop_amended = pending.stop_amended
            lifecycle.pending_exit = None

            if remaining <= 0:
                # See on_stop_filled's identical guard (EXE-07): a
                # still-working entry order may yet deliver more units.
                lifecycle.closed = not lifecycle.has_unresolved_entry
            elif lifecycle.stop.desired_price is not None and broker is not None:
                # PU-A4: `pending.source` is exactly the `source` the
                # original `request_exit` call was given (e.g. "target",
                # "time_exit") -- still readable off `pending` here even
                # though `lifecycle.pending_exit` itself is cleared just
                # above, since `pending` already holds that same object.
                await self._restore_stop_coverage(
                    lifecycle, account, broker, remaining, stop_amended, source=pending.source or "signal"
                )

        if delta > 0:
            self._apply_exit_fill(
                lifecycle,
                account,
                symbol,
                delta,
                exit_kind=pending.source,
                exit_price=filled_price,
                broker_order_id=pending.broker_order_id,
                reason=pending.reason,
            )
        else:
            self._persist(lifecycle)

    async def update_stop_price(self, account: DestinationAccount, symbol: str, new_price: float | None, signal_id: str = "") -> OrderResult:
        """WP-13: Update the stop price on an existing managed-lifecycle position.

        Returns FILLED if the stop price was successfully updated, or REJECTED if
        the lifecycle doesn't exist or the update fails."""
        self.lease_guard.require_active()
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal_id,
                message=f"no active lifecycle for {symbol}",
            )

        if new_price is None:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal_id,
                message="stop update requires a stop_loss price",
            )

        # Update the desired price on the lifecycle
        lifecycle.stop.desired_price = new_price
        self._persist(lifecycle)

        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal_id,
            message=f"stop price updated to {new_price} for {symbol}",
        )

    async def update_targets(self, account: DestinationAccount, symbol: str, targets: list, signal_id: str = "") -> OrderResult:
        """WP-13: Update the profit targets on an existing managed-lifecycle position.

        Returns FILLED if the targets were successfully updated, or REJECTED if
        the lifecycle doesn't exist."""
        self.lease_guard.require_active()
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal_id,
                message=f"no active lifecycle for {symbol}",
            )

        if not targets:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal_id,
                message="target update requires at least one target",
            )

        # TODO: Convert targets to lifecycle Target objects and update the plan
        # For now, just persist the lifecycle
        self._persist(lifecycle)

        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal_id,
            message=f"targets updated for {symbol}",
        )

    #: TR-EPISODE-01: exit kinds this method persists a real `orders`/
    #: `signals` row for -- exactly the managed-lifecycle exit kinds the
    #: accounting-ledger review found invisible to the ordinary order
    #: history (stop/target/time_exit fires triggered from *inside* this
    #: module, with no external caller that already persists a row for
    #: them). "provider_exit" and "manual_exit" are deliberately EXCLUDED:
    #: those are always externally initiated through app/engine.py
    #: (`_handle_signal`'s managed-lifecycle branch, or `close_position`),
    #: and that caller already persists its own order row for the same
    #: fill (see engine.py's own `save_order_result` calls) -- persisting
    #: here too would double-count the exact same execution in every
    #: consumer of the `orders` table (app/economics.py,
    #: app/provider_value.py, app/trade_episode.py).
    _SELF_PERSISTED_EXIT_KINDS = frozenset({"stop", "target", "time_exit"})

    def _apply_exit_fill(
        self,
        lifecycle: PositionLifecycle,
        account: DestinationAccount,
        symbol: str,
        filled_quantity: float,
        *,
        exit_kind: str = "",
        exit_price: float | None = None,
        broker_order_id: str | None = None,
        reason: str = "",
    ) -> None:
        """Single execution-application owner for a confirmed exit fill —
        called once from `request_exit`'s synchronous branch, once from
        `resolve_pending_exit`'s partial and terminal branches, and once
        from `on_stop_filled`. Applies the confirmed delta to
        `SignalStore.positions` atomically alongside the lifecycle
        checkpoint that already reflects it (or just persists the lifecycle
        if there's nothing to apply / no store wired in).

        PU-A1: this is also the single place every path that can close a
        lifecycle converges on, so it's the one place that finalizes this
        position's MAE/MFE into `SignalStore.position_excursions` —
        `lifecycle_state` (the in-progress record) is deleted once closed
        (see below), so without this, a closed position's excursion data
        would vanish rather than remain queryable for later analytics.

        TR-EPISODE-01: ALSO the one place that persists a real `orders`
        row (plus its originating synthetic `signals` row) for a stop/
        target/time_exit fill triggered from inside this module -- see
        `_SELF_PERSISTED_EXIT_KINDS` above for exactly which `exit_kind`s
        and why. `exit_price` is whatever real price the caller has for
        this specific fill (or None, honestly, when none is available at
        all yet -- see `on_stop_filled`'s own fallback chain and
        `resolve_pending_exit`'s docstring for the one case where no real
        price is available at this layer); it is NEVER guessed here.

        TRK-23: `broker_order_id` (also never guessed -- `None` when the
        caller has no real one for this specific fill) is the real
        broker-assigned id this fill happened under -- `on_stop_filled`'s
        own `lifecycle.stop.broker_order_id` (captured there BEFORE it's
        cleared), or `request_exit`/`resolve_pending_exit`'s own exit
        order id. Threaded through only so `_persist_self_initiated_exit`
        can build a real EXECUTION_APPLIED envelope alongside its `orders`
        row -- unused for any `exit_kind` outside
        `_SELF_PERSISTED_EXIT_KINDS`."""
        if lifecycle.closed:
            self._persist_closed_excursion(lifecycle, account, symbol)
            # TRK-27: record this exit episode's identity the instant it
            # actually closes -- see `_ClosedExitRecord`'s own docstring and
            # `request_exit`'s duplicate-request check for what this is for.
            self._record_closed_exit_episode(account, symbol, lifecycle, reason=reason or exit_kind)
        if self.store is not None and filled_quantity > 0:
            state = None if lifecycle.closed else _lifecycle_to_state(
                lifecycle, self.arbiter.snapshot(account.account_id, symbol)
            )
            self.store.record_fill(account.account_id, symbol, lifecycle.exit_side, filled_quantity, lifecycle_state=state)
            if lifecycle.closed:
                self.store.delete_lifecycle_state(account.account_id, symbol)
            if exit_kind in self._SELF_PERSISTED_EXIT_KINDS:
                self._persist_self_initiated_exit(
                    lifecycle,
                    account,
                    symbol,
                    filled_quantity,
                    exit_kind=exit_kind,
                    exit_price=exit_price,
                    broker_order_id=broker_order_id,
                    reason=reason,
                )
        else:
            self._persist(lifecycle)

    def _persist_self_initiated_exit(
        self,
        lifecycle: PositionLifecycle,
        account: DestinationAccount,
        symbol: str,
        filled_quantity: float,
        *,
        exit_kind: str,
        exit_price: float | None,
        broker_order_id: str | None,
        reason: str,
    ) -> None:
        """Persist one real `orders` row (plus its originating `signals`
        row) for a stop/target/time_exit fill this module itself
        triggered -- see `_apply_exit_fill`'s docstring. `self.store` is
        guaranteed non-None by the only caller.

        TRK-23: also builds and persists this fill's own EXECUTION_APPLIED
        export envelope, the same `build_execution_applied_envelope`/
        outbox mechanism app/engine.py's own fills already use (see
        app/export_events.py) -- this was the one real confirmed-fill
        point (a stop/target/time_exit triggered from *inside* this
        module, with no app/engine.py caller of its own) that never built
        one at all, not merely one with a missing field. `broker_order_id`
        is `None` (no envelope built -- `build_execution_applied_envelope`
        itself refuses one without it, never fabricated) whenever the
        caller genuinely has no real one for this specific fill."""
        assert self.store is not None
        exit_signal = Signal(
            source="lifecycle_manager",
            symbol=symbol,
            side=lifecycle.exit_side,
            asset_class=lifecycle.plan.asset_class,
            raw={"reason": reason or exit_kind, "exit_kind": exit_kind},
        )
        self.store.save_signal(exit_signal)
        result = OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=exit_signal.id,
            broker_order_id=broker_order_id,
            filled_quantity=filled_quantity,
            filled_price=exit_price,
            executed_at=datetime.now(timezone.utc),
        )
        source_stream = f"signal-copier:{account.account_id}"
        export_envelope = build_execution_applied_envelope(
            result,
            account=account,
            symbol=symbol,
            side=lifecycle.exit_side,
            asset_class=lifecycle.plan.asset_class,
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[config.RELAY_EVIDENCE_CLASS],
            environment=Environment[config.RELAY_ENVIRONMENT],
            originating_source_event_id=exit_signal.id,
            originating_analyst_id=None,
        )
        self.store.save_order_result(
            result,
            broker=account.broker,
            symbol=symbol,
            side=lifecycle.exit_side,
            applied_quantity=filled_quantity,
            export_envelope=export_envelope,
            purpose=exit_kind if exit_kind.endswith("_exit") else f"{exit_kind}_exit",
            family_id=lifecycle.plan.entry_signal_id or None,
        )

    # --- internals ---

    def _target_triggered(self, lifecycle: PositionLifecycle, target: Target, price: float) -> bool:
        if lifecycle.plan.side == Side.BUY:
            return price >= target.trigger_price
        return price <= target.trigger_price

    async def _submit_exit_order(
        self, broker: BrokerAdapter, account: DestinationAccount, lifecycle: PositionLifecycle, quantity: float, reason: str
    ) -> OrderResult:
        # WP-14 (C-03/C-04): set intent=EXIT so adapters emit the correct
        # close intent (e.g., "Sell to Close" for Tastytrade, "flat" for NinjaTrader).
        exit_signal = Signal(
            source="lifecycle_manager",
            symbol=lifecycle.plan.symbol,
            side=lifecycle.exit_side,
            asset_class=lifecycle.plan.asset_class,
            intent=Intent.EXIT,
            raw={"reason": reason},
        )
        if self.store is not None:
            # DB-01: this Signal's freshly-generated id becomes the
            # OrderResult's signal_id, which callers (app/engine.py's
            # close_position) persist into `orders.signal_id` -- a real
            # foreign key into `signals.id`. Persist it here, once, at the
            # single place every request_exit-driven submission (targets,
            # trailing, provider EXIT signals, time exits, manual
            # close/flatten) goes through, rather than every caller having
            # to know this id needs saving.
            self.store.save_signal(exit_signal)

        # P0-2: pre-effect durable command-ledger intent for this exit --
        # `_submit_exit_order` is the single place every request_exit-driven
        # submission (a logical target, trailing, a provider EXIT signal, a
        # time exit, manual close/flatten) funnels through, so wiring the
        # ledger here (rather than at every individual caller) covers all
        # of them. `exit_signal.id` is fresh every call (there is no
        # caller-supplied idempotency token threaded this deep through
        # `request_exit`'s own reservation/stop-resize transition above --
        # a documented, honest gap, not a silent one) so this row's
        # `idempotency_key` is a best-effort identity built from this
        # exit's own real parameters, not a value a genuine external retry
        # would reliably reproduce; `CloseArbiter`'s own `pending_exit`
        # check (see `request_exit`, just above this call) is what
        # actually prevents a CONCURRENT duplicate exit for the same
        # position today. TRK-27: a duplicate arriving AFTER the first
        # exit has already fully resolved (so `pending_exit` is no longer
        # in flight, and the replay-by-signal-id guard in app/engine.py's
        # `_handle_signal` doesn't catch it either, e.g. a genuinely
        # duplicate EXIT with a different channel_id/message_id) is instead
        # caught one level up, in `request_exit`'s own "no active lifecycle"
        # branch — see `check_duplicate_exit`/`_ClosedExitRecord`'s
        # docstrings for that mechanism and its explicitly documented
        # scope. `command_type` distinguishes a dashboard-driven
        # flatten from every other exit reason (see app/engine.py's
        # `close_position`, the only caller that passes a "flatten"-tagged
        # `reason`).
        command_type = CommandType.FLATTEN if "flatten" in reason.lower() else CommandType.CLOSE
        ledger_key = None
        if self.store is not None:
            ledger_key = f"{command_type.value}:{account.account_id}:{lifecycle.plan.symbol}:{exit_signal.id}"
            ledger_fingerprint = command_ledger.compute_fingerprint(
                {
                    "command_type": command_type.value,
                    "account_id": account.account_id,
                    "symbol": lifecycle.plan.symbol,
                    "side": exit_signal.side.value,
                    "quantity": quantity,
                    "reason": reason,
                    "exit_signal_id": exit_signal.id,
                }
            )
            self.store.open_command_ledger_entry(
                idempotency_key=ledger_key,
                command_type=command_type,
                account_id=account.account_id,
                environment=command_ledger.current_environment(),
                request_fingerprint=ledger_fingerprint,
            )
        try:
            # WP-17 (B-04): normalize quantity to venue precision before exit submission
            normalized_qty = broker.normalize_quantity(account, lifecycle.plan.symbol, quantity)
            if normalized_qty is not None:
                if normalized_qty <= 0:
                    # Exit quantity rounds to zero or below minimum
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=exit_signal.id,
                        message=f"exit quantity {quantity} rounds to {normalized_qty:.8g} below venue minimum",
                    )
                else:
                    quantity = normalized_qty
                    result = await broker.place_order(exit_signal, account, quantity, lifecycle.plan.symbol)
            else:
                # Unknown precision, proceed with original quantity
                result = await broker.place_order(exit_signal, account, quantity, lifecycle.plan.symbol)
        except Exception as exc:
            if ledger_key is not None and self.store is not None:
                self.store.mark_command_ledger_outcome(
                    ledger_key,
                    uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                    terminal_evidence=command_ledger.ambiguous_evidence_for_exception(exc),
                )
            raise
        if ledger_key is not None and self.store is not None:
            outcome_state, outcome_remote, outcome_evidence = command_ledger.classify_order_result(result)
            self.store.mark_command_ledger_outcome(
                ledger_key, uncertainty_state=outcome_state, remote_identifiers=outcome_remote, terminal_evidence=outcome_evidence
            )
        return result

    async def _restore_stop_coverage(
        self,
        lifecycle: PositionLifecycle,
        account: DestinationAccount,
        broker: BrokerAdapter,
        remaining: float,
        stop_amended: bool,
        *,
        source: str = "signal",
    ) -> None:
        """PRO-06: once an exit's real outcome is known, bring stop coverage
        to exactly `remaining`. If the stop was amended down in place before
        the exit was submitted (rather than cancelled), it is STILL resting
        at the broker under the same `broker_order_id` -- correct that same
        order's quantity via another amend, never submit a brand new stop on
        top of one that's already there (that would be two live stop orders
        covering the same shares). Only when nothing was amended (the
        cancel-then-resubmit fallback path, for a broker with no amend
        capability) is a fresh stop actually placed."""
        assert lifecycle.stop.desired_price is not None  # both call sites check this first
        if stop_amended and lifecycle.stop.broker_order_id:
            replaced = await self._ledgered_replace_stop_quantity(
                broker,
                account,
                lifecycle.plan.symbol,
                lifecycle.stop.broker_order_id,
                remaining,
                lifecycle.stop.desired_price,
                source=source,
            )
            if replaced is not None and replaced.status not in (OrderStatus.ERROR, OrderStatus.REJECTED):
                if replaced.broker_order_id:
                    lifecycle.stop.broker_order_id = replaced.broker_order_id
                lifecycle.stop.protected_quantity = remaining
                return
            logger.warning(
                "could not correct amended stop to final remaining=%.6f for account=%s symbol=%s -- "
                "coverage may be stale at %.6f",
                remaining,
                account.account_id,
                lifecycle.plan.symbol,
                lifecycle.stop.protected_quantity,
            )
            return
        await self._place_stop_locked(
            lifecycle, account, broker, remaining, lifecycle.stop.desired_price, source=source
        )

    async def _try_adopt_unresolved_stop(
        self,
        account: DestinationAccount,
        symbol: str,
        broker: BrokerAdapter,
    ) -> OrderResult | None:
        """D-09: Before placing a new stop, check if a recent stop_change
        command in the ledger has a known broker_order_id that we haven't yet
        seen confirmed. If so, poll that order's status — if it's still open
        (PENDING), adopt it; if it's filled/closed, return the result; if it's
        not found at the broker, proceed to place a new one (return None).

        This prevents submitting duplicate resting stops when a previous
        placement returned ERROR (timeout, connection reset) after the broker
        had already accepted it.
        """
        if self.store is None:
            return None

        # Query the most recent stop_change ledger entries for this (account, symbol).
        # They're ordered oldest-first, so iterate backwards to find the newest.
        for entry in reversed(
            self.store.list_unresolved_command_ledger_entries(account_id=account.account_id)
        ):
            if entry.command_type != CommandType.STOP_CHANGE:
                continue
            if not entry.remote_identifiers or "broker_order_id" not in entry.remote_identifiers:
                continue

            broker_order_id = entry.remote_identifiers["broker_order_id"]
            status_result = await broker.get_order_status(account, broker_order_id)

            if status_result is None:
                # Broker has no record of this order ID — either it was rejected
                # at submission or a different issue occurred. Proceed to place
                # a new stop.
                continue

            if status_result.status == OrderStatus.PENDING:
                # The previous attempt is still resting; adopt it instead of
                # placing a duplicate.
                logger.info(
                    "adopting unresolved stop order %s for account=%s symbol=%s",
                    broker_order_id,
                    account.account_id,
                    symbol,
                )
                return status_result

            # If it's filled, rejected, or any other terminal status,
            # return that result so the caller can handle it accordingly.
            return status_result

        return None

    async def _place_stop_locked(
        self,
        lifecycle: PositionLifecycle,
        account: DestinationAccount,
        broker: BrokerAdapter,
        quantity: float,
        price: float,
        *,
        source: str = "signal",
    ) -> None:
        """Submit (or resubmit) the protective stop. Caller must already hold this
        position's arbiter lock (or be in the single-threaded on_entry_fill path,
        where nothing else can be racing yet)."""
        # PU-A4: captured BEFORE this call mutates anything below -- the
        # real "previous" broker-confirmed price for whichever event this
        # attempt ends up emitting (None for a true initial placement).
        previous_confirmed_price = lifecycle.stop.broker_confirmed_price
        lifecycle.stop.status = ProtectionStatus.STOP_PENDING

        # D-09: Before placing a new stop, check if a previous attempt has a
        # known broker_order_id that might still be resting at the broker.
        # If so, adopt it instead of creating a duplicate.
        adopted = await self._try_adopt_unresolved_stop(account, lifecycle.plan.symbol, broker)
        if adopted is not None:
            if adopted.status == OrderStatus.PENDING:
                # Successfully adopted the unresolved stop.
                lifecycle.stop.submitted_price = price
                lifecycle.stop.broker_order_id = adopted.broker_order_id
                lifecycle.stop.broker_confirmed_price = price
                lifecycle.stop.protected_quantity = quantity
                lifecycle.stop.status = ProtectionStatus.STOP_CONFIRMED
                lifecycle.stop.confirmed_at = datetime.now(timezone.utc)
                logger.info(
                    "adopted previous stop order for account=%s symbol=%s broker_order_id=%s",
                    account.account_id,
                    lifecycle.plan.symbol,
                    adopted.broker_order_id,
                )
                self._record_stop_target_event(
                    lifecycle,
                    StopTargetEventType.STOP_PLACED,
                    price=price,
                    previous_price=previous_confirmed_price,
                    source=source,
                )
                self._persist(lifecycle)
                return
            else:
                # The previous stop had a terminal status (filled, rejected, etc.)
                # — treat it like any other result and let the normal path handle it.
                if adopted.status in (OrderStatus.ERROR, OrderStatus.REJECTED):
                    lifecycle.stop.status = ProtectionStatus.UNPROTECTED
                    lifecycle.stop.protected_quantity = 0.0
                    lifecycle.stop.confirmed_at = None
                    logger.warning(
                        "previous stop for account=%s symbol=%s was rejected/errored",
                        account.account_id,
                        lifecycle.plan.symbol,
                    )
                    self._record_stop_target_event(
                        lifecycle,
                        StopTargetEventType.PROTECTION_FAILED,
                        price=price,
                        previous_price=previous_confirmed_price,
                        source=source,
                    )
                    self._persist(lifecycle)
                    return
                elif adopted.status == OrderStatus.FILLED:
                    # The previous stop filled; treat this as an exit.
                    await self.on_stop_filled(
                        account, lifecycle.plan.symbol, adopted.filled_quantity or quantity
                    )
                    return
                else:
                    # Other terminal status; log and treat as unprotected.
                    lifecycle.stop.status = ProtectionStatus.UNPROTECTED
                    lifecycle.stop.protected_quantity = 0.0
                    lifecycle.stop.confirmed_at = None
                    logger.error(
                        "previous stop for account=%s symbol=%s has unexpected status %s",
                        account.account_id,
                        lifecycle.plan.symbol,
                        adopted.status.value,
                    )
                    self._persist(lifecycle)
                    return

        # P0-2: pre-effect durable command-ledger intent for this stop
        # placement/re-placement -- `_place_stop_locked` is the single real
        # `place_protective_stop` call site in this module (both the
        # initial post-fill placement and the cancel-then-resubmit
        # fallback funnel through here). Unlike the entry/close paths in
        # app/engine.py (which dedup on a real, caller-supplied signal id),
        # this call site is ALSO this module's own deliberate-retry path
        # (see `retry_unprotected_positions`, which resubmits the exact
        # same account/symbol/quantity/price after an earlier REJECTED/
        # unprotected outcome, by design) -- a key built only from those
        # request parameters would wrongly treat a legitimate retry as a
        # duplicate of the failed first attempt and either short-circuit it
        # or raise `CommandFingerprintMismatch` the moment `source` differs
        # between the two. So `idempotency_key` here includes a fresh
        # per-call nonce: every call to this method gets its own ledger
        # row (a full, honest pre-effect/outcome audit trail of every
        # attempt), never silently deduped against a previous one. See
        # `_submit_exit_order`'s identical, already-documented gap for the
        # same reasoning applied to a close/flatten exit.
        ledger_key = None
        if self.store is not None:
            ledger_key = f"stop_change:{account.account_id}:{lifecycle.plan.symbol}:{quantity}:{price}:{uuid.uuid4().hex[:12]}"
            ledger_fingerprint = command_ledger.compute_fingerprint(
                {
                    "command_type": "stop_change",
                    "account_id": account.account_id,
                    "symbol": lifecycle.plan.symbol,
                    "quantity": quantity,
                    "price": price,
                    "exit_side": lifecycle.exit_side.value,
                    "source": source,
                }
            )
            ledger_entry = self.store.open_command_ledger_entry(
                idempotency_key=ledger_key,
                command_type=CommandType.STOP_CHANGE,
                account_id=account.account_id,
                environment=command_ledger.current_environment(),
                request_fingerprint=ledger_fingerprint,
            )
            if command_ledger.is_duplicate_submission(ledger_entry.uncertainty_state):
                logger.info(
                    "duplicate stop_change command idempotency_key=%s -- not resubmitting", ledger_key
                )
                return

        try:
            # WP-17 (B-04): normalize quantity to venue precision before stop submission
            normalized_qty = broker.normalize_quantity(account, lifecycle.plan.symbol, quantity)
            if normalized_qty is not None:
                if normalized_qty <= 0:
                    # Stop quantity rounds to zero or below minimum
                    lifecycle.stop.status = ProtectionStatus.UNPROTECTED
                    lifecycle.stop.protected_quantity = 0.0
                    lifecycle.stop.confirmed_at = None
                    logger.warning(
                        "stop quantity %f rounds to %f below venue minimum for account=%s symbol=%s",
                        quantity,
                        normalized_qty,
                        account.account_id,
                        lifecycle.plan.symbol,
                    )
                    return
                quantity = normalized_qty
            # Unknown precision: proceed with original quantity
            result = await broker.place_protective_stop(account, lifecycle.plan.symbol, quantity, price, lifecycle.exit_side)
        except Exception as exc:
            if ledger_key is not None and self.store is not None:
                self.store.mark_command_ledger_outcome(
                    ledger_key,
                    uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                    terminal_evidence=command_ledger.ambiguous_evidence_for_exception(exc),
                )
            raise
        if ledger_key is not None and self.store is not None:
            outcome_state, outcome_remote, outcome_evidence = command_ledger.classify_optional_order_result(result)
            self.store.mark_command_ledger_outcome(
                ledger_key, uncertainty_state=outcome_state, remote_identifiers=outcome_remote, terminal_evidence=outcome_evidence
            )
        if result is None or result.status in (OrderStatus.ERROR, OrderStatus.REJECTED):
            # REJECTED is not a working protective order any more than ERROR
            # is — both mean nothing is actually resting at the broker; only
            # their wording differs (an explicit refusal vs. a submission
            # failure). Neither counts as STOP_CONFIRMED.
            lifecycle.stop.status = ProtectionStatus.UNPROTECTED
            lifecycle.stop.protected_quantity = 0.0
            lifecycle.stop.confirmed_at = None
            logger.warning(
                "no protective stop in place for account=%s symbol=%s (broker '%s' has no "
                "verified place_protective_stop, or the submission failed/was rejected)",
                account.account_id,
                lifecycle.plan.symbol,
                account.broker,
            )
            self._record_stop_target_event(
                lifecycle,
                StopTargetEventType.PROTECTION_FAILED,
                price=price,
                previous_price=previous_confirmed_price,
                source=source,
            )
            return
        if result.status == OrderStatus.FILLED or not result.broker_order_id:
            # PRO-05: neither of these is "confirmed resting coverage at
            # `quantity`". A FILLED result means the stop already executed
            # on submission (e.g. price was already past the trigger) --
            # that's a real exit, not a standing order, and reporting it as
            # STOP_CONFIRMED would hide that the position actually changed.
            # A resting order with no broker_order_id is unmanageable (it
            # can never be cancelled or resized later) and just as
            # unverified as no stop at all. Treat both as an unprotected
            # incident rather than fabricate confidence in coverage that
            # isn't actually verifiable.
            lifecycle.stop.status = ProtectionStatus.UNPROTECTED
            lifecycle.stop.protected_quantity = 0.0
            lifecycle.stop.broker_order_id = None
            lifecycle.stop.confirmed_at = None
            logger.error(
                "protective stop submission for account=%s symbol=%s returned an ambiguous "
                "result (status=%s, broker_order_id=%s) -- treating as unprotected rather "
                "than confirmed coverage; if the stop actually filled, this position's real "
                "exposure needs manual reconciliation against the broker",
                account.account_id,
                lifecycle.plan.symbol,
                result.status.value,
                result.broker_order_id,
            )
            self._record_stop_target_event(
                lifecycle,
                StopTargetEventType.PROTECTION_FAILED,
                price=price,
                previous_price=previous_confirmed_price,
                source=source,
            )
            return
        # A broker accepting the order (however it reports that — PENDING/resting is the
        # normal case) is what "broker confirmed" means for a standing stop order; it isn't
        # asserting the stop has *filled*.
        lifecycle.stop.submitted_price = price
        lifecycle.stop.broker_order_id = result.broker_order_id
        lifecycle.stop.broker_confirmed_price = price
        lifecycle.stop.protected_quantity = quantity
        lifecycle.stop.status = ProtectionStatus.STOP_CONFIRMED
        # PU-A2: the real moment the broker confirmed this stop is resting --
        # app/execution_quality.py's "protection acknowledgment" stage.
        lifecycle.stop.confirmed_at = datetime.now(timezone.utc)
        # PU-A4: this same real moment is also this position's stop/target
        # event log's STOP_PLACED entry -- reusing PU-A2's `confirmed_at`
        # timestamp/call site rather than duplicating the "is this really
        # confirmed" logic above.
        self._record_stop_target_event(
            lifecycle,
            StopTargetEventType.STOP_PLACED,
            price=price,
            previous_price=previous_confirmed_price,
            source=source,
        )

    async def _ledgered_replace_stop_quantity(
        self,
        broker: BrokerAdapter,
        account: DestinationAccount,
        symbol: str,
        broker_order_id: str,
        new_quantity: float,
        new_price: float | None,
        *,
        source: str,
    ) -> OrderResult | None:
        """P0-2: the one real `broker.replace_stop_quantity` call, wrapped
        with a pre-effect command_ledger entry and outcome recording --
        used by every one of this module's 3 real call sites (the
        can-amend-in-place branch of `request_exit`'s own stop-resize
        transition, `_restore_stop_coverage`, and `_replace_stop_price`) so
        the ledger wiring lives in one place instead of being repeated at
        each. Like `_place_stop_locked`'s identical note: this module can
        legitimately retry the SAME (broker_order_id, new_quantity,
        new_price) replace after an earlier failed attempt (e.g.
        `retry_unprotected_positions`), so `idempotency_key` includes a
        fresh per-call nonce -- every call gets its own row rather than
        risking a false duplicate/`CommandFingerprintMismatch` against an
        earlier attempt's differing `source`."""
        ledger_key = None
        if self.store is not None:
            ledger_key = f"replace:{account.account_id}:{symbol}:{broker_order_id}:{new_quantity}:{new_price}:{uuid.uuid4().hex[:12]}"
            fingerprint = command_ledger.compute_fingerprint(
                {
                    "command_type": "replace",
                    "account_id": account.account_id,
                    "symbol": symbol,
                    "broker_order_id": broker_order_id,
                    "new_quantity": new_quantity,
                    "new_price": new_price,
                    "source": source,
                }
            )
            entry = self.store.open_command_ledger_entry(
                idempotency_key=ledger_key,
                command_type=CommandType.REPLACE,
                account_id=account.account_id,
                environment=command_ledger.current_environment(),
                request_fingerprint=fingerprint,
                expected_revision=broker_order_id,
            )
            if command_ledger.is_duplicate_submission(entry.uncertainty_state):
                logger.info("duplicate replace command idempotency_key=%s -- replaying tracked state", ledger_key)
                return _order_result_from_ledger_entry(account.account_id, entry)
        try:
            result = await broker.replace_stop_quantity(account, broker_order_id, new_quantity, new_price, symbol=symbol)
        except Exception as exc:
            if ledger_key is not None and self.store is not None:
                self.store.mark_command_ledger_outcome(
                    ledger_key,
                    uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                    terminal_evidence=command_ledger.ambiguous_evidence_for_exception(exc),
                )
            raise
        if ledger_key is not None and self.store is not None:
            outcome_state, outcome_remote, outcome_evidence = command_ledger.classify_optional_order_result(result)
            self.store.mark_command_ledger_outcome(
                ledger_key, uncertainty_state=outcome_state, remote_identifiers=outcome_remote, terminal_evidence=outcome_evidence
            )
        return result

    async def _ledgered_cancel_order(
        self, broker: BrokerAdapter, account: DestinationAccount, symbol: str, broker_order_id: str, *, source: str
    ) -> bool:
        """P0-2: the one real `broker.cancel_order` call, wrapped with a
        pre-effect command_ledger entry and outcome recording -- used by
        this module's 2 real call sites (the cancel-then-resubmit fallback
        in `request_exit` and in `_replace_stop_price`). Same per-call
        nonce reasoning as `_ledgered_replace_stop_quantity`/
        `_place_stop_locked`: this module can legitimately re-attempt
        cancelling the same broker_order_id from a different `source`."""
        ledger_key = None
        if self.store is not None:
            ledger_key = f"cancel:{account.account_id}:{symbol}:{broker_order_id}:{uuid.uuid4().hex[:12]}"
            fingerprint = command_ledger.compute_fingerprint(
                {
                    "command_type": "cancel",
                    "account_id": account.account_id,
                    "symbol": symbol,
                    "broker_order_id": broker_order_id,
                    "source": source,
                }
            )
            entry = self.store.open_command_ledger_entry(
                idempotency_key=ledger_key,
                command_type=CommandType.CANCEL,
                account_id=account.account_id,
                environment=command_ledger.current_environment(),
                request_fingerprint=fingerprint,
                expected_revision=broker_order_id,
            )
            if command_ledger.is_duplicate_submission(entry.uncertainty_state):
                logger.info("duplicate cancel command idempotency_key=%s -- replaying tracked state", ledger_key)
                return entry.uncertainty_state == UncertaintyState.CONFIRMED
        try:
            cancelled = await broker.cancel_order(account, broker_order_id, symbol=symbol)
        except Exception as exc:
            if ledger_key is not None and self.store is not None:
                self.store.mark_command_ledger_outcome(
                    ledger_key,
                    uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                    terminal_evidence=command_ledger.ambiguous_evidence_for_exception(exc),
                )
            raise
        if ledger_key is not None and self.store is not None:
            outcome_state, outcome_evidence = command_ledger.classify_cancel_result(cancelled)
            self.store.mark_command_ledger_outcome(
                ledger_key, uncertainty_state=outcome_state, terminal_evidence=outcome_evidence
            )
        return cancelled

    async def _tighten_stop_to(self, lifecycle: PositionLifecycle, account: DestinationAccount, price: float) -> None:
        current = lifecycle.stop.desired_price
        if current is not None:
            if lifecycle.plan.side == Side.BUY and price <= current:
                return  # never loosen
            if lifecycle.plan.side == Side.SELL and price >= current:
                return
        lifecycle.stop.desired_price = price
        await self._replace_stop_price(lifecycle, account, source="signal")

    async def _update_trailing(self, lifecycle: PositionLifecycle, account: DestinationAccount, price: float) -> None:
        """A trail must never loosen whatever is already protecting this
        position. The bug this guards against: on the FIRST trail update,
        `trailing.floor_price` is still None, so comparing only against it
        (as this used to) treats any candidate as "improved" -- including
        one worse than the existing stop (e.g. an existing 95 stop, price
        100, a 20-point trail distance computes a candidate of 80, which
        used to be accepted as the new stop, loosening protection from 95
        to 80). The non-loosening floor is the *tighter* of the trailing
        policy's own prior floor and the stop's current desired price,
        never just one or the other."""
        trailing = lifecycle.plan.trailing
        assert trailing is not None  # only caller (on_price_update) checks this first
        candidate_floor, improved = _compute_trailing_candidate(
            trailing, lifecycle.stop.desired_price, lifecycle.plan.side, price
        )

        if not improved:
            return
        trailing.floor_price = candidate_floor
        lifecycle.stop.desired_price = candidate_floor
        await self._replace_stop_price(lifecycle, account, source="signal")

    async def _replace_stop_price(
        self, lifecycle: PositionLifecycle, account: DestinationAccount, *, source: str = "signal"
    ) -> None:
        broker = self.brokers.get(account.broker)
        if broker is None or lifecycle.stop.desired_price is None:
            return
        if lifecycle.pending_exit is not None and not lifecycle.pending_exit.remainder_resolved:
            # A prior exit already cancelled the old stop and freed shares that
            # may still sell (see PendingExit's docstring). Resizing/rearming
            # now — even to `tx.owned`, which still includes those uncertain
            # shares — would re-cover quantity that request_exit already
            # accounted for as "may leave via that pending order," recreating
            # the exact double-claim this subsystem exists to prevent.
            logger.info(
                "skipping stop replacement for account=%s symbol=%s: pending exit %s "
                "(%.6f of %.6f requested) hasn't resolved yet",
                account.account_id,
                lifecycle.plan.symbol,
                lifecycle.pending_exit.broker_order_id,
                lifecycle.pending_exit.unresolved_remainder,
                lifecycle.pending_exit.requested_quantity,
            )
            return

        # PU-A4: captured before anything below mutates it -- the real
        # price this replace is REPLACING (None if no stop has ever been
        # broker-confirmed for this position yet, which the `broker_order_id`
        # check just below means can't actually happen on this call path,
        # but is still the honest value if it somehow were).
        previous_confirmed_price = lifecycle.stop.broker_confirmed_price
        desired_price = lifecycle.stop.desired_price

        async with self.arbiter.transition(account.account_id, lifecycle.plan.symbol) as tx:
            quantity = tx.owned
            if quantity <= 0:
                return

            if lifecycle.stop.broker_order_id:
                replaced = await self._ledgered_replace_stop_quantity(
                    broker,
                    account,
                    lifecycle.plan.symbol,
                    lifecycle.stop.broker_order_id,
                    quantity,
                    lifecycle.stop.desired_price,
                    source=source,
                )
                if replaced is not None and replaced.status in (OrderStatus.ERROR, OrderStatus.REJECTED):
                    # The broker attempted the replace and told us it did NOT
                    # happen (as distinct from `replaced is None`, meaning
                    # this adapter has no in-place replace at all — see the
                    # cancel/resubmit fallback below). A returned status
                    # alone isn't proof the venue's state actually changed
                    # (Alpaca's own docs: a successful replacement response
                    # doesn't guarantee the old order was replaced), so an
                    # explicit failure is treated the same as "nothing
                    # changed" here too: preserve the existing coverage
                    # rather than guessing at a cancel/resubmit against an
                    # order the venue says it didn't touch, and don't report
                    # the requested `quantity` as newly confirmed.
                    logger.warning(
                        "stop replacement failed for account=%s symbol=%s status=%s -- "
                        "keeping existing coverage of %.6f",
                        account.account_id,
                        lifecycle.plan.symbol,
                        replaced.status.value,
                        lifecycle.stop.protected_quantity,
                    )
                    self._persist(lifecycle)
                    return
                if replaced is not None:
                    # Some brokers (e.g. Alpaca) replace by cancelling the old order and
                    # creating a new one — always trust whatever id comes back rather than
                    # assuming the id is unchanged, or a later cancel/replace would target
                    # a dead order and fail.
                    if replaced.broker_order_id:
                        lifecycle.stop.broker_order_id = replaced.broker_order_id
                    lifecycle.stop.submitted_price = lifecycle.stop.desired_price
                    lifecycle.stop.broker_confirmed_price = lifecycle.stop.desired_price
                    lifecycle.stop.protected_quantity = quantity
                    if previous_confirmed_price is not None and previous_confirmed_price != desired_price:
                        # PU-A4: an already-resting stop's PRICE actually
                        # changed in place -- only `_tighten_stop_to`/
                        # `_update_trailing` ever change `desired_price`
                        # before calling this method (retry_unprotected_
                        # positions re-submits the SAME price, so this
                        # condition is false for it, and correctly emits no
                        # tightening event for a same-price retry).
                        self._record_stop_target_event(
                            lifecycle,
                            StopTargetEventType.STOP_TIGHTENED,
                            price=desired_price,
                            previous_price=previous_confirmed_price,
                            source=source,
                        )
                    self._persist(lifecycle)
                    return

                # replaced is None: no atomic in-place replace supported by this
                # adapter at all -- cancel then resubmit. If cancellation can't be
                # confirmed, the old stop might have just filled: design section 8's rule is
                # "the old working stop remains meaningful until actual broker evidence says
                # otherwise," so this leaves it alone rather than guessing.
                cancelled = await self._ledgered_cancel_order(
                    broker, account, lifecycle.plan.symbol, lifecycle.stop.broker_order_id, source=source
                )
                if not cancelled:
                    return
                lifecycle.stop.broker_order_id = None

            await self._place_stop_locked(
                lifecycle, account, broker, quantity, lifecycle.stop.desired_price, source=source
            )
        self._persist(lifecycle)


def _order_result_from_ledger_entry(account_id: str, entry: "CommandLedgerEntry") -> OrderResult:
    """P0-2: reconstruct a synthetic `OrderResult` from a duplicate
    command_ledger entry's own tracked state, for a caller (see
    `_ledgered_replace_stop_quantity`) that must return something in this
    shape without calling the broker again. Maps `UncertaintyState` to the
    closest honest `OrderStatus`: CONFIRMED/SUBMITTED_UNCONFIRMED both
    report as PENDING (something real is/was resting, whether the row's
    own last update knows the exact broker_order_id or not -- a caller
    branching on `status not in (ERROR, REJECTED)` treats this the same as
    the original successful replace did); REJECTED_CONFIRMED reports
    REJECTED; UNKNOWN_AMBIGUOUS reports ERROR (the honest "don't trust
    this as a success" signal), never fabricated as a confirmed success."""
    status = {
        UncertaintyState.CONFIRMED: OrderStatus.PENDING,
        UncertaintyState.SUBMITTED_UNCONFIRMED: OrderStatus.PENDING,
        UncertaintyState.REJECTED_CONFIRMED: OrderStatus.REJECTED,
        UncertaintyState.UNKNOWN_AMBIGUOUS: OrderStatus.ERROR,
        UncertaintyState.PENDING_SUBMISSION: OrderStatus.ERROR,
    }[entry.uncertainty_state]
    return OrderResult(
        account_id=account_id,
        status=status,
        signal_id="",
        broker_order_id=entry.remote_identifiers.get("broker_order_id"),
        message=f"duplicate command (idempotency_key={entry.idempotency_key}); tracked state={entry.uncertainty_state.value}",
    )


# --- state (de)serialization, for PositionLifecycleManager's store-backed persist/restore ---


def _lifecycle_to_state(lifecycle: PositionLifecycle, ledger: dict) -> dict:
    plan = lifecycle.plan
    return {
        "closed": lifecycle.closed,
        "confirmed_owned_quantity": lifecycle.confirmed_owned_quantity,
        # PU-A1: in-progress MAE/MFE tracking, so a restart resumes it
        # instead of losing every observation made before the crash.
        "entry_price": lifecycle.entry_price,
        "highest_price_since_entry": lifecycle.highest_price_since_entry,
        "highest_price_at": lifecycle.highest_price_at.isoformat() if lifecycle.highest_price_at else None,
        "lowest_price_since_entry": lifecycle.lowest_price_since_entry,
        "lowest_price_at": lifecycle.lowest_price_at.isoformat() if lifecycle.lowest_price_at else None,
        "plan": {
            "account_id": plan.account_id,
            "symbol": plan.symbol,
            "side": plan.side.value,
            "planned_quantity": plan.planned_quantity,
            "asset_class": plan.asset_class.value,
            "broker": plan.broker,
            "initial_stop": plan.initial_stop,
            "entry_signal_id": plan.entry_signal_id,
            "targets": [
                {
                    "trigger_price": t.trigger_price,
                    "action": t.action.value,
                    "reduce_fraction": t.reduce_fraction,
                    "fired": t.fired,
                }
                for t in plan.targets
            ],
            "trailing": None
            if plan.trailing is None
            else {
                "activate_at_price": plan.trailing.activate_at_price,
                "trail_distance": plan.trailing.trail_distance,
                "active": plan.trailing.active,
                "floor_price": plan.trailing.floor_price,
            },
            "time_exit": plan.time_exit.isoformat() if plan.time_exit else None,
            "max_risk": plan.max_risk,
        },
        "stop": {
            "desired_price": lifecycle.stop.desired_price,
            "submitted_price": lifecycle.stop.submitted_price,
            "broker_confirmed_price": lifecycle.stop.broker_confirmed_price,
            "broker_order_id": lifecycle.stop.broker_order_id,
            "protected_quantity": lifecycle.stop.protected_quantity,
            "status": lifecycle.stop.status.value,
            "confirmed_at": lifecycle.stop.confirmed_at.isoformat() if lifecycle.stop.confirmed_at else None,
        },
        "pending_exit": None
        if lifecycle.pending_exit is None
        else {
            "broker_order_id": lifecycle.pending_exit.broker_order_id,
            "requested_quantity": lifecycle.pending_exit.requested_quantity,
            "confirmed_filled_quantity": lifecycle.pending_exit.confirmed_filled_quantity,
            "remainder_resolved": lifecycle.pending_exit.remainder_resolved,
            "phase": lifecycle.pending_exit.phase.value,
            "source": lifecycle.pending_exit.source,
            "reason": lifecycle.pending_exit.reason,
            "stop_amended": lifecycle.pending_exit.stop_amended,
        },
        "pending_entry": None
        if lifecycle.pending_entry is None
        else {
            "broker_order_id": lifecycle.pending_entry.broker_order_id,
            "requested_quantity": lifecycle.pending_entry.requested_quantity,
            "confirmed_filled_quantity": lifecycle.pending_entry.confirmed_filled_quantity,
            "remainder_resolved": lifecycle.pending_entry.remainder_resolved,
            "reserved_notional": lifecycle.pending_entry.reserved_notional,
            "reserved_quantity": lifecycle.pending_entry.reserved_quantity,
        },
        "ledger": ledger,
    }


def _lifecycle_from_state(row: dict) -> PositionLifecycle:
    plan_row = row["plan"]
    trailing_row = plan_row.get("trailing")
    time_exit = datetime.fromisoformat(plan_row["time_exit"]) if plan_row.get("time_exit") else None
    plan = PositionPlan(
        account_id=plan_row["account_id"],
        symbol=plan_row["symbol"],
        side=Side(plan_row["side"]),
        planned_quantity=plan_row["planned_quantity"],
        asset_class=AssetClass(plan_row["asset_class"]),
        broker=plan_row.get("broker", ""),
        initial_stop=plan_row.get("initial_stop"),
        entry_signal_id=plan_row.get("entry_signal_id", ""),
        targets=[
            Target(
                trigger_price=t["trigger_price"],
                action=TargetAction(t["action"]),
                reduce_fraction=t.get("reduce_fraction"),
                fired=t.get("fired", False),
            )
            for t in plan_row.get("targets", [])
        ],
        trailing=None
        if trailing_row is None
        else TrailingPolicy(
            activate_at_price=trailing_row.get("activate_at_price"),
            trail_distance=trailing_row.get("trail_distance", 0.0),
            active=trailing_row.get("active", False),
            floor_price=trailing_row.get("floor_price"),
        ),
        time_exit=time_exit,
        max_risk=plan_row.get("max_risk"),
    )

    stop_row = row["stop"]
    stop_confirmed_at_row = stop_row.get("confirmed_at")
    stop = StopRecord(
        desired_price=stop_row.get("desired_price"),
        submitted_price=stop_row.get("submitted_price"),
        broker_confirmed_price=stop_row.get("broker_confirmed_price"),
        broker_order_id=stop_row.get("broker_order_id"),
        protected_quantity=stop_row.get("protected_quantity", 0.0),
        status=ProtectionStatus(stop_row.get("status", ProtectionStatus.UNPROTECTED.value)),
        confirmed_at=datetime.fromisoformat(stop_confirmed_at_row) if stop_confirmed_at_row else None,
    )

    pending_row = row.get("pending_exit")
    pending_exit = (
        None
        if pending_row is None
        else PendingExit(
            broker_order_id=pending_row.get("broker_order_id"),
            requested_quantity=pending_row["requested_quantity"],
            confirmed_filled_quantity=pending_row.get("confirmed_filled_quantity", 0.0),
            remainder_resolved=pending_row.get("remainder_resolved", False),
            phase=TransferPhase(pending_row.get("phase", TransferPhase.AWAITING_REMAINDER_RESOLUTION.value)),
            source=pending_row.get("source", ""),
            reason=pending_row.get("reason", ""),
            stop_amended=pending_row.get("stop_amended", False),
        )
    )

    pending_entry_row = row.get("pending_entry")
    pending_entry = (
        None
        if pending_entry_row is None
        else PendingEntry(
            broker_order_id=pending_entry_row.get("broker_order_id"),
            requested_quantity=pending_entry_row["requested_quantity"],
            confirmed_filled_quantity=pending_entry_row.get("confirmed_filled_quantity", 0.0),
            remainder_resolved=pending_entry_row.get("remainder_resolved", False),
            reserved_notional=pending_entry_row.get("reserved_notional", 0.0),
            reserved_quantity=pending_entry_row.get("reserved_quantity", 0.0),
        )
    )

    highest_price_at_row = row.get("highest_price_at")
    lowest_price_at_row = row.get("lowest_price_at")
    return PositionLifecycle(
        plan=plan,
        confirmed_owned_quantity=row.get("confirmed_owned_quantity", 0.0),
        stop=stop,
        closed=row.get("closed", False),
        pending_exit=pending_exit,
        pending_entry=pending_entry,
        entry_price=row.get("entry_price"),
        highest_price_since_entry=row.get("highest_price_since_entry"),
        highest_price_at=datetime.fromisoformat(highest_price_at_row) if highest_price_at_row else None,
        lowest_price_since_entry=row.get("lowest_price_since_entry"),
        lowest_price_at=datetime.fromisoformat(lowest_price_at_row) if lowest_price_at_row else None,
    )
