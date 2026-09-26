"""The copier engine: wires a Signal from any source to every configured
destination account, sized and symbol-mapped per account.

Sources and brokers know nothing about each other — this is the only place
that does.

## Close signals

A `Side.CLOSE` signal doesn't say how much to close — that depends on
what's currently open on that specific destination account, which the
account's `multiplier`/`fixed_quantity` sizing doesn't know either (closing
is about flattening a position, not scaling a trade). So the engine
resolves it here, per account, before calling the broker at all:

    1. Look up this account's tracked net position for the mapped symbol
       (`SignalStore.get_position` — this service's own record of what it
       has sent, not a live read of the broker's book).
    2. If flat (zero), there's nothing to close: report REJECTED without
       calling the broker.
    3. Otherwise resolve to the opposing BUY/SELL at the full open
       quantity, and call `place_order` with that — brokers never see
       `Side.CLOSE` from the engine; they only need to implement BUY/SELL.
       (Each broker's own `Side.CLOSE` handling, where present, is a
       defensive fallback for direct/standalone use, not something the
       engine relies on.)

## Provider/analyst settings overrides

Before sizing or routing, each destination account's own
multiplier/fixed_quantity/managed_lifecycle/enabled are narrowed by
`app/providers.py`'s `ProviderRegistry` using `signal.source` (provider)
and `signal.analyst`, if either has a `config/providers.yaml` entry — see
that module's docstring for the account -> provider -> analyst precedence.
An account with no matching provider/analyst config is completely
unaffected (this is an additive, opt-in layer, same pattern as
`managed_lifecycle` itself). `enabled=False` at any level (account,
provider, or analyst) skips that destination the same way a disabled
account already does.

Position tracking itself updates from `OrderResult.filled_quantity` when a
broker confirms FILLED, or optimistically from the requested quantity when
a broker only reports PENDING (SignalStack, Alpaca, IBKR, NinjaTrader,
Rithmic all confirm fills asynchronously, outside this call). That means
tracked positions on those brokers can drift from the real book if an
order is later rejected or partially filled after reporting PENDING — this
is a known limitation of not having a fill-confirmation feedback path from
those brokers back into this service yet.

## Managed-lifecycle accounts

An account with `DestinationAccount.managed_lifecycle = True` skips the
plain path above entirely and routes through
`app/lifecycle/manager.py`'s `PositionLifecycleManager` instead — see that
module's docstring for why (protect-the-actual-fill-first, logical
targets, one serialized close arbiter, never two independent full-position
sells racing each other). BUY/SELL signals become a `PositionPlan`
(stop_loss -> `initial_stop`, a single take_profit -> one SELL target for
the full planned quantity); an entry with no resolved stop is refused
outright (design section 11) rather than sent to the broker unprotected.
CLOSE signals are resolved against the lifecycle manager's own tracked
owned quantity (via `CloseArbiter`), not `SignalStore.get_position`, since
the arbiter is the source of truth for what's actually still open once
targets/trailing have been firing. `SignalStore` still records every fill
for `/positions` observability, same as the plain path.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, Target, TargetAction
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.providers import ProviderRegistry, SettingsOverride
from app.risk import size_for_account, symbol_for_account
from app.routing import RoutingConfig

logger = logging.getLogger(__name__)


class SignalCopierEngine:
    def __init__(
        self,
        routing: RoutingConfig,
        brokers: dict[str, BrokerAdapter],
        store: SignalStore,
        lifecycle_manager: PositionLifecycleManager | None = None,
        provider_registry: ProviderRegistry | None = None,
    ):
        self.routing = routing
        self.brokers = brokers
        self.store = store
        self.lifecycle_manager = lifecycle_manager or PositionLifecycleManager(brokers)
        self.provider_registry = provider_registry or ProviderRegistry()
        # Serializes a plain (non-managed_lifecycle) account's close resolution +
        # submission per (account_id, symbol) -- see _resolve_and_submit_plain_close.
        # managed_lifecycle accounts already get this from CloseArbiter; plain
        # accounts had no equivalent, so two near-simultaneous closes (a retried
        # request, a duplicate button click, a provider EXIT signal racing a
        # manual dashboard close) could both read the same tracked position and
        # both submit a full-quantity sell.
        self._plain_close_locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def _effective_settings(self, signal: Signal, account: DestinationAccount) -> SettingsOverride:
        account_defaults = SettingsOverride(
            multiplier=account.multiplier,
            fixed_quantity=account.fixed_quantity,
            managed_lifecycle=account.managed_lifecycle,
            enabled=account.enabled,
        )
        return self.provider_registry.effective_settings(account_defaults, signal.source, signal.analyst)

    async def handle_signal(self, signal: Signal) -> list[OrderResult]:
        # SIG-01: this exact signal id may already have been processed --
        # e.g. a caller that retries handle_signal itself after a timeout
        # without knowing whether the first attempt's orders actually went
        # through. Replay those recorded results rather than resolving
        # destinations and submitting to every broker a second time. This
        # is a distinct protection from routing.destinations_for's own
        # per-signal dedup (which stops one signal fanning out to the same
        # account twice) and from the webhook/Twilio routes' own replay
        # guards (which stop the SAME external delivery producing two
        # DIFFERENT signal ids in the first place) -- see those for what
        # each specifically covers.
        already_processed = self.store.list_orders_for_signal(signal.id)
        if already_processed:
            logger.info(
                "signal id=%s already produced %d order result(s); replaying them instead of "
                "re-submitting to every destination",
                signal.id,
                len(already_processed),
            )
            return [_order_result_from_row(row) for row in already_processed]

        self.store.save_signal(signal)

        destinations = self.routing.destinations_for(signal.source, signal.symbol)
        if not destinations:
            logger.info("no destinations configured for source=%s symbol=%s", signal.source, signal.symbol)
            return []

        results: list[OrderResult] = []
        for raw_account in destinations:
            effective = self._effective_settings(signal, raw_account)
            if effective.enabled is False:
                logger.info(
                    "account=%s disabled for source=%s analyst=%s by provider/analyst settings override",
                    raw_account.account_id,
                    signal.source,
                    signal.analyst,
                )
                continue
            # A per-(signal, account) view with provider/analyst overrides applied —
            # every downstream call reads sizing/managed_lifecycle from this, not
            # raw_account, without needing its own copy of the resolution logic.
            account = replace(
                raw_account,
                multiplier=effective.multiplier if effective.multiplier is not None else raw_account.multiplier,
                fixed_quantity=effective.fixed_quantity,
                managed_lifecycle=(
                    effective.managed_lifecycle if effective.managed_lifecycle is not None else raw_account.managed_lifecycle
                ),
            )

            broker = self.brokers.get(account.broker)
            if broker is None:
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"no broker adapter registered for '{account.broker}'",
                )
                self.store.save_order_result(result)
                results.append(result)
                continue

            if not broker.can_trade_asset_class(signal.asset_class):
                # e.g. an OPTION signal reaching AlpacaBroker/IBKRBroker (equity-only
                # in this codebase — see their module docstrings) or any non-CRYPTO
                # signal reaching ccxt. Refusing here is what makes "a source mixing
                # asset classes routes each trade to the right broker" actually true —
                # without this, the wrong-asset-class order would still be *sent*,
                # just malformed or silently misinterpreted by that broker.
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=(
                        f"broker '{account.broker}' cannot trade asset_class="
                        f"'{signal.asset_class.value}' — refusing to route this signal here"
                    ),
                )
                self.store.save_order_result(result, broker=account.broker)
                results.append(result)
                continue

            symbol = symbol_for_account(signal, account)

            if account.managed_lifecycle:
                result = await self._handle_managed_signal(signal, account, symbol)
                self.store.save_order_result(
                    result, broker=account.broker, symbol=symbol, side=signal.side, requested_quantity=None
                )
                results.append(result)
                continue

            if signal.side == Side.CLOSE:
                result = await self._resolve_and_submit_plain_close(signal, account, symbol, broker)
                results.append(result)
                continue

            order_signal, quantity = signal, size_for_account(signal, account)

            if (order_signal.stop_loss is not None or order_signal.take_profit is not None) and not broker.supports_native_bracket:
                # This account isn't managed_lifecycle, so nothing will submit a
                # standalone protective order after the fact either — sending this
                # signal's stop_loss/take_profit to a broker that can't embed it in
                # the entry means it's silently dropped and the position opens
                # unprotected (see app/brokers/*.py's supports_native_bracket=False
                # adapters, none of which read stop_loss/take_profit at all).
                # Refuse rather than admit that silently; the fix is either a
                # broker that supports it or opting the account into
                # managed_lifecycle so PositionLifecycleManager manages protection.
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=(
                        f"broker '{account.broker}' cannot embed stop_loss/take_profit into the entry "
                        "order (no native bracket) and this account is not managed_lifecycle — refusing "
                        "to submit an entry that would silently open unprotected"
                    ),
                )
                self.store.save_order_result(
                    result, broker=account.broker, symbol=symbol, side=order_signal.side, requested_quantity=quantity
                )
                results.append(result)
                continue

            try:
                result = await broker.place_order(order_signal, account, quantity, symbol)
            except Exception as exc:  # noqa: BLE001 - one account's failure must not block others
                logger.exception("order failed for account=%s", account.account_id)
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=str(exc),
                )

            applied_quantity = None
            if result.status in (OrderStatus.FILLED, OrderStatus.PENDING):
                # An explicit, reported zero fill must stay zero -- `x or
                # quantity` treats 0.0 as falsy and silently substitutes the
                # full requested quantity, exactly the "zero fill becomes a
                # fictitious full fill" bug (EXE-04). Only a genuinely
                # unknown fill (`None` -- the broker hasn't said anything
                # yet) falls back to the optimistic full-quantity guess.
                applied_quantity = quantity if result.filled_quantity is None else result.filled_quantity
                self.store.record_fill(account.account_id, symbol, order_signal.side, applied_quantity)

            self.store.save_order_result(
                result,
                broker=account.broker,
                symbol=symbol,
                side=order_signal.side,
                requested_quantity=quantity,
                applied_quantity=applied_quantity,
            )
            results.append(result)

        return results

    def _resolve_close(
        self, signal: Signal, account: DestinationAccount, symbol: str
    ) -> tuple[Signal, float] | None:
        position = self.store.get_position(account.account_id, symbol)
        if position == 0:
            return None

        closing_side = Side.SELL if position > 0 else Side.BUY
        quantity = abs(position)
        resolved_signal = Signal(
            source=signal.source,
            symbol=signal.symbol,
            side=closing_side,
            asset_class=signal.asset_class,
            quantity=quantity,
            price=signal.price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            id=signal.id,
            received_at=signal.received_at,
            raw=signal.raw,
        )
        return resolved_signal, quantity

    async def _submit_order(
        self, order_signal: Signal, quantity: float, account: DestinationAccount, symbol: str, broker: BrokerAdapter
    ) -> tuple[OrderResult, float | None]:
        """Returns (result, applied_quantity) — `applied_quantity` is what was
        actually applied to the tracked position (None if nothing was), for
        the caller to pass into `save_order_result`'s `applied_quantity` so
        the stored row matches what `record_fill` did (see that parameter's
        docstring for why the two must agree)."""
        try:
            result = await broker.place_order(order_signal, account, quantity, symbol)
        except Exception as exc:  # noqa: BLE001 - one account's failure must not block others
            logger.exception("order failed for account=%s", account.account_id)
            result = OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=order_signal.id, message=str(exc)
            )
        applied_quantity = None
        if result.status in (OrderStatus.FILLED, OrderStatus.PENDING):
            # See handle_signal's identical fix -- an explicit zero fill
            # must stay zero, never fall back to the requested quantity.
            applied_quantity = quantity if result.filled_quantity is None else result.filled_quantity
            self.store.record_fill(account.account_id, symbol, order_signal.side, applied_quantity)
        return result, applied_quantity

    async def _resolve_and_submit_plain_close(
        self, signal: Signal, account: DestinationAccount, symbol: str, broker: BrokerAdapter
    ) -> OrderResult:
        """The plain-account (non-managed_lifecycle) equivalent of
        `PositionLifecycleManager.request_exit`'s serialization: reading the
        tracked position, resolving it to an opposing order, submitting it,
        and recording the fill all happen under this (account_id, symbol)'s
        own lock, so a second close attempt arriving while the first is still
        in flight sees the position *after* the first one's fill is recorded,
        not the same stale value the first one read.

        `_plain_close_locks` alone only serializes calls within THIS engine
        instance/process (EXE-12: two independent engine instances sharing
        the same underlying database have entirely separate lock objects
        and no real exclusion between them -- a controlled reproduction
        with two engines both reading a 10-unit position and both
        submitting a 10-unit close left actual inventory at -10). The
        `SignalStore.claim_close` claim below is the real cross-process
        guard, enforced by the database itself via a UNIQUE constraint;
        the in-memory lock stays as a fast, uncontended first check within
        one process."""
        lock = self._plain_close_locks[(account.account_id, symbol)]
        async with lock:
            if not self.store.claim_close(account.account_id, symbol):
                return OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message="a close for this account/symbol is already in progress elsewhere",
                )
            try:
                resolved = self._resolve_close(signal, account, symbol)
                if resolved is None:
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message="no open position to close",
                    )
                    self.store.save_order_result(result)
                    return result

                order_signal, quantity = resolved
                result, applied_quantity = await self._submit_order(order_signal, quantity, account, symbol, broker)
                self.store.save_order_result(
                    result,
                    broker=account.broker,
                    symbol=symbol,
                    side=order_signal.side,
                    requested_quantity=quantity,
                    applied_quantity=applied_quantity,
                )
                return result
            finally:
                self.store.release_close(account.account_id, symbol)

    async def _handle_managed_signal(
        self, signal: Signal, account: DestinationAccount, symbol: str
    ) -> OrderResult:
        """Route a BUY/SELL/CLOSE signal for a `managed_lifecycle` account through
        `PositionLifecycleManager` instead of the plain broker.place_order path."""
        if signal.side == Side.CLOSE:
            return await self._handle_managed_close(signal, account, symbol)
        return await self._handle_managed_entry(signal, account, symbol)

    async def _handle_managed_entry(
        self, signal: Signal, account: DestinationAccount, symbol: str
    ) -> OrderResult:
        broker = self.brokers.get(account.broker)
        if broker is None:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=f"no broker adapter registered for '{account.broker}'",
            )

        quantity = size_for_account(signal, account)
        targets = []
        if signal.take_profit is not None:
            targets.append(Target(trigger_price=signal.take_profit, action=TargetAction.SELL, reduce_fraction=1.0))

        plan = PositionPlan(
            account_id=account.account_id,
            symbol=symbol,
            side=signal.side,
            planned_quantity=quantity,
            asset_class=signal.asset_class,
            broker=account.broker,
            initial_stop=signal.stop_loss,
            targets=targets,
        )

        error = self.lifecycle_manager.validate_plan(plan)
        if error is not None:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id, message=error
            )

        self.lifecycle_manager.start_plan(plan)

        # Entry order only — stop_loss/take_profit are deliberately DROPPED
        # here: they're managed by the lifecycle manager from here on (its
        # logical targets/trailing/protective-stop machinery), not embedded
        # in the broker order, where a native bracket would fight the
        # lifecycle manager's own protection instead of deferring to it.
        # RISK-03: price/quantity/analyst were ALSO being dropped, with no
        # such reason -- some broker adapters read signal.price directly
        # (PaperBroker's simulated fill price, NinjaTraderBroker's limit
        # price), so a managed-lifecycle entry on either always executed at
        # price 0 regardless of what the source actually specified.
        entry_signal = Signal(
            source=signal.source,
            symbol=signal.symbol,
            side=signal.side,
            asset_class=signal.asset_class,
            analyst=signal.analyst,
            quantity=signal.quantity,
            price=signal.price,
            id=signal.id,
            received_at=signal.received_at,
            raw=signal.raw,
        )

        try:
            result = await broker.place_order(entry_signal, account, quantity, symbol)
        except Exception as exc:  # noqa: BLE001 - one account's failure must not block others
            logger.exception("managed entry failed for account=%s", account.account_id)
            self.lifecycle_manager.unregister_plan(account.account_id, symbol)
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id, message=str(exc)
            )

        if result.status in (OrderStatus.ERROR, OrderStatus.REJECTED):
            # The entry never happened — don't leave a plan registered with nothing
            # protecting it (and nothing to protect).
            self.lifecycle_manager.unregister_plan(account.account_id, symbol)
        elif result.status == OrderStatus.FILLED:
            filled_quantity = result.filled_quantity if result.filled_quantity is not None else quantity
            self.store.record_fill(account.account_id, symbol, signal.side, filled_quantity)
            await self.lifecycle_manager.on_entry_fill(account, symbol, filled_quantity)
        elif result.status == OrderStatus.PENDING:
            # Don't assume the requested quantity is owned yet -- retain the
            # intent (this may already be a real, accepted order) and let
            # app/reconciliation.py's pending-entry polling call
            # `resolve_pending_entry` once the broker's final word is known,
            # which is the only thing allowed to call `on_entry_fill` (and so
            # place the protective stop) for this position. Calling
            # `record_fill` with the full requested quantity here, before
            # anything is confirmed, is exactly the optimistic-guess bug this
            # exists to avoid -- see PendingEntry's docstring.
            logger.info(
                "managed entry for account=%s symbol=%s is PENDING; retaining as an unresolved "
                "entry until the broker confirms what actually filled",
                account.account_id,
                symbol,
            )
            self.lifecycle_manager.register_pending_entry(account, symbol, result.broker_order_id, quantity)
            if result.filled_quantity is not None and result.filled_quantity > 0:
                # The initial synchronous response can itself already carry
                # a confirmed partial fill (e.g. some brokers report
                # PENDING with a non-zero filled_quantity for a still-open
                # order) -- protecting it must not wait for the next
                # reconciliation poll, which could be minutes away (EXE-08).
                await self.lifecycle_manager.resolve_pending_entry(
                    account, symbol, result.filled_quantity, remainder_cancelled=False
                )

        return result

    async def _handle_managed_close(
        self, signal: Signal, account: DestinationAccount, symbol: str, source: str = "provider_exit"
    ) -> OrderResult:
        lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
        if lifecycle is None or lifecycle.closed:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message="no open position to close",
            )

        available = self.lifecycle_manager.arbiter.available_to_sell(account.account_id, symbol)
        if available <= 0:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message="no shares available to sell",
            )

        # `request_exit` is now the single execution-application owner for this
        # fill (see PositionLifecycleManager._apply_exit_fill): it applies the
        # confirmed delta to SignalStore itself, once, whether the exit fills
        # synchronously or is later resolved via resolve_pending_exit --
        # applying an optimistic `available` guess here too was exactly the
        # "PENDING commitment recorded as a completed sale" bug this closes
        # (a partial fill followed by a cancelled remainder used to leave the
        # tracked position flat/wrong forever, since nothing ever corrected
        # this optimistic write).
        return await self.lifecycle_manager.request_exit(account, symbol, available, source=source)

    async def close_position(
        self, account: DestinationAccount, symbol: str, reason: str = "manual_exit"
    ) -> OrderResult:
        """Manually close (flatten) one destination account's position in one
        symbol, bypassing routing rules entirely — the actual work behind
        the dashboard's per-position "Exit now" button and account-level
        "Flatten account" action (see app/main.py's
        `POST /positions/{account_id}/{symbol}/close` and
        `POST /accounts/{account_id}/flatten`).

        Reuses the exact same close-resolution logic a real provider CLOSE
        signal would use — managed-lifecycle accounts go through
        `PositionLifecycleManager.request_exit` (respecting `CloseArbiter`,
        the pending-exit protection-transfer rules, everything already
        covered by `tests/test_protection_transfer.py`); plain accounts
        resolve against the tracked `SignalStore` position and submit the
        opposing BUY/SELL at the full quantity. It deliberately does NOT
        apply the entry-only gates (`can_trade_asset_class`, the
        stop_loss-embedding check) — those exist to stop *new* risk from
        being added on a route that can't protect it; they have no
        business blocking someone from *removing* existing risk.
        """
        broker = self.brokers.get(account.broker)
        if broker is None:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id="",
                message=f"no broker adapter registered for '{account.broker}'",
            )

        close_signal = Signal(source=reason, symbol=symbol, side=Side.CLOSE)

        if account.managed_lifecycle:
            result = await self._handle_managed_close(close_signal, account, symbol, source=reason)
            self.store.save_order_result(result, broker=account.broker, symbol=symbol, side=Side.CLOSE)
        else:
            # Goes through the same (account_id, symbol) lock as a provider-driven
            # CLOSE signal (see _resolve_and_submit_plain_close) -- a dashboard
            # "Exit now"/"Flatten" click can't race a concurrent provider EXIT
            # signal, or a second click, into a double-sell. This also persists
            # its own order-result row, so no separate save here.
            result = await self._resolve_and_submit_plain_close(close_signal, account, symbol, broker)
        return result


def _order_result_from_row(row: dict) -> OrderResult:
    """Reconstruct an OrderResult from an `orders` table row — used only to
    replay already-recorded results for a signal id `handle_signal` has
    already processed (see SIG-01)."""
    executed_at = row["executed_at"]
    return OrderResult(
        account_id=row["account_id"],
        status=OrderStatus(row["status"]),
        signal_id=row["signal_id"],
        broker_order_id=row["broker_order_id"],
        filled_quantity=row["filled_quantity"],
        filled_price=row["filled_price"],
        message=row["message"] or "",
        executed_at=datetime.fromisoformat(executed_at) if executed_at else datetime.now(timezone.utc),
    )
