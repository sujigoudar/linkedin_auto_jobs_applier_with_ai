"""Corrects tracked positions once a broker confirms what a PENDING order
actually did.

Several brokers (SignalStack, Alpaca, IBKR, NinjaTrader, Rithmic) report
PENDING from `place_order` rather than a confirmed fill, so the engine
records an optimistic position update at request time (see
app/engine.py's docstring). This background loop periodically re-checks
those PENDING orders via each broker's optional `get_order_status()` (see
app/brokers/base.py) and corrects the tracked position — reversing it if
the order was actually rejected, or truing it up if the confirmed filled
quantity differs from the optimistic guess.

Only brokers that implement `get_order_status()` are checked (currently
Alpaca and IBKR — see their modules for how). Brokers without it are
silently skipped on every pass; their PENDING orders just stay PENDING and
optimistic in the position tracker, same as before this module existed.

## Managed-lifecycle pending exits

When a `PositionLifecycleManager` is wired in (`lifecycle_manager=`), this
loop also polls every unresolved `PendingExit` it's tracking (see
app/lifecycle/manager.py's `request_exit`/`resolve_pending_exit`) the same
way — via `get_order_status()` — and, once that exit order reaches a
terminal state, hands the result to `resolve_pending_exit`. That's the only
thing that settles the reservation and restores the protective stop after
a target/trailing exit that didn't fill synchronously; until this fires,
the position stays with those shares deliberately uncovered rather than
guessing at what the broker will still do with them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from signal_platform_contracts import Environment, EvidenceClass

from app import config
from app.brokers.base import BrokerAdapter
from app.capital_allocator import CapitalAllocator
from app.db import SignalStore
from app.export_events import build_execution_applied_envelope
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AssetClass, CommandType, DestinationAccount, OrderResult, OrderStatus, Side, UncertaintyState

logger = logging.getLogger(__name__)


class OrderReconciler:
    def __init__(
        self,
        store: SignalStore,
        brokers: dict[str, BrokerAdapter],
        interval_seconds: float = 30.0,
        lifecycle_manager: PositionLifecycleManager | None = None,
        capital_allocator: CapitalAllocator | None = None,
    ):
        self.store = store
        self.brokers = brokers
        self.interval_seconds = interval_seconds
        self.lifecycle_manager = lifecycle_manager
        # E03 (bounded): released in `_correct_position` once a PENDING
        # order this reserved capital for reaches a confirmed terminal
        # status -- see app/capital_allocator.py's "Known gap" section.
        # None (the default) is a safe no-op: every order's own
        # `reserved_notional` is then just never released here, same as
        # before this reservation-timing fix existed.
        self.capital_allocator = capital_allocator
        self._task: asyncio.Task | None = None
        #: See PriceMonitor.last_success_at (app/pricing.py) -- same contract,
        #: surfaced by app/main.py's /health.
        self.last_success_at: datetime | None = None
        # TR-06-A02/TR-03-A03: guards `run_now()` (the owner-facing on-demand
        # reconciliation trigger, POST /reconciliation/run-now) so a second
        # click while a manual pass is still in flight can't stack a second
        # concurrent `reconcile_once()` against the same DB/broker calls --
        # it reports `already_running` instead of starting another one. Does
        # NOT serialize against the background `_loop`'s own scheduled
        # passes; `reconcile_once()` was already written to tolerate being
        # called repeatedly (it re-reads pending state fresh every call).
        self._manual_run_lock = asyncio.Lock()

    async def start(self) -> None:
        # OPS-02: a second start() call used to unconditionally spawn a
        # SECOND background loop, orphaning the first (still running, no
        # longer referenced, never cancelled by stop()) -- two concurrent
        # reconciliation loops racing over the same store/brokers. Idempotent:
        # a call while one is already running is a no-op.
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.reconcile_once()
                self.last_success_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise  # see PriceMonitor._loop's identical comment
            except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
                logger.exception("error during order reconciliation pass")

    async def run_now(self) -> dict:
        """TR-06-A02/TR-03-A03: the real owner-facing on-demand reconciliation
        trigger -- runs the exact same `reconcile_once()` the background loop
        calls on its own schedule (see this module's docstring), synchronously,
        so the HTTP caller gets the real, immediate outcome rather than firing
        into the void. Reports how many pending orders/exits/entries were
        about to be re-examined (counted from the real store/lifecycle-manager
        state right before the pass, the same collections `reconcile_once`
        itself iterates) and how many actually changed state (`reconcile_once`'s
        own real return value -- never a fabricated count). If a manual pass
        is already in flight, returns `already_running=True` immediately
        instead of stacking a second concurrent pass."""
        if self._manual_run_lock.locked():
            return {"already_running": True, "corrected": 0, "orders_examined": 0}
        async with self._manual_run_lock:
            pending_orders = self.store.list_pending_orders()
            pending_exits = self.lifecycle_manager.list_pending_exits() if self.lifecycle_manager is not None else []
            pending_entries = (
                self.lifecycle_manager.list_pending_entries() if self.lifecycle_manager is not None else []
            )
            orders_examined = len(pending_orders) + len(pending_exits) + len(pending_entries)
            corrected = await self.reconcile_once()
            self.last_success_at = datetime.now(timezone.utc)
            return {
                "already_running": False,
                "orders_examined": orders_examined,
                "pending_orders_examined": len(pending_orders),
                "pending_exits_examined": len(pending_exits),
                "pending_entries_examined": len(pending_entries),
                "corrected": corrected,
            }

    async def reconcile_once(self) -> int:
        """Re-check every PENDING order once. Returns how many were corrected."""
        corrected = 0
        for order in self.store.list_pending_orders():
            broker = self.brokers.get(order["broker"])
            if broker is None:
                continue

            account = DestinationAccount(account_id=order["account_id"], broker=order["broker"])
            try:
                result = await broker.get_order_status(account, order["broker_order_id"])
            except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                logger.exception(
                    "get_order_status failed for account=%s order=%s", order["account_id"], order["id"]
                )
                continue

            # WP-27: E-07 ensure executed_at is set for export events
            if result is not None and result.status == OrderStatus.FILLED and result.executed_at is None:
                result.executed_at = datetime.now(timezone.utc)

            if result is None or result.status == OrderStatus.PENDING:
                # Still open on the broker's side. A PENDING result here can
                # carry partial-fill progress (see AlpacaBroker/IBKRBroker's
                # get_order_status) -- for a lifecycle-tracked position that's
                # exactly what `_reconcile_pending_entries` below independently
                # polls and acts on. Applying it here too, or letting
                # `update_order_status` overwrite this row's `filled_quantity`
                # with the broker's raw in-progress number, would corrupt the
                # baseline `_correct_position` needs once this order actually
                # reaches a terminal status -- so a non-terminal PENDING is
                # left alone here no matter which account it belongs to.
                continue

            # THIS SPECIFIC ORDER's own confirmed entry fill is owned exclusively
            # by `_reconcile_pending_entries` below (via `resolve_pending_entry`) --
            # applying `_correct_position` to it too would double-apply the same
            # confirmed fill a second time (once here, once there). That
            # exclusion is scoped to the exact order the lifecycle is waiting
            # on (matched by broker_order_id), not to every order that ever
            # shares this (account, symbol) -- an older plain order still
            # pending when the account later became managed_lifecycle, or a
            # lifecycle that already resolved and moved on, must still get
            # its own correction here; the mere existence of *some* lifecycle
            # for this symbol doesn't make it that order's owner. Still
            # update this row's display status (FILLED/REJECTED) so GET
            # /orders doesn't show it stuck at "pending" forever either way.
            lifecycle = (
                self.lifecycle_manager.get_lifecycle(order["account_id"], order["symbol"])
                if self.lifecycle_manager is not None
                else None
            )
            order_is_lifecycles_own_pending_order = lifecycle is not None and order["broker_order_id"] in (
                lifecycle.pending_entry.broker_order_id if lifecycle.pending_entry is not None else None,
                lifecycle.pending_exit.broker_order_id if lifecycle.pending_exit is not None else None,
            )
            if not order_is_lifecycles_own_pending_order:
                self._correct_position(order, result.status, result.filled_quantity, result, account)
            else:
                self.store.update_order_status(order["id"], result)
                # TRK-23: the audit's HIGH-severity gap -- this exact
                # branch is a managed-lifecycle fill THIS reconciler itself
                # is the sole confirmation of (a provider-entry or
                # provider_exit/manual_exit CLOSE that reported PENDING at
                # submission time -- see app/engine.py's own managed-
                # lifecycle `save_order_result` calls, which only ever
                # build an export envelope for a SYNCHRONOUS FILLED). It
                # never reached `_correct_position`/`_export_reconciled_
                # fill` above (that path is deliberately excluded for a
                # lifecycle's own pending order -- see this branch's own
                # comment above), so without this it was never exported at
                # all. `order["side"]` itself can't be used here -- a
                # managed CLOSE's own `orders` row stores `signal.side`
                # (literally `Side.CLOSE`) for this branch's own save
                # (see app/engine.py's managed branch), which
                # `build_execution_applied_envelope` refuses outright --
                # the lifecycle's own `plan.side`/`exit_side` is the real,
                # resolved BUY/SELL instead.
                if result.status == OrderStatus.FILLED:
                    assert lifecycle is not None  # order_is_lifecycles_own_pending_order guarantees this
                    is_pending_entry_order = (
                        lifecycle.pending_entry is not None
                        and order["broker_order_id"] == lifecycle.pending_entry.broker_order_id
                    )
                    export_side = lifecycle.plan.side if is_pending_entry_order else lifecycle.exit_side
                    self._export_lifecycle_resolved_fill(order, account, export_side, lifecycle.plan.asset_class, result)
            corrected += 1

        # D-01: Poll bracket child leg orders (stop/take-profit exits)
        corrected += await self._reconcile_pending_child_orders()
        corrected += await self._reconcile_pending_exits()
        corrected += await self._reconcile_pending_entries()
        corrected += await self._reconcile_unknown_submissions()
        if self.lifecycle_manager is not None:
            # WP-23 D-14: resolve old lost entries (pending with no order id,
            # broker_owned == 0, age > LOST_ENTRY_GRACE_SECONDS)
            corrected += await self._resolve_lost_entries()
            # PRO-04: retry protection for any owned-but-unprotected
            # lifecycle every pass, independent of whether a new fill
            # increment ever arrives to trigger it otherwise.
            corrected += await self.lifecycle_manager.retry_unprotected_positions()
            # PRO-02: a plan's time_exit is otherwise never checked against
            # the clock anywhere else -- this periodic pass is what actually
            # enforces it.
            corrected += await self.lifecycle_manager.check_time_exits()
            # OPS-03: a native stop (or any other broker-side execution
            # this service never directly observed -- e.g. one that fired
            # while the process was down) leaves the locally-tracked
            # lifecycle believing more is still owned than the venue
            # actually holds. Restarting and replaying the last persisted
            # PENDING orders/exits (above) can't catch this, since no
            # pending order was ever involved -- only asking the broker
            # for its own current position can.
            corrected += await self._reconcile_broker_positions()
        return corrected

    async def _reconcile_pending_child_orders(self) -> int:
        """D-01: Poll bracket child leg orders (stop/take_profit) for
        plain accounts. When a child order fills, apply the exit to
        positions and potentially cancel the sibling if still open."""
        corrected = 0
        for child_order in self.store.list_pending_child_orders():

            broker = self.brokers.get(child_order["broker"])
            if broker is None:
                continue

            account = DestinationAccount(
                account_id=child_order["account_id"], broker=child_order["broker"]
            )
            try:
                result = await broker.get_order_status(account, child_order["broker_order_id"])
            except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                logger.exception(
                    "get_order_status failed for child order account=%s order=%s",
                    child_order["account_id"],
                    child_order["id"],
                )
                continue

            if result is None or result.status == OrderStatus.PENDING:
                # Still open on the broker's side
                continue

            # Child order reached a terminal status -- update the row
            # and handle fills by applying to positions
            self.store.update_order_status(child_order["id"], result)

            if result.status == OrderStatus.FILLED:
                # Apply the child exit fill to positions
                # Child orders are exits (opposite side of the entry)
                # Get the quantity from the child order's requested_quantity
                filled_quantity = result.filled_quantity or child_order["requested_quantity"]

                # Determine the exit side (opposite of the entry side)
                entry_side = Side(child_order["entry_side"])
                exit_side = Side.SELL if entry_side == Side.BUY else Side.BUY

                # Record the fill to correct positions
                self.store.record_fill(
                    child_order["account_id"],
                    child_order["symbol"],
                    exit_side,
                    filled_quantity,
                )

                # Find and potentially cancel the sibling child order
                # (e.g., if stop filled, cancel the take-profit)
                sibling_purpose = (
                    "target_exit" if child_order["purpose"] == "stop_exit" else "stop_exit"
                )
                with self.store._connect() as conn:
                    sibling_orders = conn.execute(
                        """SELECT id, broker_order_id FROM orders
                           WHERE family_id = ? AND purpose = ? AND status = 'pending'""",
                        (child_order["family_id"], sibling_purpose),
                    ).fetchall()

                for sibling in sibling_orders:
                    try:
                        cancel_success = await broker.cancel_order(account, sibling[1])
                        if cancel_success:
                            # Update the sibling status to rejected
                            cancel_result = OrderResult(
                                account_id=child_order["account_id"],
                                status=OrderStatus.REJECTED,
                                signal_id="",
                                broker_order_id=sibling[1],
                                message="cancelled because sibling child order filled",
                            )
                            self.store.update_order_status(sibling[0], cancel_result)
                    except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                        logger.exception(
                            "cancel_order failed for sibling order account=%s order=%s",
                            child_order["account_id"],
                            sibling[1],
                        )

            corrected += 1

        return corrected
    async def _resolve_lost_entries(self) -> int:
        """WP-23 D-14: resolve pending entries that have no order ID, no broker
        ownership, and have exceeded the grace period (LOST_ENTRY_GRACE_SECONDS).
        These are lost-response entries that never reached the venue and should
        be auto-resolved to allow the account/symbol to be used again."""
        from app import config

        assert self.lifecycle_manager is not None  # only caller checks this
        resolved = 0

        for account_id, symbol, broker_name, pending in self.lifecycle_manager.list_pending_entries():
            # Only handle entries with no broker_order_id (lost-response case)
            if pending.broker_order_id is not None:
                continue

            # Check if this entry has been pending for longer than the grace period
            lifecycle = self.lifecycle_manager.get_lifecycle(account_id, symbol)
            if lifecycle is None or lifecycle.pending_entry is None:
                continue

            # Try to get the entry order's created_at time from the orders table
            # to determine if it has exceeded the grace period
            broker = self.brokers.get(broker_name)
            if broker is None:
                continue

            account = DestinationAccount(account_id=account_id, broker=broker_name)

            # Check broker position: only resolve if broker_owned == 0
            try:
                broker_owned = await broker.get_broker_position(account, symbol)
            except Exception:
                logger.exception(
                    "get_broker_position failed checking lost entry for account=%s symbol=%s",
                    account_id,
                    symbol,
                )
                continue

            if broker_owned is not None and broker_owned != 0:
                continue  # Entry may still be filling, don't resolve yet

            # Get the entry order from the orders table to check its age
            # Look for the most recent entry order for this account/symbol
            entry_orders = self.store.list_orders_for_signal(lifecycle.plan.entry_signal_id)
            if not entry_orders:
                # No order row found; can't determine age reliably
                continue

            entry_order = entry_orders[0]  # Most recent
            try:
                created_at = datetime.fromisoformat(entry_order["created_at"])
                age_seconds = (datetime.now(timezone.utc) - created_at.replace(tzinfo=timezone.utc)).total_seconds()

                if age_seconds > config.LOST_ENTRY_GRACE_SECONDS:
                    logger.warning(
                        "resolving lost entry for account=%s symbol=%s (age=%.0f s, grace=%.0f s)",
                        account_id,
                        symbol,
                        age_seconds,
                        config.LOST_ENTRY_GRACE_SECONDS,
                    )
                    await self.lifecycle_manager.resolve_pending_entry(
                        account, symbol, 0.0, remainder_cancelled=True
                    )
                    resolved += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "error checking age of lost entry for account=%s symbol=%s",
                    account_id,
                    symbol,
                )
                continue

        return resolved

    async def _reconcile_broker_positions(self) -> int:
        assert self.lifecycle_manager is not None  # only caller (reconcile_once) checks this first
        corrected = 0
        for lifecycle in list(self.lifecycle_manager.list_open_lifecycles()):
            exit_has_no_order_id_to_poll = (
                lifecycle.pending_exit is not None
                and lifecycle.pending_exit.broker_order_id is None
                and not lifecycle.pending_exit.remainder_resolved
            )
            if (
                lifecycle.pending_exit is not None
                and not lifecycle.pending_exit.remainder_resolved
                and not exit_has_no_order_id_to_poll
            ):
                continue  # a known order id is already resolving via _reconcile_pending_exits -- don't second-guess it with a raw read
            broker = self.brokers.get(lifecycle.plan.broker)
            if broker is None or not broker.has_position_readback_capability:
                continue
            account = DestinationAccount(account_id=lifecycle.plan.account_id, broker=lifecycle.plan.broker)
            try:
                broker_owned = await broker.get_broker_position(account, lifecycle.plan.symbol)
            except Exception:  # noqa: BLE001 - one broker's read failure must not block the rest
                logger.exception(
                    "get_broker_position failed for account=%s symbol=%s",
                    lifecycle.plan.account_id,
                    lifecycle.plan.symbol,
                )
                continue
            if broker_owned is None:
                continue  # genuinely unknown -- never treated as confirming zero

            # WP-03: convert broker readback to lifecycle's own orientation.
            # Broker returns signed quantity (negative for short); lifecycle
            # always tracks positive owned quantity. For SELL positions
            # (shorts), invert the sign to match.
            broker_owned_abs = -broker_owned if lifecycle.plan.side == Side.SELL else broker_owned
            if broker_owned_abs < 0:
                # Venue holds the OPPOSITE side of what the plan expects
                logger.warning(
                    "broker position readback for account=%s symbol=%s returned opposite side (venue: %s, plan: %s)",
                    lifecycle.plan.account_id,
                    lifecycle.plan.symbol,
                    broker_owned,
                    "short" if broker_owned < 0 else "long",
                )
                continue

            if exit_has_no_order_id_to_poll:
                # Exit-side counterpart of the entry branch below: a
                # request_exit whose place_order response was lost has no
                # broker_order_id to poll via _reconcile_pending_exits
                # either. How much of the reserved quantity actually left is
                # the drop between what this lifecycle still believed it
                # owned and what the venue now reports -- clamped to what
                # was actually requested, since the venue could also have
                # moved for an unrelated reason.
                pending = lifecycle.pending_exit
                assert pending is not None  # exit_has_no_order_id_to_poll guarantees this
                filled = min(
                    max(0.0, lifecycle.confirmed_owned_quantity - broker_owned_abs), pending.requested_quantity
                )
                await self.lifecycle_manager.resolve_pending_exit(
                    account, lifecycle.plan.symbol, filled, remainder_cancelled=True
                )
                corrected += 1
                continue

            # EXE-01: an entry whose place_order response was lost (see
            # app/engine.py's _handle_managed_entry) has no broker_order_id
            # to poll via _reconcile_pending_entries -- this broker-position
            # readback is the only way such an order's real outcome is ever
            # discovered. Whatever the venue shows now is treated as final
            # (remainder_cancelled=True): there's no order id left to keep
            # waiting on, so there's nothing more this could still become.
            if (
                lifecycle.pending_entry is not None
                and lifecycle.pending_entry.broker_order_id is None
                and not lifecycle.pending_entry.remainder_resolved
                and broker_owned_abs > 0
            ):
                await self.lifecycle_manager.resolve_pending_entry(
                    account, lifecycle.plan.symbol, broker_owned_abs, remainder_cancelled=True
                )
                corrected += 1
                continue

            deficit = lifecycle.confirmed_owned_quantity - broker_owned_abs

            # WP-23 D-16: handle venue > tracked adoption
            if deficit < -1e-9:
                # The venue owns MORE than we're tracking: adopt the difference
                # as owned and re-protect (log + TODO WP-34 alert)
                adopted_quantity = broker_owned_abs - lifecycle.confirmed_owned_quantity
                logger.warning(
                    "adopting additional quantity from venue for account=%s symbol=%s: "
                    "venue owns %.6f but tracked %.6f, adopting difference %.6f",
                    lifecycle.plan.account_id,
                    lifecycle.plan.symbol,
                    broker_owned_abs,
                    lifecycle.confirmed_owned_quantity,
                    adopted_quantity,
                )
                # TODO: WP-34 alert for venue > tracked adoption
                await self.lifecycle_manager.adopt_venue_ownership(
                    account, lifecycle.plan.symbol, broker_owned_abs
                )
                corrected += 1
                continue

            if deficit <= 1e-9:
                continue  # matches (or the venue reports MORE than tracked -- a different, unmodeled anomaly)

            # WP-19: Poll the stop before attributing a deficit to a stop fill.
            # If the stop has a broker_order_id, check its real status first.
            if lifecycle.stop.broker_order_id is not None:
                try:
                    stop_status = await broker.get_order_status(account, lifecycle.stop.broker_order_id)
                except Exception:
                    logger.exception(
                        "get_order_status failed for stop order account=%s symbol=%s broker_order_id=%s",
                        account.account_id,
                        lifecycle.plan.symbol,
                        lifecycle.stop.broker_order_id,
                    )
                    stop_status = None

                # If the stop is FILLED, route the venue's filled quantity through on_stop_filled
                if stop_status is not None and stop_status.status == OrderStatus.FILLED:
                    filled_qty = stop_status.filled_quantity if stop_status.filled_quantity is not None else lifecycle.stop.protected_quantity
                    await self.lifecycle_manager.on_stop_filled(
                        account,
                        lifecycle.plan.symbol,
                        filled_quantity=filled_qty,
                        filled_price=stop_status.filled_price,
                    )
                    corrected += 1
                    continue

                # Stop is not filled but there's a deficit: resize/cancel the stop first
                await self.lifecycle_manager.resize_stop_to_owned(account, lifecycle.plan.symbol, broker_owned_abs)

            # Apply the correction (if no stop to resize, or after resizing/cancelling)
            await self.lifecycle_manager.on_stop_filled(account, lifecycle.plan.symbol, filled_quantity=deficit)
            corrected += 1
        return corrected

    async def _reconcile_pending_entries(self) -> int:
        """Poll every managed-lifecycle position's unresolved entry (see
        app/lifecycle/manager.py's PendingEntry) and hand each new
        observation to `resolve_pending_entry` — the only thing allowed to
        place/resize protection for it, and (since this round) the one place
        that applies a confirmed increment to `SignalStore`'s tracked
        position too (immediately next to the persisted protection-state
        change, not here — see that method's docstring on why). A working
        partial fill whose remainder is still open gets acted on the same as
        a terminal one; only a timeout/lost response (still nothing new to
        report) is skipped. A no-op if no `PositionLifecycleManager` was
        wired in."""
        if self.lifecycle_manager is None:
            return 0

        resolved = 0
        for account_id, symbol, broker_name, pending in self.lifecycle_manager.list_pending_entries():
            broker = self.brokers.get(broker_name)
            if broker is None or pending.broker_order_id is None:
                continue

            account = DestinationAccount(account_id=account_id, broker=broker_name)
            try:
                result = await broker.get_order_status(account, pending.broker_order_id)
            except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                logger.exception(
                    "get_order_status failed for pending entry account=%s symbol=%s order=%s",
                    account_id,
                    symbol,
                    pending.broker_order_id,
                )
                continue

            if result is None or result.status not in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.PENDING):
                continue  # nothing new to report at all -- a timeout/lost response is not a rejection

            is_terminal = result.status in (OrderStatus.FILLED, OrderStatus.REJECTED)
            # A missing cumulative quantity is not a reversal to zero -- an
            # omitted field on a terminal response (e.g. a bare "rejected"
            # with no fill data) must not erase a fill that was already
            # confirmed on an earlier, more informative observation. Falling
            # back to the last known progress (rather than 0.0) means this
            # is a no-op below when that's genuinely all we still know.
            filled = result.filled_quantity if result.filled_quantity is not None else pending.confirmed_filled_quantity
            if not is_terminal and filled <= pending.confirmed_filled_quantity:
                continue  # a repeated observation of the same progress -- nothing new to act on

            await self.lifecycle_manager.resolve_pending_entry(account, symbol, filled, remainder_cancelled=is_terminal)
            resolved += 1

        return resolved

    async def _reconcile_pending_exits(self) -> int:
        """Poll every managed-lifecycle position's unresolved exit (see
        app/lifecycle/manager.py's PendingExit) and hand each new
        observation to `resolve_pending_exit` — the only thing allowed to
        settle the reservation and restore the protective stop. A working
        partial fill whose remainder is still open gets acted on too
        (EXE-06: `resolve_pending_exit` applies a confirmed increment to
        `SignalStore` immediately without restoring the stop early — this
        used to skip anything short of a terminal status entirely, leaving
        a confirmed partial unreflected until the whole order finished).
        Only a timeout/lost response (still nothing new to report) is
        skipped. A no-op if no `PositionLifecycleManager` was wired in."""
        if self.lifecycle_manager is None:
            return 0

        resolved = 0
        for account_id, symbol, broker_name, pending in self.lifecycle_manager.list_pending_exits():
            broker = self.brokers.get(broker_name)
            if broker is None or pending.broker_order_id is None:
                continue

            account = DestinationAccount(account_id=account_id, broker=broker_name)
            try:
                result = await broker.get_order_status(account, pending.broker_order_id)
            except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                logger.exception(
                    "get_order_status failed for pending exit account=%s symbol=%s order=%s",
                    account_id,
                    symbol,
                    pending.broker_order_id,
                )
                continue

            if result is None or result.status not in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.PENDING):
                continue  # nothing new to report at all -- a timeout/lost response is not a rejection

            is_terminal = result.status in (OrderStatus.FILLED, OrderStatus.REJECTED)
            # A missing quantity isn't a reversal to zero -- fall back to
            # whatever was last confirmed rather than erasing it.
            filled = result.filled_quantity if result.filled_quantity is not None else pending.confirmed_filled_quantity
            if not is_terminal and filled <= pending.confirmed_filled_quantity:
                continue  # a repeated observation of the same progress -- nothing new to act on

            await self.lifecycle_manager.resolve_pending_exit(
                account, symbol, filled, remainder_cancelled=is_terminal, filled_price=result.filled_price
            )
            resolved += 1

        return resolved

    async def _reconcile_unknown_submissions(self) -> int:
        """Resolve UNKNOWN_AMBIGUOUS command ledger entries by attempting to
        look up the submitted order at its broker using the client_order_id
        (the command ledger's idempotency key).

        Returns how many entries were resolved (either confirmed as placed or
        rejected as not placed).
        """
        resolved = 0
        for ledger_entry in self.store.list_unresolved_command_ledger_entries():
            # Only process ENTRY commands in UNKNOWN_AMBIGUOUS state
            if (
                ledger_entry.command_type != CommandType.ENTRY
                or ledger_entry.uncertainty_state != UncertaintyState.UNKNOWN_AMBIGUOUS
            ):
                continue

            # The idempotency key is the client_order_id we assigned before submission
            client_order_id = ledger_entry.idempotency_key
            account_id = ledger_entry.account_id
            terminal_evidence = ledger_entry.terminal_evidence or {}

            # Try each broker that has client_id lookup capability
            order_found = False
            for broker_name, broker in self.brokers.items():
                if not broker.has_client_id_lookup_capability:
                    continue

                account = DestinationAccount(account_id=account_id, broker=broker_name)
                try:
                    broker_order_id = await broker.find_order_by_client_id(account, client_order_id)
                    if broker_order_id is not None:
                        # Order found at this broker -- mark as confirmed
                        self.store.mark_command_ledger_outcome(
                            client_order_id,
                            uncertainty_state=UncertaintyState.CONFIRMED,
                            terminal_evidence={
                                **terminal_evidence,
                                "resolution": "found_at_broker",
                                "broker": broker_name,
                            },
                            remote_identifiers={"broker_order_id": broker_order_id},
                        )
                        order_found = True
                        resolved += 1
                        break
                except Exception:  # noqa: BLE001 - one broker's lookup failure must not block the rest
                    logger.exception(
                        "find_order_by_client_id failed for account=%s client_order_id=%s",
                        account_id,
                        client_order_id,
                    )
                    continue

            if not order_found:
                # Order not found at any broker -- mark as rejected and release capital
                self.store.mark_command_ledger_outcome(
                    client_order_id,
                    uncertainty_state=UncertaintyState.REJECTED_CONFIRMED,
                    terminal_evidence={
                        **terminal_evidence,
                        "resolution": "not_placed",
                    },
                )
                # Release the capital reservation since the order was not placed
                if self.capital_allocator is not None:
                    reserved_notional = terminal_evidence.get("reserved_notional", 0.0)
                    if reserved_notional > 0:
                        self.capital_allocator.release(
                            account_id,
                            reserved_notional,
                            signal_id=terminal_evidence.get("signal_id"),
                        )
                resolved += 1

        return resolved

    def _correct_position(
        self,
        order: dict,
        new_status: OrderStatus,
        confirmed_quantity: float | None,
        result: OrderResult,
        account: DestinationAccount,
    ) -> None:
        if not order["symbol"] or not order["side"]:
            self.store.update_order_status(order["id"], result)
            self._release_reservation_if_any(order)
            return  # nothing was optimistically recorded for this order to correct

        side = Side(order["side"])
        # AUD-01: `order["filled_quantity"]` is this row's own
        # `applied_execution_delta` baseline -- since app/engine.py no
        # longer optimistically applies the full requested quantity for a
        # PENDING order with nothing confirmed yet, this is correctly 0.0
        # for a genuinely-still-unconfirmed order (never an inflated
        # guess), or the real confirmed partial amount already applied for
        # one that reported partial progress alongside PENDING.
        optimistic_quantity = order["filled_quantity"] or 0.0
        signed_delta = 0.0
        confirmed_cumulative_fill: float | None = None

        if new_status == OrderStatus.FILLED:
            # B5: a broker like Alpaca/IBKR reports PENDING at placement
            # time and only confirms FILLED later, right here -- see this
            # module's own docstring. app/engine.py's own
            # `_build_export_envelope` only ever runs for a result that's
            # ALREADY FILLED synchronously at placement time, so this was
            # the one confirmed-fill path that never exported an
            # EXECUTION_APPLIED event to the commercial platform at all --
            # a real fill the reconciler itself is the sole confirmation
            # of. Exported here, before `correct_position_and_update_order_status`
            # below, using `order`'s own `signal_id`/`asset_class`/`analyst`
            # (see `list_pending_orders`'s own docstring on why the JOIN
            # exists) exactly the way `_build_export_envelope` already does
            # for the synchronous-fill path.
            self._export_reconciled_fill(order, account, side, result)

        if new_status == OrderStatus.REJECTED:
            # REJECTED also covers "canceled"/"expired" on adapters like Alpaca
            # (see its get_order_status), and a canceled order can still carry
            # a real partial fill from before the cancellation -- reverse only
            # the UNFILLED remainder of what was optimistically applied, not
            # the whole thing, or a genuinely-filled partial quantity gets
            # wiped to zero. `confirmed_quantity` is None (treated as 0) only
            # for an adapter that doesn't report a fill on rejection, meaning
            # "assume nothing filled," same as this branch's old behavior.
            actual_quantity = confirmed_quantity if confirmed_quantity is not None else 0.0
            delta = actual_quantity - optimistic_quantity
            signed_delta = delta if side == Side.BUY else -delta
            confirmed_cumulative_fill = actual_quantity
        elif new_status == OrderStatus.FILLED:
            # WP-04: adapter reported FILLED but omitted filled_quantity.
            # Fall back to requested_quantity (mirroring app/engine.py:1285),
            # then to optimistic_quantity if requested_quantity is also None.
            if confirmed_quantity is not None:
                actual_quantity = confirmed_quantity
            elif order.get("requested_quantity") is not None:
                actual_quantity = order["requested_quantity"]
                logger.warning(
                    "adapter reported FILLED without filled_quantity for order=%s; using requested_quantity=%s",
                    order["id"],
                    actual_quantity,
                )
            else:
                actual_quantity = optimistic_quantity
            delta = actual_quantity - optimistic_quantity
            signed_delta = delta if side == Side.BUY else -delta
            confirmed_cumulative_fill = actual_quantity

        # The position correction and this order row's terminal status are
        # committed together (EXE-03): applying `signed_delta` in one write
        # and marking the row done in a separate, later write left a window
        # where an interruption between them caused the NEXT reconciliation
        # pass to recompute and re-apply the exact same correction against
        # the still-stale `orders.filled_quantity` baseline (a 100-unit buy
        # canceled with 30 filled was observed reaching -40, not the correct
        # 30, after exactly this interruption).
        #
        # AUD-01: this order has now reached a terminal status (FILLED or
        # REJECTED -- the only two `new_status` values this method is ever
        # called with, see `reconcile_once`'s own PENDING-skip guard), so
        # `outstanding_possible_fill` is unconditionally 0.0 (nothing more
        # can fill) and `applied_execution_delta` is exactly this call's
        # own `signed_delta` -- the quantity this call itself just applied
        # to `positions.net_quantity`.
        self.store.correct_position_and_update_order_status(
            order["id"],
            order["account_id"],
            order["symbol"],
            signed_delta,
            result,
            confirmed_cumulative_fill=confirmed_cumulative_fill,
            applied_execution_delta=signed_delta,
            outstanding_possible_fill=0.0,
        )
        self._release_reservation_if_any(order)

    def _export_reconciled_fill(
        self, order: dict, account: DestinationAccount, side: Side, result: OrderResult
    ) -> None:
        """Mirrors app/engine.py's `_build_export_envelope` for a fill this
        reconciler itself confirmed (rather than one the engine saw
        synchronously at placement time) -- same builder, same per-
        deployment config, same per-(account, source_stream) export
        sequence from the outbox. Returns early (no export, matching
        `build_execution_applied_envelope`'s own contract) if `result` is
        missing a field the payload actually requires -- e.g. a broker
        that reports a terminal FILLED status without a `filled_price`."""
        source_stream = f"signal-copier:{account.account_id}"
        envelope = build_execution_applied_envelope(
            result,
            account=account,
            symbol=order["symbol"],
            side=side,
            asset_class=AssetClass(order["asset_class"]),
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[config.RELAY_EVIDENCE_CLASS],
            environment=Environment[config.RELAY_ENVIRONMENT],
            originating_source_event_id=order["signal_id"],
            originating_analyst_id=order["analyst"],
        )
        if envelope is not None:
            self.store.append_export_event(envelope)

    def _export_lifecycle_resolved_fill(
        self, order: dict, account: DestinationAccount, side: Side, asset_class: AssetClass, result: OrderResult
    ) -> None:
        """TRK-23: `_export_reconciled_fill`'s counterpart for a managed-
        lifecycle order this reconciler itself resolved (a provider entry,
        or a provider_exit/manual_exit CLOSE) -- see `reconcile_once`'s own
        comment on why `_export_reconciled_fill` above is never reached for
        one of these. `side`/`asset_class` are passed in explicitly, read
        from the lifecycle's own `PositionPlan` (`plan.side` for an entry,
        `exit_side` for an exit) rather than `order["side"]`/
        `order["asset_class"]` -- a managed CLOSE's own `orders` row can
        store `Side.CLOSE` for `side` (see the call site's own comment),
        which `build_execution_applied_envelope` refuses outright; the
        lifecycle is the one real source of the resolved BUY/SELL here.
        Same builder/config/per-(account, source_stream) export sequence as
        `_export_reconciled_fill`; same honest no-export contract when
        `result` is missing a field the payload actually requires."""
        source_stream = f"signal-copier:{account.account_id}"
        envelope = build_execution_applied_envelope(
            result,
            account=account,
            symbol=order["symbol"],
            side=side,
            asset_class=asset_class,
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[config.RELAY_EVIDENCE_CLASS],
            environment=Environment[config.RELAY_ENVIRONMENT],
            originating_source_event_id=order["signal_id"],
            originating_analyst_id=order["analyst"],
        )
        if envelope is not None:
            self.store.append_export_event(envelope)

    def _release_reservation_if_any(self, order: dict) -> None:
        """E03 (bounded): `order`'s status is now confirmed terminal
        (REJECTED/FILLED) -- release the notional it reserved, if any,
        exactly once. `order` only ever carries a `reserved_notional` when
        it was set at admission time specifically because this exact
        poll-and-resolve path was guaranteed to eventually run (see
        `save_order_result`'s docstring) -- so this is the one place
        allowed to release it."""
        reserved_notional = order.get("reserved_notional")
        if self.capital_allocator is not None and reserved_notional:
            self.capital_allocator.release(order["account_id"], reserved_notional, signal_id=order.get("signal_id"))
