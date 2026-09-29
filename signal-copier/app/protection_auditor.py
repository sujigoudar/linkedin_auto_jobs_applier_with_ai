"""Independent SL/TP verification -- re-derives whether every open position
is actually protected directly from each broker's own order book, WITHOUT
trusting `PositionLifecycleManager`'s own `StopRecord`/`covered_quantity`
bookkeeping for either half of that question (is this actually owned, is it
actually protected).

## Why this has to be a separate module

`app/lifecycle/manager.py`'s `PositionLifecycleManager` both PLACES
protection (`_place_stop_locked`, `_replace_stop_price`) and tracks whether
it believes a position is protected (`StopRecord.status`). That's
architecturally necessary for it to do its job -- but it means a bug in
that code can make placement and monitoring agree on the same wrong answer
at the same time (e.g. a broker rejects a stop replace but the code still
marks `STOP_CONFIRMED`; a persisted/restored lifecycle whose real broker
order was cancelled out-of-band). `PositionLifecycleManager`'s own module
docstring already names this as an open gap: "Startup reconciliation
against the broker's own live position/order state ... isn't implemented
here -- this manager trusts its own in-memory + CloseArbiter bookkeeping."

`ProtectionAuditor` is that missing check, kept deliberately independent:
it never reads `PositionLifecycle.stop` or `CloseArbiter`'s ledger to
decide whether a position is protected -- only `BrokerAdapter.
get_broker_position` (real owned quantity) and `BrokerAdapter.
list_open_orders` (real resting orders), both read straight from the
broker. The one thing it DOES reuse from `PositionLifecycleManager` is its
existing, already-tested order-placement machinery
(`resync_stop_from_audit`) to fix an unambiguous deficit -- this module
never talks to a broker's order-entry endpoint directly; see that method's
own docstring for why reusing it (rather than building a second
order-placement path here) is still safe even though the bug being audited
for might be inside `PositionLifecycleManager` itself.

## What "unambiguous" means here

Only three shapes are ever auto-repaired, and each is exactly the kind of
finding where there is only one honest interpretation of the broker's own
data:

- `NO_STOP`: broker owns a real nonzero quantity, has zero resting orders
  that look like a stop, and this service has an existing managed
  lifecycle (so a known intended stop price exists to repair to).
- `UNDER_PROTECTED` / `OVER_PROTECTED`: exactly ONE resting stop order,
  correct side, correct symbol, wrong quantity.

Everything else -- multiple resting stop orders for one symbol, a resting
order whose role this adapter can't classify, a stop on the wrong side or
wrong symbol, a broker that can't even answer the question -- is recorded
as `UNKNOWN` (or its own specific-but-still-unrepaired status) and NEVER
silently "fixed." An `UNKNOWN` finding also halts the position via
`CloseArbiter.halt` (the same halt `PositionLifecycleManager` itself
enforces against every close-intent), so a human has to look at it before
anything else touches that position.
"""
from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side

logger = logging.getLogger(__name__)

_EPSILON = 1e-9


class AuditStatus(str, enum.Enum):
    FULLY_PROTECTED = "fully_protected"
    UNDER_PROTECTED = "under_protected"
    OVER_PROTECTED = "over_protected"
    NO_STOP = "no_stop"
    NO_TARGET = "no_target"
    WRONG_QUANTITY = "wrong_quantity"
    WRONG_SIDE = "wrong_side"
    WRONG_INSTRUMENT = "wrong_instrument"
    STALE_ORDER = "stale_order"
    UNKNOWN = "unknown"


#: Statuses that leave real capital exposed and are worth surfacing as
#: incidents even when no repair is possible (see `list_incidents`).
_AT_RISK_STATUSES = frozenset(
    {
        AuditStatus.UNDER_PROTECTED,
        AuditStatus.NO_STOP,
        AuditStatus.WRONG_SIDE,
        AuditStatus.WRONG_INSTRUMENT,
        AuditStatus.STALE_ORDER,
        AuditStatus.UNKNOWN,
    }
)

