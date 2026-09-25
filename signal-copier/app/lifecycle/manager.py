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

from app.brokers.base import BrokerAdapter
from app.lifecycle.close_arbiter import CloseArbiter
from app.lifecycle.models import (
    PositionLifecycle,
    PositionPlan,
    ProtectionStatus,
    Target,
    TargetAction,
)
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal, Side

logger = logging.getLogger(__name__)


class PositionLifecycleManager:
    def __init__(self, brokers: dict[str, BrokerAdapter], arbiter: CloseArbiter | None = None):
        self.brokers = brokers
        self.arbiter = arbiter or CloseArbiter()
        self._lifecycles: dict[tuple[str, str], PositionLifecycle] = {}

    def get_lifecycle(self, account_id: str, symbol: str) -> PositionLifecycle | None:
        return self._lifecycles.get((account_id, symbol))

    @staticmethod
    def validate_plan(plan: PositionPlan) -> str | None:
        """Design section 11: no provider stop, no released fallback -> NO ENTRY.
        The caller resolves fallbacks (provider -> strategy -> asset-level) and
        sets `plan.initial_stop` *before* calling this; this only enforces that
        something ended up there. Returns an error message if the plan must not
        be entered, or None if it's fine to proceed."""
        if plan.initial_stop is None:
            return (
                "no stop-loss resolved for this entry (no provider stop and no "
                "released fallback) — refusing to enter unprotected"
            )
        return None

    def start_plan(self, plan: PositionPlan) -> PositionLifecycle:
        """Register a plan BEFORE the entry is submitted (design section 1). Call
        `validate_plan` first; submit the entry order yourself after this; then
        call `on_entry_fill` with what actually filled."""
        lifecycle = PositionLifecycle(plan=plan)
        self._lifecycles[(plan.account_id, plan.symbol)] = lifecycle
        return lifecycle

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

        return lifecycle

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
            if tx.owned <= 0:
                lifecycle.closed = True

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
            actual_filled = exit_result.filled_quantity if exit_result.filled_quantity is not None else 0.0
            tx.settle(reserved_quantity=requested, filled_quantity=actual_filled)
            remaining = tx.owned

            if remaining <= 0:
                lifecycle.closed = True
            elif had_stop and lifecycle.stop.desired_price is not None:
                await self._place_stop_locked(lifecycle, account, broker, remaining, lifecycle.stop.desired_price)

            return exit_result

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
