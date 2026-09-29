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

import structlog
from signal_platform_contracts import Environment, EventEnvelope, EvidenceClass

from app import config
from app.brokers.base import BrokerAdapter
from app.capital_allocator import CapitalAllocator, confirmed_open_notional
from app.db import SignalStore
from app.export_events import (
    build_execution_applied_envelope,
    build_routing_admission_outcome_envelope,
    build_source_receipt_envelope,
)
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, Target, TargetAction
from app.logging_config import bind_signal_context
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.providers import ProviderRegistry, SettingsOverride
from app.risk import size_for_account, symbol_for_account
from app.routing import RoutingConfig

logger = logging.getLogger(__name__)
structured_logger = structlog.get_logger(__name__)

#: INT-027: the real `OrderStatus` -> `RoutingAdmissionOutcomePayload.outcome`
#: mapping -- see `app/export_events.py`'s own
#: `build_routing_admission_outcome_envelope` docstring for what each
#: outcome string means and signal_platform_contracts.payloads's own
#: `_KNOWN_ROUTING_OUTCOMES` for why "canceled"/"loss"/"commentary" (part
#: of INT-027's own requested taxonomy) are deliberately absent -- no
#: code path in this engine or its broker adapters produces either.
_OUTCOME_BY_ORDER_STATUS = {
    OrderStatus.FILLED: "admitted_filled",
    OrderStatus.PENDING: "admitted_unfilled",
    OrderStatus.REJECTED: "rejected",
    OrderStatus.ERROR: "error",
}


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
        # E03 (bounded): per-account notional-exposure admission gate — see
        # app/capital_allocator.py's module docstring for exactly what this
        # does and doesn't enforce.
        self.capital_allocator = CapitalAllocator()
        # Wired in after construction (app/lifecycle/manager.py's own
        # __init__ can't take this: main.py often constructs a
        # PositionLifecycleManager before this Engine, and so before this
        # CapitalAllocator, exists) -- lets `resolve_pending_entry` release
        # a managed entry's reservation once its outcome is confirmed
        # terminal. See PendingEntry's docstring for why.
        self.lifecycle_manager.capital_allocator = self.capital_allocator

    def _build_export_envelope(
        self,
        result: OrderResult,
        *,
        account: DestinationAccount,
        symbol: str,
        side: Side,
        asset_class: AssetClass,
        originating_source_event_id: str | None = None,
        originating_analyst_id: str | None = None,
    ) -> EventEnvelope | None:
        """Wraps app/export_events.py's builder with this deployment's own
        config (producer id, evidence class, environment) and a real,
        per-(account, source_stream) export_sequence from the outbox
        itself -- the one thing a pure builder function can't supply on
        its own. Returns `None` under the exact same conditions
        `build_execution_applied_envelope` does (not a FILLED result, or
        missing a field the payload actually requires); callers must
        still pass `export_envelope=None` to `save_order_result` in that
        case, which is exactly what omitting the keyword already does."""
        source_stream = f"signal-copier:{account.account_id}"
        return build_execution_applied_envelope(
            result,
            account=account,
            symbol=symbol,
            side=side,
            asset_class=asset_class,
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[config.RELAY_EVIDENCE_CLASS],
            environment=Environment[config.RELAY_ENVIRONMENT],
            originating_source_event_id=originating_source_event_id,
            originating_analyst_id=originating_analyst_id,
        )

    def _export_source_receipt(self, signal: Signal) -> None:
        """S12 step 5 "Portfolio Lab source feed": exports this signal as
        RECEIVED, independent of whether routing ever sends it anywhere
        -- called once per real (non-replayed) signal, right after
        `self.store.save_signal`, before routing/fan-out. Keyed on its
        own stream (`signal-copier:source:<source>`), never an
        account's own stream -- a source recommendation exists before
        any account/routing decision. `build_source_receipt_envelope`
        returns `None` for a CLOSE signal (see its own docstring); this
        is a real, checked skip, not a silent failure."""
        source_stream = f"signal-copier:source:{signal.source}"
        envelope = build_source_receipt_envelope(
            signal,
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[config.RELAY_EVIDENCE_CLASS],
            environment=Environment[config.RELAY_ENVIRONMENT],
        )
        if envelope is not None:
            self.store.append_export_event(envelope)

    def _export_routing_outcome(
        self,
        signal: Signal,
        *,
        outcome: str,
        account: DestinationAccount | None = None,
        order_status: OrderStatus | None = None,
        message: str | None = None,
    ) -> None:
        """INT-027 "All permitted source outcomes reach research": exports
        this signal's real routing/admission/fill outcome as a separate
        `ROUTING_ADMISSION_OUTCOME` event, correlated to its own
        `SOURCE_RECEIPT` (see app/export_events.py's own
        `build_routing_admission_outcome_envelope` docstring). Called
        unconditionally at every real point in `_handle_signal` where
        such an outcome becomes known -- for a signal with no
        destinations at all, for an account skipped before any order was
        attempted, and for every account that DID reach an order attempt
        (whatever its `OrderResult.status` turned out to be) -- the same
        "never miss one" property `_export_source_receipt` already has
        for the receipt itself. Lives on the SAME stream as the receipt
        (`signal-copier:source:<source>`), never an account's own stream
        -- this is about the source's own recommendation reaching its
        outcome, not a private per-account ledger. A no-op (nothing
        appended) for a `Side.CLOSE` signal -- see the builder's own
        docstring for why."""
        source_stream = f"signal-copier:source:{signal.source}"
        envelope = build_routing_admission_outcome_envelope(
            signal,
            outcome=outcome,
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[config.RELAY_EVIDENCE_CLASS],
            environment=Environment[config.RELAY_ENVIRONMENT],
            account=account,
            broker=account.broker if account is not None else None,
            order_status=order_status,
            message=message,
        )
        if envelope is not None:
            self.store.append_export_event(envelope)

    def _effective_settings(self, signal: Signal, account: DestinationAccount) -> SettingsOverride:
        account_defaults = SettingsOverride(
            multiplier=account.multiplier,
            fixed_quantity=account.fixed_quantity,
            managed_lifecycle=account.managed_lifecycle,
            enabled=account.enabled,
        )
        return self.provider_registry.effective_settings(account_defaults, signal.source, signal.analyst)

    async def handle_signal(self, signal: Signal) -> list[OrderResult]:
        """C22: binds correlation fields (signal_id, source, symbol, side)
        onto every structlog call made anywhere during this signal's
        processing (see app/logging_config.py's bind_signal_context) --
        deliberately independent of this codebase's existing plain stdlib
        `logging.getLogger(__name__)` calls, which are unaffected either
        way. The actual routing/sizing/submission logic lives in
        _handle_signal below, unchanged."""
        with bind_signal_context(
            signal_id=signal.id, source=signal.source, symbol=signal.symbol, side=signal.side.value
        ):
            structured_logger.info("signal_received", quantity=signal.quantity, analyst=signal.analyst)
            results = await self._handle_signal(signal)
            structured_logger.info(
                "signal_processed",
                destination_count=len(results),
                statuses=[r.status.value for r in results],
            )
            return results

    async def _handle_signal(self, signal: Signal) -> list[OrderResult]:
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
        self._export_source_receipt(signal)

        # EXE-10: an account's own `enabled=False` is an entry pause, not
        # an exit block -- a CLOSE signal must still reach an account that
        # already has a position open on it, even while new entries are
        # paused (see RoutingConfig.destinations_for's docstring).
        destinations = self.routing.destinations_for(
            signal.source, signal.symbol, include_disabled=signal.side == Side.CLOSE
        )
        if not destinations:
            logger.info("no destinations configured for source=%s symbol=%s", signal.source, signal.symbol)
            self._export_routing_outcome(signal, outcome="not_routed")
            return []

        results: list[OrderResult] = []
        for raw_account in destinations:
            effective = self._effective_settings(signal, raw_account)
            if effective.enabled is False and signal.side != Side.CLOSE:
                # EXE-10: same entry-pause-not-exit-block distinction as
                # destinations_for's include_disabled above, for a
                # provider/analyst-level disable rather than an
                # account-level one.
                logger.info(
                    "account=%s disabled for source=%s analyst=%s by provider/analyst settings override",
                    raw_account.account_id,
                    signal.source,
                    signal.analyst,
                )
                self._export_routing_outcome(signal, outcome="disabled_by_settings", account=raw_account)
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

            # DB-0X (order purpose/family): known from the signal itself,
            # before anything broker/lifecycle-specific has happened yet --
            # every save_order_result call in this loop iteration shares
            # this same classification (see app/db.py's SCHEMA comment on
            # `orders.purpose`/`orders.family_id` for what each value
            # means). An 'entry' order's family is simply its own
            # originating signal id. A CLOSE's real family (the entry it's
            # closing out) is only knowable for a managed_lifecycle account
            # -- see the managed branch below, which looks it up from that
            # position's own persisted `PositionPlan.entry_signal_id` and
            # overrides this default; every other CLOSE (a plain account,
            # or one of the early rejections below that never reach a
            # broker/lifecycle at all) has no real entry to attribute it
            # to, so it stays honestly `None`.
            order_purpose = "close" if signal.side == Side.CLOSE else "entry"
            order_family_id: str | None = signal.id if order_purpose == "entry" else None

            broker = self.brokers.get(account.broker)
            if broker is None:
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"no broker adapter registered for '{account.broker}'",
                )
                self.store.save_order_result(result, purpose=order_purpose, family_id=order_family_id)
                results.append(result)
                self._export_routing_outcome(
                    signal,
                    outcome=_OUTCOME_BY_ORDER_STATUS[result.status],
                    account=account,
                    order_status=result.status,
                    message=result.message,
                )
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
                self.store.save_order_result(
                    result, broker=account.broker, purpose=order_purpose, family_id=order_family_id
                )
                results.append(result)
                self._export_routing_outcome(
                    signal,
                    outcome=_OUTCOME_BY_ORDER_STATUS[result.status],
                    account=account,
                    order_status=result.status,
                    message=result.message,
                )
                continue

            symbol = symbol_for_account(signal, account)

            if account.managed_lifecycle:
                if order_purpose == "close":
                    # DB-0X: the real family this close belongs to is
                    # whatever entry signal started this same position --
                    # persisted on its PositionPlan for exactly this (see
                    # app/lifecycle/models.py's `entry_signal_id`). Looked
                    # up BEFORE `_handle_managed_signal` runs: a successful
                    # close may fully flatten and delete this lifecycle's
                    # persisted state, so it must not be read back after.
                    # No open lifecycle at all (e.g. "no open position to
                    # close") leaves this at the safe, honest default set
                    # above (None) -- there is no real family to report for
                    # a close of nothing.
                    existing_lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
                    if existing_lifecycle is not None and existing_lifecycle.plan.entry_signal_id:
                        order_family_id = existing_lifecycle.plan.entry_signal_id
                result, submitted_at, protection_confirmed_at = await self._handle_managed_signal(
                    signal, account, symbol
                )
                self.store.save_order_result(
                    result,
                    broker=account.broker,
                    symbol=symbol,
                    side=signal.side,
                    requested_quantity=None,
                    submitted_at=submitted_at,
                    protection_confirmed_at=protection_confirmed_at,
                    purpose=order_purpose,
                    family_id=order_family_id,
                )
                results.append(result)
                self._export_routing_outcome(
                    signal,
                    outcome=_OUTCOME_BY_ORDER_STATUS[result.status],
                    account=account,
                    order_status=result.status,
                    message=result.message,
                )
                continue

            if signal.side == Side.CLOSE:
                # DB-0X: a plain (non-managed_lifecycle) account has no
                # tracked lifecycle object linking this close back to
                # whichever entry fill(s) produced the position it's
                # closing -- `_resolve_and_submit_plain_close` itself saves
                # this order with purpose='close' and family_id=None (the
                # honest default already set above), not re-derived here.
                result = await self._resolve_and_submit_plain_close(signal, account, symbol, broker)
                results.append(result)
                # A CLOSE signal never gets a SOURCE_RECEIPT (see
                # build_source_receipt_envelope), so this is a real no-op
                # here -- called anyway for the same unconditional-call-site
                # shape as every other branch in this loop.
                self._export_routing_outcome(
                    signal,
                    outcome=_OUTCOME_BY_ORDER_STATUS[result.status],
                    account=account,
                    order_status=result.status,
                    message=result.message,
                )
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
                    result,
                    broker=account.broker,
                    symbol=symbol,
                    side=order_signal.side,
                    requested_quantity=quantity,
                    purpose=order_purpose,
                    family_id=order_family_id,
                )
                results.append(result)
                self._export_routing_outcome(
                    signal,
                    outcome=_OUTCOME_BY_ORDER_STATUS[result.status],
                    account=account,
                    order_status=result.status,
                    message=result.message,
                )
                continue

            admitted, notional, rejection = await self._try_reserve_capital(account, order_signal, quantity)
            if not admitted:
                assert rejection is not None  # _try_reserve_capital always sets this when admitted is False
                self.store.save_order_result(
                    rejection,
                    broker=account.broker,
                    symbol=symbol,
                    side=order_signal.side,
                    requested_quantity=quantity,
                    purpose=order_purpose,
                    family_id=order_family_id,
                )
                results.append(rejection)
                self._export_routing_outcome(
                    signal,
                    outcome=_OUTCOME_BY_ORDER_STATUS[rejection.status],
                    account=account,
                    order_status=rejection.status,
                    message=rejection.message,
                )
                continue

            # PU-A2: the real moment this engine actually calls the broker --
            # the "decision -> submission" boundary app/execution_quality.py's
            # stage breakdown reports, captured immediately before the call
            # so nothing else on this path (routing, sizing, the capital-
            # admission check above) is folded into it.
            submitted_at = datetime.now(timezone.utc)
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

            # E03 (bounded): a PENDING result with a real broker_order_id is
            # the one case app/reconciliation.py's per-pending-order loop is
            # guaranteed to eventually poll to a terminal status and release
            # this reservation for (via _correct_position) -- everything
            # else (REJECTED/ERROR/FILLED, or a PENDING with no
            # broker_order_id to ever poll) releases immediately instead,
            # since nothing else guarantees a later release and deferring
            # it would risk a reservation that's never released (see
            # app/capital_allocator.py's "Known gap").
            reserved_notional = None
            if result.status == OrderStatus.PENDING and result.broker_order_id is not None:
                reserved_notional = notional
            else:
                self.capital_allocator.release(account.account_id, notional)

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

            export_envelope = self._build_export_envelope(
                result,
                account=account,
                symbol=symbol,
                side=order_signal.side,
                asset_class=order_signal.asset_class,
                originating_source_event_id=signal.id,
                originating_analyst_id=signal.analyst,
            )
            self.store.save_order_result(
                result,
                broker=account.broker,
                symbol=symbol,
                side=order_signal.side,
                requested_quantity=quantity,
                applied_quantity=applied_quantity,
                reserved_notional=reserved_notional,
                export_envelope=export_envelope,
                submitted_at=submitted_at,
                purpose=order_purpose,
                family_id=order_family_id,
            )
            results.append(result)
            self._export_routing_outcome(
                signal,
                outcome=_OUTCOME_BY_ORDER_STATUS[result.status],
                account=account,
                order_status=result.status,
                message=result.message,
            )

        return results

    async def _try_reserve_capital(
        self, account: DestinationAccount, order_signal: Signal, quantity: float
    ) -> tuple[bool, float, OrderResult | None]:
        """E03 (bounded): admit this entry against `account.max_notional_exposure`,
        if configured. Returns (admitted, notional_reserved, rejection_or_None).
        `notional_reserved` is always the caller's responsibility to release
        via `self.capital_allocator.release(account.account_id, notional)`
        once the broker call this admission was gating has returned AND the
        result isn't a PENDING order with a real broker_order_id -- that one
        case defers the release instead, carrying `notional` forward
        (`save_order_result(reserved_notional=...)` for the plain path,
        `register_pending_entry(reserved_notional=...)` for managed_lifecycle)
        so it survives until app/reconciliation.py confirms a terminal
        status, closing the window where a PENDING order's notional counted
        toward neither this reservation nor confirmed exposure. See both
        callers and app/capital_allocator.py's "Known gap" section for why
        every OTHER outcome still releases immediately (0.0 is a safe no-op
        release when nothing was actually reserved, i.e. the check was
        skipped)."""
        if account.max_notional_exposure is None or order_signal.price is None:
            return True, 0.0, None
        notional = abs(quantity) * order_signal.price
        confirmed = confirmed_open_notional(self.store, account.account_id)
        admitted = await self.capital_allocator.admit(
            account.account_id, notional, confirmed_exposure=confirmed, max_exposure=account.max_notional_exposure
        )
        if not admitted:
            return (
                False,
                notional,
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=order_signal.id,
                    message=(
                        f"account '{account.account_id}' notional exposure ceiling "
                        f"({account.max_notional_exposure}) would be exceeded by this entry "
                        f"(confirmed={confirmed:.2f}, requested={notional:.2f}) -- refusing"
                    ),
                ),
            )
        return True, notional, None

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
    ) -> tuple[OrderResult, float | None, datetime]:
        """Returns (result, applied_quantity, submitted_at) — `applied_quantity`
        is what was actually applied to the tracked position (None if nothing
        was), for the caller to pass into `save_order_result`'s
        `applied_quantity` so the stored row matches what `record_fill` did
        (see that parameter's docstring for why the two must agree).
        `submitted_at` (PU-A2) is the real moment this call actually reached
        the broker, captured immediately before it -- see handle_signal's
        identical field for what it feeds into."""
        submitted_at = datetime.now(timezone.utc)
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
        return result, applied_quantity, submitted_at

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
                    # DB-0X: a plain account's close always reports
                    # purpose='close'; family_id stays None -- there's no
                    # tracked lifecycle to attribute it to (see this
                    # method's own docstring / app/db.py's SCHEMA comment).
                    self.store.save_order_result(result, purpose="close", family_id=None)
                    return result

                order_signal, quantity = resolved
                result, applied_quantity, submitted_at = await self._submit_order(
                    order_signal, quantity, account, symbol, broker
                )
                self.store.save_order_result(
                    result,
                    broker=account.broker,
                    symbol=symbol,
                    side=order_signal.side,
                    requested_quantity=quantity,
                    applied_quantity=applied_quantity,
                    submitted_at=submitted_at,
                    purpose="close",
                    family_id=None,
                )
                return result
            finally:
                self.store.release_close(account.account_id, symbol)

    async def _handle_managed_signal(
        self, signal: Signal, account: DestinationAccount, symbol: str
    ) -> tuple[OrderResult, datetime | None, datetime | None]:
        """Route a BUY/SELL/CLOSE signal for a `managed_lifecycle` account through
        `PositionLifecycleManager` instead of the plain broker.place_order path.

        Returns (result, submitted_at, protection_confirmed_at) -- the latter
        two are PU-A2's execution-quality timestamps, both None for a CLOSE
        (an exit, not an entry: nothing here submits a fresh protective stop
        for it, and app/execution_quality.py's protection stage is entry-only
        -- see _handle_managed_close)."""
        if signal.side == Side.CLOSE:
            return await self._handle_managed_close(signal, account, symbol)
        return await self._handle_managed_entry(signal, account, symbol)

    async def _handle_managed_entry(
        self, signal: Signal, account: DestinationAccount, symbol: str
    ) -> tuple[OrderResult, datetime | None, datetime | None]:
        broker = self.brokers.get(account.broker)
        if broker is None:
            return (
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"no broker adapter registered for '{account.broker}'",
                ),
                None,
                None,
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
            # DB-0X: this position's own real entry signal id, carried for
            # its whole lifetime so a later CLOSE for this same
            # (account_id, symbol) can report the same `orders.family_id`
            # -- see PositionPlan.entry_signal_id's own docstring.
            entry_signal_id=signal.id,
        )

        error = self.lifecycle_manager.validate_plan(plan)
        if error is not None:
            return (
                OrderResult(
                    account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id, message=error
                ),
                None,
                None,
            )

        admitted, notional, rejection = await self._try_reserve_capital(account, signal, quantity)
        if not admitted:
            assert rejection is not None  # _try_reserve_capital always sets this when admitted is False
            return rejection, None, None

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

        # PU-A2: submission moment for this managed entry -- see
        # handle_signal's identical field for what it feeds into.
        submitted_at = datetime.now(timezone.utc)
        try:
            result = await broker.place_order(entry_signal, account, quantity, symbol)
        except Exception as exc:  # noqa: BLE001 - one account's failure must not block others
            # EXE-01: `place_order` raising here is genuinely ambiguous — the
            # broker adapter may have already sent the request and gotten it
            # accepted at the venue before the exception happened (a network
            # timeout/connection reset reading the response, unlike a
            # RuntimeError from `_credentials_for` that never sent anything).
            # Assuming "never happened" and calling `unregister_plan` (the
            # old behavior) would delete the one durable record that could
            # ever let a restart's reconciliation notice a real position it
            # doesn't know about. Keep the plan's persisted row and mark it
            # as an unresolved pending entry with no known broker_order_id —
            # `app/reconciliation.py`'s broker-position readback (OPS-03)
            # is what can eventually discover whether this actually filled.
            logger.exception(
                "managed entry response lost for account=%s symbol=%s -- retaining as an "
                "unresolved pending entry (may already be a real, accepted order)",
                account.account_id,
                symbol,
            )
            # E03 (bounded): release now, not defer -- a broker_order_id=None
            # entry is never polled by app/reconciliation.py's
            # _reconcile_pending_entries (it has nothing to poll), so
            # nothing guarantees resolve_pending_entry is ever called for
            # this one. Deferring here risks a reservation that's never
            # released -- see app/capital_allocator.py's "Known gap".
            self.capital_allocator.release(account.account_id, notional)
            self.lifecycle_manager.register_pending_entry(account, symbol, None, quantity)
            return (
                OrderResult(
                    account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id, message=str(exc)
                ),
                submitted_at,
                None,
            )

        protection_confirmed_at: datetime | None = None
        if result.status == OrderStatus.REJECTED:
            # A broker-confirmed rejection (or a client-side validation
            # failure that never reached the network) is the one case that
            # definitely never happened -- nothing to protect, so nothing
            # to keep registered.
            self.capital_allocator.release(account.account_id, notional)
            self.lifecycle_manager.unregister_plan(account.account_id, symbol)
        elif result.status == OrderStatus.ERROR:
            # Same ambiguity as the raised-exception branch above, just
            # returned instead of raised: several adapters (e.g. AlpacaBroker)
            # catch their own transport/timeout errors and return ERROR
            # rather than letting the exception propagate, so a request that
            # actually reached the venue before the timeout looks identical,
            # from here, to one that never left this process. Treating
            # every ERROR as "definitely never happened" and unregistering
            # the plan would silently stop protecting a position that may
            # already be real. Retain it the same way, for the same
            # reason: app/reconciliation.py's broker-position readback
            # (OPS-03) is what can eventually discover whether this filled.
            logger.error(
                "managed entry for account=%s symbol=%s returned ERROR (not raised) -- retaining as an "
                "unresolved pending entry (may already be a real, accepted order): %s",
                account.account_id,
                symbol,
                result.message,
            )
            # E03 (bounded): same "release now, not defer" reasoning as the
            # raised-exception branch above -- an ERROR result is never
            # polled by _reconcile_pending_entries either.
            self.capital_allocator.release(account.account_id, notional)
            self.lifecycle_manager.register_pending_entry(account, symbol, None, quantity)
        elif result.status == OrderStatus.FILLED:
            # Confirmed exposure now includes this fill, so the provisional
            # reservation's job is done.
            self.capital_allocator.release(account.account_id, notional)
            filled_quantity = result.filled_quantity if result.filled_quantity is not None else quantity
            self.store.record_fill(account.account_id, symbol, signal.side, filled_quantity)
            # PU-A1: `result.filled_price` is the real confirmed fill price for
            # this entry — seeds PositionLifecycle's MAE/MFE tracking. None
            # when the broker's FILLED response didn't report one; that stays
            # honestly unknown rather than assumed (see on_entry_fill's
            # docstring).
            lifecycle_after_fill = await self.lifecycle_manager.on_entry_fill(
                account, symbol, filled_quantity, entry_price=result.filled_price
            )
            # PU-A2: real only if the broker actually confirmed the stop
            # resting synchronously within on_entry_fill above -- None
            # (never fabricated) if it didn't (no verified
            # place_protective_stop for this broker, or the submission
            # failed/was rejected -- see StopRecord.confirmed_at).
            protection_confirmed_at = lifecycle_after_fill.stop.confirmed_at
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
            # E03 (bounded): this is the one case that actually closes the
            # reservation-timing gap -- a real broker_order_id means
            # _reconcile_pending_entries is guaranteed to eventually poll
            # this and call resolve_pending_entry, which releases the
            # reservation then instead of now. No broker_order_id at all is
            # the same "nothing will ever revisit this" situation as the
            # ERROR/exception branches above, so it releases immediately.
            if result.broker_order_id is not None:
                self.lifecycle_manager.register_pending_entry(
                    account, symbol, result.broker_order_id, quantity, reserved_notional=notional
                )
            else:
                self.capital_allocator.release(account.account_id, notional)
                self.lifecycle_manager.register_pending_entry(account, symbol, None, quantity)
            if result.filled_quantity is not None and result.filled_quantity > 0:
                # The initial synchronous response can itself already carry
                # a confirmed partial fill (e.g. some brokers report
                # PENDING with a non-zero filled_quantity for a still-open
                # order) -- protecting it must not wait for the next
                # reconciliation poll, which could be minutes away (EXE-08).
                await self.lifecycle_manager.resolve_pending_entry(
                    account, symbol, result.filled_quantity, remainder_cancelled=False
                )
                # PU-A2: resolve_pending_entry may have placed/confirmed the
                # protective stop synchronously (same reasoning as the
                # FILLED branch above) -- re-read the lifecycle rather than
                # assume, since resolve_pending_entry doesn't return one.
                lifecycle_now = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
                if lifecycle_now is not None:
                    protection_confirmed_at = lifecycle_now.stop.confirmed_at

        return result, submitted_at, protection_confirmed_at

    async def _handle_managed_close(
        self, signal: Signal, account: DestinationAccount, symbol: str, source: str = "provider_exit"
    ) -> tuple[OrderResult, datetime | None, datetime | None]:
        """Returns (result, submitted_at, protection_confirmed_at) like
        `_handle_managed_entry`, for the same call-site shape -- but a CLOSE
        is an exit, not an entry: nothing here confirms a fresh protective
        stop for it, so `protection_confirmed_at` is always None (PU-A2's
        protection stage is entry-only in this codebase today). `submitted_at`
        is also None here -- unlike the plain-account close path (see
        `_submit_order`), `request_exit`'s own submission call is inside
        app/lifecycle/manager.py, not this method, so there is no real
        submission instant available at this call site to report honestly."""
        lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
        if lifecycle is None or lifecycle.closed:
            return (
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message="no open position to close",
                ),
                None,
                None,
            )

        available = self.lifecycle_manager.arbiter.available_to_sell(account.account_id, symbol)
        if available <= 0:
            return (
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message="no shares available to sell",
                ),
                None,
                None,
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
        result = await self.lifecycle_manager.request_exit(account, symbol, available, source=source)
        return result, None, None

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
        # DB-01: every order row's signal_id is a real foreign key into
        # `signals` -- this manual close path is the one place that used to
        # build a Signal and hand its id straight to save_order_result
        # without ever persisting the signal itself, an orphaned reference
        # that only worked because foreign key enforcement was off.
        self.store.save_signal(close_signal)

        if account.managed_lifecycle:
            # E06: the resolved opposing side (BUY/SELL), not Side.CLOSE --
            # save_order_result's own docstring says "for a resolved close,
            # side is the opposing buy/sell, not Side.CLOSE" (the plain-
            # account path below already honors this), but this branch
            # used to save Side.CLOSE regardless, making it impossible to
            # tell a managed close's actual trade direction from the
            # `orders` table alone -- exactly what a P&L/execution-journal
            # report needs to reconstruct realized gains correctly.
            lifecycle_before_close = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
            resolved_side = lifecycle_before_close.exit_side if lifecycle_before_close is not None else Side.CLOSE

            result, _submitted_at, _protection_confirmed_at = await self._handle_managed_close(
                close_signal, account, symbol, source=reason
            )
            # DB-01: PositionLifecycleManager.request_exit (which this
            # ultimately calls into) reports some outcomes with
            # signal_id="" (it has no Signal of its own there, only a
            # source/reason string) and others with a throwaway internal
            # Signal's id (_submit_exit_order's own exit_signal, which is
            # only guaranteed saved when the manager has a store wired in --
            # not true of every construction, e.g. in tests). Either way,
            # the semantically correct identity for this order, from this
            # call's own perspective, is close_signal (just persisted
            # above) -- always attribute the row to it rather than trust
            # whatever id happened to come back from deeper in the call.
            result = replace(result, signal_id=close_signal.id)
            # DB-0X: real family link back to this position's own entry
            # (see app/lifecycle/models.py's `PositionPlan.entry_signal_id`)
            # -- already fetched above as `lifecycle_before_close`. None
            # (never fabricated) when there's no lifecycle to read it from.
            family_id = (
                lifecycle_before_close.plan.entry_signal_id
                if lifecycle_before_close is not None and lifecycle_before_close.plan.entry_signal_id
                else None
            )
            self.store.save_order_result(
                result, broker=account.broker, symbol=symbol, side=resolved_side, purpose="close", family_id=family_id
            )
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