#: The only statuses this module will ever attempt to repair -- see the
#: module docstring's "What 'unambiguous' means here."
_REPAIRABLE_STATUSES = frozenset({AuditStatus.NO_STOP, AuditStatus.UNDER_PROTECTED, AuditStatus.OVER_PROTECTED})


@dataclass
class ProtectionAuditFinding:
    account_id: str
    symbol: str
    broker: str
    status: AuditStatus
    detail: str
    #: The broker's own reported position, independent of anything this
    #: service tracks -- None means the broker couldn't answer at all
    #: (never conflated with 0.0, "confirmed flat").
    broker_position_quantity: float | None = None
    stop_orders_found: int = 0
    repaired: bool = False
    repair_detail: str = ""
    audited_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "symbol": self.symbol,
            "broker": self.broker,
            "status": self.status.value,
            "detail": self.detail,
            "broker_position_quantity": self.broker_position_quantity,
            "stop_orders_found": self.stop_orders_found,
            "repaired": self.repaired,
            "repair_detail": self.repair_detail,
            "audited_at": self.audited_at.isoformat(),
        }


class ProtectionAuditor:
    def __init__(
        self,
        brokers: dict[str, BrokerAdapter],
        *,
        lifecycle_manager: PositionLifecycleManager | None = None,
        accounts: dict[str, DestinationAccount] | None = None,
        store: SignalStore | None = None,
        interval_seconds: float = 300.0,
    ):
        self.brokers = brokers
        self.lifecycle_manager = lifecycle_manager
        #: Every configured account (routing_config.accounts) -- lets this
        #: audit a position that's tracked locally (app/db.py's `positions`
        #: table) but has NO managed lifecycle (a native-bracket/unmanaged
        #: account), where repair is never attempted but a finding is still
        #: genuinely useful. Paired with `store` below -- both or neither;
        #: with either missing, `audit_all` only covers managed-lifecycle
        #: positions, a safe, smaller default, not a failure.
        self.accounts = accounts or {}
        self.store = store
        self.interval_seconds = interval_seconds
        self._findings: dict[tuple[str, str], ProtectionAuditFinding] = {}
        self._task: asyncio.Task | None = None
        #: Same contract as OrderReconciler.last_success_at/PriceMonitor's
        #: identical field -- surfaced by app/main.py's /health.
        self.last_success_at: datetime | None = None

    # --- background scheduling (same start/stop/_loop shape as
    # app/reconciliation.py's OrderReconciler / app/relay_scheduler.py's
    # RelayScheduler -- no new scheduling framework) ---

    async def start(self) -> None:
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
                await self.audit_all()
                self.last_success_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
                logger.exception("error during protection audit pass")

    # --- queries (for GET /protection-audit, GET /positions/.../protection-audit) ---

    def get_finding(self, account_id: str, symbol: str) -> ProtectionAuditFinding | None:
        return self._findings.get((account_id, symbol))

    def list_findings(self) -> list[ProtectionAuditFinding]:
        return list(self._findings.values())

    def list_incidents(self) -> list[ProtectionAuditFinding]:
        """Findings that leave real capital exposed with nothing (yet)
        fixing it -- either unrepairable by design (ambiguous/UNKNOWN,
        wrong side/instrument, no managed lifecycle to repair through) or a
        repair was attempted and did not succeed. Never includes a
        `repaired=True` finding: a successful repair is exactly this
        module doing its job, not an incident to escalate."""
        return [f for f in self._findings.values() if f.status in _AT_RISK_STATUSES and not f.repaired]

    # --- the audit itself ---

    async def audit_all(self) -> list[ProtectionAuditFinding]:
        """Every open position this service knows about, from two sources:
        every open managed-lifecycle position (where a repair path also
        exists), plus every OTHER locally-tracked nonzero position on a
        known account that has no managed lifecycle (detection only, no
        repair -- see `audit_position`'s docstring)."""
        seen: set[tuple[str, str]] = set()
        findings: list[ProtectionAuditFinding] = []

        if self.lifecycle_manager is not None:
            for lifecycle in self.lifecycle_manager.list_open_lifecycles():
                account = DestinationAccount(account_id=lifecycle.plan.account_id, broker=lifecycle.plan.broker)
                key = (account.account_id, lifecycle.plan.symbol)
                seen.add(key)
                findings.append(await self.audit_position(account, lifecycle.plan.symbol))

        if self.store is not None and self.accounts:
            for position in self.store.list_open_positions():
                key = (position["account_id"], position["symbol"])
                if key in seen:
                    continue
                unmanaged_account = self.accounts.get(position["account_id"])
                if unmanaged_account is None:
                    continue  # not a currently-configured account -- nothing to audit against
                seen.add(key)
                findings.append(await self.audit_position(unmanaged_account, position["symbol"]))

        return findings

    async def audit_position(
        self, account: DestinationAccount, symbol: str, *, repair: bool = True
    ) -> ProtectionAuditFinding:
        """Independently classify ONE position. `repair=False` is used
        internally to re-derive the post-repair status without recursing
        into another repair attempt (see the tail of this method)."""
        broker = self.brokers.get(account.broker)
        if broker is None:
            return await self._record(
                account,
                symbol,
                AuditStatus.UNKNOWN,
                "no broker adapter registered for this account's broker -- cannot verify",
            )

        if not broker.has_position_readback_capability:
            return await self._record(
                account,
                symbol,
                AuditStatus.UNKNOWN,
                f"broker '{account.broker}' has no verified get_broker_position -- protection cannot be "
                "independently verified for this broker at all (a real, disclosed capability gap, not a "
                "finding about this specific position)",
            )

        try:
            broker_qty = await broker.get_broker_position(account, symbol)
        except Exception:  # noqa: BLE001 - one broker's failure must not block auditing the rest
            logger.exception("get_broker_position failed during audit for account=%s symbol=%s", account.account_id, symbol)
            return await self._record(account, symbol, AuditStatus.UNKNOWN, "get_broker_position raised an exception")

        if broker_qty is None:
            # Never treat "the broker couldn't answer" as "confirmed flat" --
            # same convention as every other optional capability in this
            # codebase (see app/brokers/base.py's docstrings).
            return await self._record(account, symbol, AuditStatus.UNKNOWN, "broker returned no position data (None)")

        if abs(broker_qty) < _EPSILON:
            return await self._audit_flat_position(account, symbol, broker)

        return await self._audit_open_position(account, symbol, broker, broker_qty, repair=repair)

    async def _audit_flat_position(
        self, account: DestinationAccount, symbol: str, broker: BrokerAdapter
    ) -> ProtectionAuditFinding:
        if not broker.has_open_orders_capability:
            return await self._record(
                account, symbol, AuditStatus.FULLY_PROTECTED, "flat at the broker -- nothing to protect",
                broker_position_quantity=0.0,
            )
        try:
            open_orders = await broker.list_open_orders(account, symbol)
        except Exception:  # noqa: BLE001
            logger.exception("list_open_orders failed during audit for account=%s symbol=%s", account.account_id, symbol)
            open_orders = None
        if open_orders:
            return await self._record(
                account,
                symbol,
                AuditStatus.STALE_ORDER,
                f"broker is flat but {len(open_orders)} resting order(s) remain for this symbol -- "
                "leftover coverage on a position that no longer exists",
                broker_position_quantity=0.0,
                stop_orders_found=len(open_orders),
            )
        return await self._record(
            account, symbol, AuditStatus.FULLY_PROTECTED, "flat at the broker -- nothing to protect",
            broker_position_quantity=0.0,
        )

    async def _audit_open_position(
        self,
        account: DestinationAccount,
        symbol: str,
        broker: BrokerAdapter,
        broker_qty: float,
        *,
        repair: bool,
    ) -> ProtectionAuditFinding:
        expected_exit_side = Side.SELL if broker_qty > 0 else Side.BUY
        abs_qty = abs(broker_qty)

        if not broker.has_open_orders_capability:
            return await self._record(
                account,
                symbol,
                AuditStatus.UNKNOWN,
                f"broker owns {abs_qty} of {symbol} but has no verified way to enumerate resting orders -- "
                "protection cannot be independently verified for this broker",
                broker_position_quantity=broker_qty,
            )

        try:
            open_orders = await broker.list_open_orders(account, symbol)
        except Exception:  # noqa: BLE001
            logger.exception("list_open_orders failed during audit for account=%s symbol=%s", account.account_id, symbol)
            return await self._record(
                account, symbol, AuditStatus.UNKNOWN, "list_open_orders raised an exception",
                broker_position_quantity=broker_qty,
            )
        if open_orders is None:
            return await self._record(
                account, symbol, AuditStatus.UNKNOWN, "broker returned no order data (None)",
                broker_position_quantity=broker_qty,
            )

        # Defensive: a well-behaved adapter only returns orders for the
        # symbol it was asked about, but never trust that silently -- a
        # cross-symbol order in the response is itself worth flagging
        # rather than quietly filtered out.
        wrong_instrument_orders = [o for o in open_orders if o.symbol != symbol]
        symbol_orders = [o for o in open_orders if o.symbol == symbol]
        stop_orders = [o for o in symbol_orders if o.role == "stop"]
        other_orders = [o for o in symbol_orders if o.role != "stop"]

        if wrong_instrument_orders and not stop_orders:
            return await self._record(
                account,
                symbol,
                AuditStatus.WRONG_INSTRUMENT,
                f"{len(wrong_instrument_orders)} resting order(s) returned for a different symbol than "
                f"requested ({sorted({o.symbol for o in wrong_instrument_orders})}) -- broker order data "
                "looks unreliable for this query",
                broker_position_quantity=broker_qty,
            )

        if not stop_orders:
            if other_orders:
                return await self._record(
                    account,
                    symbol,
                    AuditStatus.UNKNOWN,
                    f"no order recognizable as a stop, but {len(other_orders)} other resting order(s) present "
                    "for this symbol -- ambiguous, not treated as unprotected",
                    broker_position_quantity=broker_qty,
                    stop_orders_found=0,
                )
            finding = await self._record(
                account,
                symbol,
                AuditStatus.NO_STOP,
                f"broker owns {abs_qty} of {symbol} with zero resting stop orders",
                broker_position_quantity=broker_qty,
                stop_orders_found=0,
            )
            if repair:
                await self._attempt_repair(finding, account, symbol, abs_qty, expected_exit_side)
            return finding

        if len(stop_orders) > 1:
            finding = await self._record(
                account,
                symbol,
                AuditStatus.UNKNOWN,
                f"{len(stop_orders)} resting stop orders found for one position -- ambiguous, refusing to "
                "guess which one is authoritative",
                broker_position_quantity=broker_qty,
                stop_orders_found=len(stop_orders),
            )
            await self._halt(account, symbol, finding.detail)
            return finding

        stop = stop_orders[0]

        if stop.side != expected_exit_side:
            finding = await self._record(
                account,
                symbol,
                AuditStatus.WRONG_SIDE,
                f"resting stop side={stop.side.value} does not match the required exit side="
                f"{expected_exit_side.value} for a {broker_qty} position in {symbol}",
                broker_position_quantity=broker_qty,
                stop_orders_found=1,
            )
            return finding

        diff = stop.quantity - abs_qty
        if abs(diff) < _EPSILON:
            return await self._record(
                account,
                symbol,
                AuditStatus.FULLY_PROTECTED,
                f"stop covers {stop.quantity}, matching the {abs_qty} actually owned",
                broker_position_quantity=broker_qty,
                stop_orders_found=1,
            )

        if diff < 0:
            finding = await self._record(
                account,
                symbol,
                AuditStatus.UNDER_PROTECTED,
                f"stop covers only {stop.quantity} of the {abs_qty} actually owned (deficit {abs(diff)})",
                broker_position_quantity=broker_qty,
                stop_orders_found=1,
            )
        else:
            finding = await self._record(
                account,
                symbol,
                AuditStatus.OVER_PROTECTED,
                f"stop covers {stop.quantity}, more than the {abs_qty} actually owned -- an oversell risk if "
                "it fires alongside another exit",
                broker_position_quantity=broker_qty,
                stop_orders_found=1,
            )

        if repair:
            await self._attempt_repair(
                finding, account, symbol, abs_qty, expected_exit_side, desired_price_hint=stop.price
            )
        return finding

    async def _attempt_repair(
        self,
        finding: ProtectionAuditFinding,
        account: DestinationAccount,
        symbol: str,
        true_quantity: float,
        exit_side: Side,
        *,
        desired_price_hint: float | None = None,
    ) -> None:
        """Only ever called for `_REPAIRABLE_STATUSES` -- see the module
        docstring's "What 'unambiguous' means here." Reuses
        `PositionLifecycleManager.resync_stop_from_audit`, never places an
        order itself."""
        assert finding.status in _REPAIRABLE_STATUSES
        if self.lifecycle_manager is None:
            finding.detail += " -- no PositionLifecycleManager wired in; reporting only, no repair path available"
            return

        lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
        if lifecycle is None:
            finding.detail += (
                " -- no managed lifecycle tracked for this position; ProtectionAuditor has no repair path "
                "for an unmanaged/native-bracket account, reporting only"
            )
            return

        price = lifecycle.stop.desired_price or lifecycle.plan.initial_stop or desired_price_hint
        if price is None:
            finding.detail += " -- no known intended stop price to repair to; logging incident instead of guessing a level"
            return

        try:
            result = await self.lifecycle_manager.resync_stop_from_audit(account, symbol, true_quantity, price, exit_side)
        except Exception:  # noqa: BLE001 - a repair failure must not crash the audit pass
            logger.exception("resync_stop_from_audit raised for account=%s symbol=%s", account.account_id, symbol)
            finding.repair_detail = "repair attempt raised an exception -- see logs"
            return

        if result is None or result.status in (OrderStatus.ERROR, OrderStatus.REJECTED):
            finding.repair_detail = (
                "repair attempt failed -- broker did not confirm a resting stop"
                if result is None
                else f"repair attempt failed -- broker returned {result.status.value}: {result.message}"
            )
            return

        # Re-verify from the broker's own data rather than trusting the
        # placement response alone -- `repair=False` so this can't recurse
        # into another repair attempt.
        post_repair = await self.audit_position(account, symbol, repair=False)
        finding.status = post_repair.status
        finding.broker_position_quantity = post_repair.broker_position_quantity
        finding.stop_orders_found = post_repair.stop_orders_found
        finding.detail += f" | post-repair: {post_repair.detail}"
        finding.repaired = finding.status == AuditStatus.FULLY_PROTECTED
        finding.repair_detail = f"placed/replaced protective stop for {true_quantity} @ {price}"
        self._findings[(account.account_id, symbol)] = finding

    async def _halt(self, account: DestinationAccount, symbol: str, reason: str) -> None:
        """Block conflicting operations on an ambiguous position rather
        than fabricate a resolution -- reuses the exact same halt
        `PositionLifecycleManager`'s own invariant check uses
        (CloseArbiter.halt), so a halted position refuses every future
        `reserve()` (and therefore every `request_exit`) the same way
        either halt reason would."""
        if self.lifecycle_manager is None:
            return
        await self.lifecycle_manager.arbiter.halt(account.account_id, symbol, f"protection audit: {reason}")

    async def _record(
        self,
        account: DestinationAccount,
        symbol: str,
        status: AuditStatus,
        detail: str,
        *,
        broker_position_quantity: float | None = None,
        stop_orders_found: int = 0,
    ) -> ProtectionAuditFinding:
        finding = ProtectionAuditFinding(
            account_id=account.account_id,
            symbol=symbol,
            broker=account.broker,
            status=status,
            detail=detail,
            broker_position_quantity=broker_position_quantity,
            stop_orders_found=stop_orders_found,
        )
        if status == AuditStatus.UNKNOWN:
            logger.error(
                "protection audit incident: account=%s symbol=%s status=UNKNOWN detail=%s",
                account.account_id,
                symbol,
                detail,
            )
            await self._halt(account, symbol, detail)
        elif status in _AT_RISK_STATUSES:
            logger.warning(
                "protection audit finding: account=%s symbol=%s status=%s detail=%s",
                account.account_id,
                symbol,
                status.value,
                detail,
            )
        self._findings[(account.account_id, symbol)] = finding
        return finding
