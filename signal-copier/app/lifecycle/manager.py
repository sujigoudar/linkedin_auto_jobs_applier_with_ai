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

import logging
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
        since the last call protects exactly that much via `on_entry_fill`
        (first confirmed fill) or by resizing the existing stop (a later,
        larger confirmed fill) — never a duplicate stop placed alongside the
        first one. A repeated observation of the same `confirmed_filled_quantity`
        is a no-op. Once terminal: zero confirmed fill unregisters the plan
        (nothing to protect, nothing protecting it); otherwise the pending
        entry is simply cleared, since whatever was confirmed is already
        protected."""
        lifecycle = self._lifecycles.get((account.account_id, symbol))
        if lifecycle is None or lifecycle.pending_entry is None:
            return
        pending = lifecycle.pending_entry
        broker = self.brokers.get(account.broker)

        if confirmed_filled_quantity > pending.confirmed_filled_quantity:
            newly_applied = confirmed_filled_quantity - pending.confirmed_filled_quantity
            if lifecycle.stop.broker_order_id is None:
                # First confirmed fill for this entry -- on_entry_fill's normal
                # first-time placement path (persists internally).
                await self.on_entry_fill(account, symbol, confirmed_filled_quantity)
            else:
                # Already protected at a smaller confirmed quantity -- resize
                # the existing stop (cancel/replace) rather than placing a
                # second one alongside it.
                async with self.arbiter.transition(account.account_id, symbol) as tx:
                    lifecycle.confirmed_owned_quantity = confirmed_filled_quantity
                    tx.set_owned(confirmed_filled_quantity)
                if broker is not None:
                    await self._replace_stop_price(lifecycle, account)
                self._persist(lifecycle)
            pending.confirmed_filled_quantity = confirmed_filled_quantity
            # Applied immediately after the persisted protection-state change
            # above, with no `await` in between -- shrinking (not eliminating)
            # the crash window between "protection is durably recorded" and
            # "SignalStore's tracked position reflects it" to two back-to-back
            # local writes, in the safer order: if a crash lands between them,
            # the lifecycle already durably knows what it protected, and the
            # tracked position merely lags behind rather than risking a
            # duplicate stop placement on restart.
            if self.store is not None:
                self.store.record_fill(account.account_id, symbol, lifecycle.plan.side, newly_applied)

        if not remainder_cancelled and confirmed_filled_quantity < pending.requested_quantity:
            self._persist(lifecycle)
            return

        pending.remainder_resolved = True
        lifecycle.pending_entry = None

        if confirmed_filled_quantity <= 0:
            self.unregister_plan(account.account_id, symbol)
            return

        self._persist(lifecycle)

    async def on_stop_filled(
        self, account: DestinationAccount, symbol: str, filled_quantity: float, filled_price: float | None = None
    ) -> None:
        """Call when the broker reports the protective stop itself filled
        (reconciliation, or PaperBroker.simulate_price in tests)."""
        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle = self._lifecycles.get((account.account_id, symbol))
            if lifecycle is None:
                return
            tx.settle(reserved_quantity=0.0, filled_quantity=filled_quantity)
            lifecycle.stop.protected_quantity = 0.0
            lifecycle.stop.status = ProtectionStatus.UNPROTECTED
            lifecycle.stop.broker_order_id = None
            lifecycle.confirmed_owned_quantity = tx.owned
            if tx.owned <= 0:
                lifecycle.closed = True
        self._persist(lifecycle)

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
            target.fired = True
            if target.action == TargetAction.SELL:
                quantity = lifecycle.plan.planned_quantity * (target.reduce_fraction or 0.0)
                result = await self.request_exit(
                    account, symbol, quantity, source="target", reason=f"target @ {target.trigger_price}"
                )
                results.append(result)
            elif target.action == TargetAction.TIGHTEN_STOP:
                await self._tighten_stop_to(lifecycle, account, target.trigger_price)
            elif target.action == TargetAction.ACTIVATE_TRAIL:
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
                lifecycle.closed = True
            elif had_stop and lifecycle.stop.desired_price is not None:
                await self._place_stop_locked(lifecycle, account, broker, remaining, lifecycle.stop.desired_price)

            self._persist(lifecycle)
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

        If the remainder isn't resolved yet, this just records progress
        (`confirmed_filled_quantity`) and leaves the deficit open — it does
        NOT restore anything early."""
        broker = self.brokers.get(account.broker)
        async with self.arbiter.transition(account.account_id, symbol) as tx:
            lifecycle = self._lifecycles.get((account.account_id, symbol))
            if lifecycle is None or lifecycle.pending_exit is None:
                return
            pending = lifecycle.pending_exit

            if not remainder_cancelled and confirmed_filled_quantity < pending.requested_quantity:
                pending.confirmed_filled_quantity = confirmed_filled_quantity
                self._persist(lifecycle)
                return

            tx.settle(reserved_quantity=pending.requested_quantity, filled_quantity=confirmed_filled_quantity)
            pending.confirmed_filled_quantity = confirmed_filled_quantity
            pending.remainder_resolved = True
            pending.phase = TransferPhase.RESTORING
            remaining = tx.owned
            lifecycle.confirmed_owned_quantity = remaining
            lifecycle.pending_exit = None

            if remaining <= 0:
                lifecycle.closed = True
            elif lifecycle.stop.desired_price is not None and broker is not None:
                await self._place_stop_locked(lifecycle, account, broker, remaining, lifecycle.stop.desired_price)

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
        if result is None or result.status == OrderStatus.ERROR:
            lifecycle.stop.status = ProtectionStatus.UNPROTECTED
            lifecycle.stop.protected_quantity = 0.0
            logger.warning(
                "no protective stop in place for account=%s symbol=%s (broker '%s' has no "
                "verified place_protective_stop, or the submission failed)",
                account.account_id,
                lifecycle.plan.symbol,
                account.broker,
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
        trailing = lifecycle.plan.trailing
        if lifecycle.plan.side == Side.BUY:
            candidate_floor = price - trailing.trail_distance
            improved = trailing.floor_price is None or candidate_floor > trailing.floor_price
        else:
            candidate_floor = price + trailing.trail_distance
            improved = trailing.floor_price is None or candidate_floor < trailing.floor_price

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

                # No atomic in-place replace — cancel then resubmit. If cancellation can't be
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
