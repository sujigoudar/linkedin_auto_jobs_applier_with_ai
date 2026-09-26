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
from collections import defaultdict
from datetime import datetime

from app.brokers.base import BrokerAdapter
from app.lifecycle.close_arbiter import CloseArbiter
from app.lifecycle.models import (
    PendingEntry,
    PendingExit,
    PositionLifecycle,
    PositionPlan,
    ProtectionStatus,
    StopRecord,
    Target,
    TargetAction,
    TrailingPolicy,
    TransferPhase,
)
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Signal, Side

logger = logging.getLogger(__name__)


class PositionLifecycleManager:
    def __init__(self, brokers: dict[str, BrokerAdapter], arbiter: CloseArbiter | None = None, store=None):
        self.brokers = brokers
        self.arbiter = arbiter or CloseArbiter()
        self.store = store  # app.db.SignalStore, optional — enables crash-resumable persistence
        self._lifecycles: dict[tuple[str, str], PositionLifecycle] = {}
        # Serializes the ENTIRE fill-application decision in resolve_pending_entry
        # (read the last-applied checkpoint, compute the new delta, apply it) per
        # (account_id, symbol) -- separate from `self.arbiter`'s lock, which only
        # protects individual ledger mutations and is acquired/released multiple
        # times within one resolve_pending_entry call. Without this, two
        # concurrent observations of the same broker order can both read the
        # checkpoint before either advances it and both apply the same fill.
        self._pending_entry_locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def get_lifecycle(self, account_id: str, symbol: str) -> PositionLifecycle | None:
        return self._lifecycles.get((account_id, symbol))

    def list_open_lifecycles(self) -> list[PositionLifecycle]:
        """Every managed-lifecycle position not yet closed — for monitoring
        (see app/main.py's `/positions`), not for mutation."""
        return [lifecycle for lifecycle in self._lifecycles.values() if not lifecycle.closed]

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

    def restore_from_store(self) -> None:
        """Rebuild in-memory lifecycles + arbiter ledgers from persisted
        state — call once at startup, before any signal is handled. Answers
        design section 3's "resume the existing episode after a restart":
        without this, `PositionLifecycleManager` starts with no memory of
        what it was protecting, which app/lifecycle/manager.py's module
        docstring used to list as an open gap."""
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

    def _persist(self, lifecycle: PositionLifecycle) -> None:
        if self.store is None:
            return
        account_id, symbol = lifecycle.key
        if lifecycle.closed:
            self.store.delete_lifecycle_state(account_id, symbol)
            return
        state = _lifecycle_to_state(lifecycle, self.arbiter.snapshot(account_id, symbol))
        self.store.save_lifecycle_state(account_id, symbol, state)

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
        call `on_entry_fill` with what actually filled."""
        lifecycle = PositionLifecycle(plan=plan)
        self._lifecycles[(plan.account_id, plan.symbol)] = lifecycle
        return lifecycle

    def unregister_plan(self, account_id: str, symbol: str) -> None:
        """Drop a plan that was registered but never entered (e.g. the entry
        order itself failed after `start_plan`) — otherwise a stale,
        never-filled lifecycle sits around forever."""
        self._lifecycles.pop((account_id, symbol), None)
        if self.store is not None:
            self.store.delete_lifecycle_state(account_id, symbol)

    async def on_entry_fill(
        self, account: DestinationAccount, symbol: str, filled_quantity: float
    ) -> PositionLifecycle:
        """Call once the entry order confirms a fill — `filled_quantity` is the
        total confirmed so far (design section 2: "Confirmed owned quantity =
        62", not a delta). Immediately submits protection before returning,
        per the core rule: protect first, everything else follows."""
        lifecycle = self._lifecycles[(account.account_id, symbol)]
        broker = self.brokers.get(account.broker)

        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle.confirmed_owned_quantity = filled_quantity
            tx.set_owned(filled_quantity)
            if lifecycle.plan.initial_stop is not None and broker is not None:
                lifecycle.stop.desired_price = lifecycle.plan.initial_stop
                await self._place_stop_locked(lifecycle, account, broker, filled_quantity, lifecycle.plan.initial_stop)

        self._persist(lifecycle)
        return lifecycle

    def register_pending_entry(
        self, account: DestinationAccount, symbol: str, broker_order_id: str | None, requested_quantity: float
    ) -> None:
        """Call instead of `on_entry_fill` when the entry order's broker
        response is PENDING rather than a synchronous fill: retains the
        intent (this may already be a real, accepted order) without
        assuming `requested_quantity` is actually owned/protected yet — see
        PendingEntry's docstring for why guessing here is exactly the bug
        this exists to avoid. `app/reconciliation.py` polls
        `list_pending_entries()` and eventually calls
        `resolve_pending_entry` once the broker's final word is known."""
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None:
            return
        lifecycle.pending_entry = PendingEntry(broker_order_id=broker_order_id, requested_quantity=requested_quantity)
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
        self._apply_exit_fill(lifecycle, account, symbol, filled_quantity)

    async def on_price_update(self, account: DestinationAccount, symbol: str, price: float) -> list[OrderResult]:
        """Evaluate logical targets and trailing against a new price. Call this
        from whatever feed you have wired up for this broker (see this
        module's docstring — no feed is wired up generically)."""
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None or lifecycle.closed or self.arbiter.is_halted(account.account_id, symbol):
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
            elif target.action == TargetAction.TIGHTEN_STOP:
                target.fired = True
                await self._tighten_stop_to(lifecycle, account, target.trigger_price)
            elif target.action == TargetAction.ACTIVATE_TRAIL:
                target.fired = True
                if lifecycle.plan.trailing:
                    lifecycle.plan.trailing.active = True

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
        broker = self.brokers.get(account.broker)
        if broker is None:
            return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id="", message=f"no broker adapter registered for '{account.broker}'")

        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle = self._lifecycles.get((account.account_id, symbol))
            if lifecycle is None or lifecycle.closed:
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

            requested = min(quantity, tx.available)
            if requested <= 0:
                return OrderResult(account_id=account.account_id, status=OrderStatus.REJECTED, signal_id="", message="no shares available to sell")

            had_stop = lifecycle.stop.broker_order_id is not None
            if had_stop:
                cancelled = await broker.cancel_order(account, lifecycle.stop.broker_order_id)
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

            exit_result = await self._submit_exit_order(broker, account, lifecycle, requested, reason or source)

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
                await self._place_stop_locked(lifecycle, account, broker, remaining, lifecycle.stop.desired_price)

            self._apply_exit_fill(lifecycle, account, symbol, actual_filled)
            return exit_result

    async def resolve_pending_exit(
        self, account: DestinationAccount, symbol: str, confirmed_filled_quantity: float, remainder_cancelled: bool
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
        15 confirmed, 7 still open -- restores against 54, not 47)."""
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
                    self._apply_exit_fill(lifecycle, account, symbol, delta)
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
            lifecycle.pending_exit = None

            if remaining <= 0:
                # See on_stop_filled's identical guard (EXE-07): a
                # still-working entry order may yet deliver more units.
                lifecycle.closed = not lifecycle.has_unresolved_entry
            elif lifecycle.stop.desired_price is not None and broker is not None:
                await self._place_stop_locked(lifecycle, account, broker, remaining, lifecycle.stop.desired_price)

        if delta > 0:
            self._apply_exit_fill(lifecycle, account, symbol, delta)
        else:
            self._persist(lifecycle)

    def _apply_exit_fill(
        self, lifecycle: PositionLifecycle, account: DestinationAccount, symbol: str, filled_quantity: float
    ) -> None:
        """Single execution-application owner for a confirmed exit fill —
        called once from `request_exit`'s synchronous branch, once from
        `resolve_pending_exit`'s terminal branch. Applies the confirmed
        delta to `SignalStore.positions` atomically alongside the lifecycle
        checkpoint that already reflects it (or just persists the lifecycle
        if there's nothing to apply / no store wired in)."""
        if self.store is not None and filled_quantity > 0:
            state = None if lifecycle.closed else _lifecycle_to_state(
                lifecycle, self.arbiter.snapshot(account.account_id, symbol)
            )
            self.store.record_fill(account.account_id, symbol, lifecycle.exit_side, filled_quantity, lifecycle_state=state)
            if lifecycle.closed:
                self.store.delete_lifecycle_state(account.account_id, symbol)
        else:
            self._persist(lifecycle)

    # --- internals ---

    def _target_triggered(self, lifecycle: PositionLifecycle, target: Target, price: float) -> bool:
        if lifecycle.plan.side == Side.BUY:
            return price >= target.trigger_price
        return price <= target.trigger_price

    async def _submit_exit_order(
        self, broker: BrokerAdapter, account: DestinationAccount, lifecycle: PositionLifecycle, quantity: float, reason: str
    ) -> OrderResult:
        exit_signal = Signal(
            source="lifecycle_manager",
            symbol=lifecycle.plan.symbol,
            side=lifecycle.exit_side,
            asset_class=lifecycle.plan.asset_class,
            raw={"reason": reason},
        )
        return await broker.place_order(exit_signal, account, quantity, lifecycle.plan.symbol)

    async def _place_stop_locked(
        self,
        lifecycle: PositionLifecycle,
        account: DestinationAccount,
        broker: BrokerAdapter,
        quantity: float,
        price: float,
    ) -> None:
        """Submit (or resubmit) the protective stop. Caller must already hold this
        position's arbiter lock (or be in the single-threaded on_entry_fill path,
        where nothing else can be racing yet)."""
        lifecycle.stop.status = ProtectionStatus.STOP_PENDING
        result = await broker.place_protective_stop(account, lifecycle.plan.symbol, quantity, price, lifecycle.exit_side)
        if result is None or result.status in (OrderStatus.ERROR, OrderStatus.REJECTED):
            # REJECTED is not a working protective order any more than ERROR
            # is — both mean nothing is actually resting at the broker; only
            # their wording differs (an explicit refusal vs. a submission
            # failure). Neither counts as STOP_CONFIRMED.
            lifecycle.stop.status = ProtectionStatus.UNPROTECTED
            lifecycle.stop.protected_quantity = 0.0
            logger.warning(
                "no protective stop in place for account=%s symbol=%s (broker '%s' has no "
                "verified place_protective_stop, or the submission failed/was rejected)",
                account.account_id,
                lifecycle.plan.symbol,
                account.broker,
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
            return
        # A broker accepting the order (however it reports that — PENDING/resting is the
        # normal case) is what "broker confirmed" means for a standing stop order; it isn't
        # asserting the stop has *filled*.
        lifecycle.stop.submitted_price = price
        lifecycle.stop.broker_order_id = result.broker_order_id
        lifecycle.stop.broker_confirmed_price = price
        lifecycle.stop.protected_quantity = quantity
        lifecycle.stop.status = ProtectionStatus.STOP_CONFIRMED

    async def _tighten_stop_to(self, lifecycle: PositionLifecycle, account: DestinationAccount, price: float) -> None:
        current = lifecycle.stop.desired_price
        if current is not None:
            if lifecycle.plan.side == Side.BUY and price <= current:
                return  # never loosen
            if lifecycle.plan.side == Side.SELL and price >= current:
                return
        lifecycle.stop.desired_price = price
        await self._replace_stop_price(lifecycle, account)

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
        existing = [v for v in (trailing.floor_price, lifecycle.stop.desired_price) if v is not None]
        if lifecycle.plan.side == Side.BUY:
            current_best = max(existing) if existing else None
            candidate_floor = price - trailing.trail_distance
            improved = current_best is None or candidate_floor > current_best
        else:
            current_best = min(existing) if existing else None
            candidate_floor = price + trailing.trail_distance
            improved = current_best is None or candidate_floor < current_best

        if not improved:
            return
        trailing.floor_price = candidate_floor
        lifecycle.stop.desired_price = candidate_floor
        await self._replace_stop_price(lifecycle, account)

    async def _replace_stop_price(self, lifecycle: PositionLifecycle, account: DestinationAccount) -> None:
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

        async with self.arbiter.transition(account.account_id, lifecycle.plan.symbol) as tx:
            quantity = tx.owned
            if quantity <= 0:
                return

            if lifecycle.stop.broker_order_id:
                replaced = await broker.replace_stop_quantity(
                    account, lifecycle.stop.broker_order_id, quantity, lifecycle.stop.desired_price
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
                    self._persist(lifecycle)
                    return

                # replaced is None: no atomic in-place replace supported by this
                # adapter at all -- cancel then resubmit. If cancellation can't be
                # confirmed, the old stop might have just filled: design section 8's rule is
                # "the old working stop remains meaningful until actual broker evidence says
                # otherwise," so this leaves it alone rather than guessing.
                cancelled = await broker.cancel_order(account, lifecycle.stop.broker_order_id)
                if not cancelled:
                    return
                lifecycle.stop.broker_order_id = None

            await self._place_stop_locked(lifecycle, account, broker, quantity, lifecycle.stop.desired_price)
        self._persist(lifecycle)


# --- state (de)serialization, for PositionLifecycleManager's store-backed persist/restore ---


def _lifecycle_to_state(lifecycle: PositionLifecycle, ledger: dict) -> dict:
    plan = lifecycle.plan
    return {
        "closed": lifecycle.closed,
        "confirmed_owned_quantity": lifecycle.confirmed_owned_quantity,
        "plan": {
            "account_id": plan.account_id,
            "symbol": plan.symbol,
            "side": plan.side.value,
            "planned_quantity": plan.planned_quantity,
            "asset_class": plan.asset_class.value,
            "broker": plan.broker,
            "initial_stop": plan.initial_stop,
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
        },
        "pending_entry": None
        if lifecycle.pending_entry is None
        else {
            "broker_order_id": lifecycle.pending_entry.broker_order_id,
            "requested_quantity": lifecycle.pending_entry.requested_quantity,
            "confirmed_filled_quantity": lifecycle.pending_entry.confirmed_filled_quantity,
            "remainder_resolved": lifecycle.pending_entry.remainder_resolved,
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
    stop = StopRecord(
        desired_price=stop_row.get("desired_price"),
        submitted_price=stop_row.get("submitted_price"),
        broker_confirmed_price=stop_row.get("broker_confirmed_price"),
        broker_order_id=stop_row.get("broker_order_id"),
        protected_quantity=stop_row.get("protected_quantity", 0.0),
        status=ProtectionStatus(stop_row.get("status", ProtectionStatus.UNPROTECTED.value)),
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
        )
    )

    return PositionLifecycle(
        plan=plan,
        confirmed_owned_quantity=row.get("confirmed_owned_quantity", 0.0),
        stop=stop,
        closed=row.get("closed", False),
        pending_exit=pending_exit,
        pending_entry=pending_entry,
    )
