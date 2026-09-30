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
    3. P0-5: before resolving/submitting anything further, reconcile that
       tracked quantity against reality -- see
       `_reconcile_before_plain_close`'s own docstring for the full
       contract. In short: a plain account's own tracked position can go
       stale after manual intervention, an external fill placed directly
       at the broker, a corporate action, or ordinary reconciliation lag,
       so a plain CLOSE additionally requires EITHER (a) a fresh
       `BrokerAdapter.get_broker_position` readback that matches the
       tracked quantity within tolerance, or (b) the account's own
       explicit, off-by-default `DestinationAccount.exclusive_writer_qualified`
       flag. Neither holding is a REJECTED result, not a close that
       proceeds anyway against a possibly-stale local projection.
    4. Otherwise resolve to the opposing BUY/SELL at the full open
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

## Position tracking's distinct-field quantity model (AUD-01)

`SignalStore.positions.net_quantity` (`actual_remaining_ownership` by
contract — see that table's own SCHEMA comment in app/db.py) is updated
ONLY from a broker-CONFIRMED fill: a synchronous FILLED result, or a real,
broker-reported `filled_quantity` accompanying a PENDING result (a genuine
partial-fill progress report). It is NEVER updated optimistically from the
merely-requested quantity while a broker's order is still PENDING with
nothing confirmed yet (SignalStack, Alpaca, IBKR, NinjaTrader, Rithmic all
confirm fills asynchronously, outside this call, via
app/reconciliation.py's polling loop) — doing so used to let tracked
positions silently diverge from the real book whenever such an order was
later rejected or only partially filled, which is not a tolerable basis
for live holdings, risk, P&L, or a subsequent close in this system.

Every order/fill event distinguishes five fields, persisted on the
`orders` row (see app/db.py's SCHEMA comment for the exact column
contract) and surfaced in aggregate via `SignalStore
.get_outstanding_possible_fill`/`PositionLifecycleManager
.get_outstanding_possible_fill`:

- `requested_quantity` — what was asked for.
- `confirmed_cumulative_fill` — what the broker has actually confirmed
  filled so far for this order, exactly as reported (never guessed).
- `applied_execution_delta` — the position-impacting change this specific
  fill event actually applied to `positions.net_quantity` (0.0, not a
  fabricated guess, when nothing was confirmed yet).
- `outstanding_possible_fill` — `requested_quantity -
  confirmed_cumulative_fill` while the order remains PENDING: genuine
  uncertain exposure that must be tracked and surfaced (e.g. to
  app/capital_allocator.py, or a position-detail UI), never silently
  treated as already-owned and never silently treated as zero.
- `actual_remaining_ownership` — `positions.net_quantity` itself: the
  current believed-owned quantity, updated only from confirmed fills/exits.

A PENDING order whose broker later reports REJECTED or a lower final fill
than requested therefore never needed "correcting" from a wrong optimistic
baseline in the first place — `OrderReconciler._correct_position` still
applies the (now correctly zero-or-partial) baseline-to-confirmed delta,
but that delta is the FULL confirmed amount, not a true-up of an inflated
guess.

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
import math
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone

import structlog
from signal_platform_contracts import Environment, EventEnvelope, EvidenceClass

from app import command_ledger, config
from app import quantity as quantity_module
from app import signal_freshness
from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker
from app.capital_allocator import CapitalAllocator, confirmed_open_notional, owner_wide_exposure
from app.db import SignalStore
from app.export_events import (
build_execution_applied_envelope,
    build_routing_admission_outcome_envelope,
    build_source_event_envelope,
    build_source_receipt_envelope,
)
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, Target, TargetAction
from app.logging_config import bind_signal_context
from app.models import (
    AssetClass,
    CommandType,
    DestinationAccount,
    OrderResult,
    OrderStatus,
    Side,
    Signal,
    SourceEvent,
    UncertaintyState,
)
from app.providers import ProviderRegistry, SettingsOverride
from app.risk import size_for_account, symbol_for_account
from app.routing import RoutingConfig
from app.shadow_mode import evaluate_shadow, to_result_row
from app.writer_lease import NullLeaseGuard, WriterLeaseGuard

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

#: P0-5: tolerance for comparing a broker's live position readback against
#: this service's own locally tracked quantity before a plain close is
#: allowed to proceed -- see `_positions_reconcile`. A pure `==` would
#: reject on ordinary floating-point noise (accumulated fills/partial
#: fills/fx-converted quantities); this is deliberately small (never
#: large enough to paper over a real, material mismatch) and combines an
#: absolute floor with a relative term so it scales sanely for both a
#: fractional crypto position and a large equity/futures one.
_RECONCILIATION_ABS_TOLERANCE = 1e-6
_RECONCILIATION_REL_TOLERANCE = 1e-6

#: Track 1b live-routing gate (audit finding: "the qualification ladder
#: never enforced as a live-routing gate" -- see app/qualification.py's
#: module docstring for the full 4-tuple this ladder is tracked per).
#: `DestinationAccount` has no dedicated field distinguishing multiple
#: distinct trading PRODUCTS sharing one account/route (e.g. a CCXT spot
#: market vs a CCXT perpetual market both trading through the same
#: account_id) -- every account/route configured in this codebase today
#: gets its own distinct `account_id` per product instead (see
#: tests/test_route_qualification.py's own "ccxt_binance_spot" vs
#: "ccxt_binance_perp" fixture, which are two different route_keys, not
#: one route_key with two product_types). So `route_key` below
#: (`account.account_id`) already carries the real per-product
#: distinction this codebase actually uses, and `product_type` is a
#: fixed, honestly-labeled placeholder -- never fabricated per-account
#: specificity that doesn't exist in this schema. FLAGGED FINDING: if an
#: operator ever needs two distinct products sharing one account_id, this
#: schema (and this constant) will need a real per-account product_type
#: field added; nothing here can honestly support that today.
_UNDECLARED_ROUTE_PRODUCT_TYPE = "default"


def _positions_reconcile(broker_position: float, local_position: float) -> bool:
    tolerance = _RECONCILIATION_ABS_TOLERANCE + _RECONCILIATION_REL_TOLERANCE * max(
        abs(broker_position), abs(local_position)
    )
    return abs(broker_position - local_position) <= tolerance


class SignalCopierEngine:
    def __init__(
        self,
        routing: RoutingConfig,
        brokers: dict[str, BrokerAdapter],
        store: SignalStore,
        lifecycle_manager: PositionLifecycleManager | None = None,
        provider_registry: ProviderRegistry | None = None,
        lease_guard: WriterLeaseGuard | NullLeaseGuard | None = None,
    ):
        self.routing = routing
        self.brokers = brokers
        self.store = store
        self.lifecycle_manager = lifecycle_manager or PositionLifecycleManager(brokers)
        self.provider_registry = provider_registry or ProviderRegistry()
        # Cross-process/cross-host single-writer fencing (see
        # app/writer_lease.py, docs/FAILOVER.md): checked at the top of
        # every command-execution entry point below (`handle_signal`,
        # `close_position`) before any broker call can be reached, and
        # also passed through to `self.lifecycle_manager` so its own
        # entry points (on_price_update/request_exit/etc., which can be
        # driven by background loops, not just a signal/close call) are
        # covered too. Defaults to a no-op guard so every existing
        # construction (tests, ad-hoc scripts) that doesn't wire real
        # fencing is unaffected -- app/main.py is the one place that
        # constructs a real WriterLeaseGuard, for the app's own live
        # ACTIVE process.
        self.lease_guard = lease_guard or NullLeaseGuard()
        self.lifecycle_manager.lease_guard = self.lease_guard
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
        # does and doesn't enforce. P0-4: `store=store` makes its
        # reservations durable and reloads any left unresolved by a
        # previous process against this same database -- see that
        # module's own docstring.
        self.capital_allocator = CapitalAllocator(store=store)
        # E03 (owner-wide exposure): opt-in global ceiling, see
        # app/config.py's MAX_OWNER_NOTIONAL_EXPOSURE and
        # app/capital_allocator.py's `owner_wide_exposure`. Read once at
        # construction (same pattern every other config.py value this
        # engine depends on already uses) -- None (the default) means the
        # owner-wide gate is never evaluated, no change from before this
        # existed.
        self.max_owner_notional_exposure: float | None = config.MAX_OWNER_NOTIONAL_EXPOSURE
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

    async def export_source_event(self, event: SourceEvent) -> None:
        """The public counterpart to `_export_source_receipt`, matching
        `app.sources.base.SourceEventHandler`'s own shape so it can be
        wired directly as a `SourceAdapter`'s `on_source_event` (see
        app/main.py) -- appends one `SOURCE_EVENT` ledger row per
        ORIGINAL/EDIT/DELETE/... an adapter recognizes. Async (unlike the
        sync `_export_source_receipt`) because `SourceEventHandler` is;
        unlike `build_source_receipt_envelope`,
        `build_source_event_envelope` never returns `None`, so this
        always appends."""
        source_stream = f"signal-copier:source:{event.source}"
        envelope = build_source_event_envelope(
            event,
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[config.RELAY_EVIDENCE_CLASS],
            environment=Environment[config.RELAY_ENVIRONMENT],
        )
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
        # Cross-process/cross-host fencing (app/writer_lease.py): checked
        # before anything else in this method, including the SIG-01
        # replay-lookup below -- a process that's been fenced out must
        # refuse the whole call, not just the eventual broker.place_order.
        # Raises FencedOutError (uncaught here, deliberately) rather than
        # returning a REJECTED result -- this is a "this process must
        # stop acting as writer" condition, not an ordinary per-signal
        # rejection a caller should retry.
        self.lease_guard.require_active()
        # Track 5, point 6 (cross-collector/cross-transport redelivery
        # dedup): canonicalize this signal's id onto whatever earlier
        # signal already shares its real provider identity
        # (channel_id/message_id/revision_id) -- e.g. a bot collector and
        # a user-account collector both configured against the same
        # Telegram channel each parsing the SAME message into their own
        # fresh Signal.id, or one collector redelivering on reconnect.
        # Reassigning `signal.id` here (BEFORE the SIG-01 lookup right
        # below) means that existing, unchanged per-signal-id replay
        # mechanism now also catches this cross-collector case for free:
        # `save_signal`'s `INSERT OR REPLACE` on the now-shared id is
        # idempotent (no duplicate `signals` row), and a second delivery
        # sharing the id finds `list_orders_for_signal` non-empty and
        # replays instead of submitting a second live order. A `None`
        # channel_id/message_id (every adapter this task didn't touch) is
        # a no-op here -- see `find_signal_id_by_provider_identity`'s own
        # docstring.
        existing_signal_id = self.store.find_signal_id_by_provider_identity(
            channel_id=signal.channel_id, message_id=signal.message_id, revision_id=signal.revision_id
        )
        if existing_signal_id is not None:
            signal.id = existing_signal_id
        elif config.SIGNAL_CORRELATION_ENABLED:
            # Track 12: exact WITHIN-transport identity found nothing new
            # (either this is genuinely new, or it's the same alert
            # arriving via a DIFFERENT transport) -- try cross-transport
            # fingerprint correlation. See `_correlate_cross_transport`'s
            # own docstring for the full guard (only signals that already
            # carry real provider identity ever reach this at all) and
            # `app/signal_correlation.py`'s own module docstring for why
            # this composes with, and never weakens, the exact-identity
            # dedup just above.
            conflict_result = await self._correlate_cross_transport(signal)
            if conflict_result is not None:
                return conflict_result
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
        # Track 17: SHADOW MODE -- "what would the live system have
        # done?" for a provider not yet certified live. Purely additive
        # and non-blocking: computed from the SAME routing/sizing logic
        # (see app/shadow_mode.py's module docstring) as the real
        # destination-resolution below, but never gates, delays, or
        # replaces it -- a provider's live routing continues exactly as
        # it already does today (app.provider_catalog.
        # ExecutionEligibility is still not wired into this gate, per
        # Track 14's own documented scoping). Wrapped so a failure here
        # (a malformed provider row, a store error) can never break real
        # signal processing -- see this method's own call site comment.
        self._maybe_run_shadow_mode(signal)

        # Track 16: `decision_at` -- this engine is about to make its
        # routing/admission decision for this signal (freshness gating
        # immediately below, then per-destination-account processing) --
        # recorded so `get_signal_lifecycle` can report it, whichever way
        # that decision goes. A no-op cost (one more UPDATE) for every
        # signal, whether or not it has a registered provider.
        signal.decision_at = datetime.now(timezone.utc)
        self.store.save_signal(signal)

        # Track 16: per-provider/per-source freshness -- see
        # `_check_signal_freshness`'s own docstring for the full contract.
        # Strict superset: a signal whose `source` names no registered
        # `providers` row (every signal this codebase produced before
        # Track 14, and most of its adapters/fixtures today) resolves to
        # `FreshnessConfig.disabled()`, which always returns `ok=True` --
        # this call is then a real no-op, identical to before this
        # existed.
        stale_result = self._check_signal_freshness(signal)
        if stale_result is not None:
            return stale_result

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

            if signal.side != Side.CLOSE:
                # Track 1b: refuse a live ENTRY before anything else broker/
                # asset-class-specific is even checked -- see
                # `_check_route_qualified`'s own docstring for exactly what
                # this gates, why CLOSE is exempt, and why PAPER accounts
                # are exempt.
                route_qualified, qualification_rejection = self._check_route_qualified(account, signal, broker)
                if not route_qualified:
                    assert qualification_rejection is not None
                    self.store.save_order_result(
                        qualification_rejection,
                        broker=account.broker,
                        purpose=order_purpose,
                        family_id=order_family_id,
                    )
                    results.append(qualification_rejection)
                    self._export_routing_outcome(
                        signal,
                        outcome=_OUTCOME_BY_ORDER_STATUS[qualification_rejection.status],
                        account=account,
                        order_status=qualification_rejection.status,
                        message=qualification_rejection.message,
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

            # P0-2: pre-effect durable command-ledger intent, written and
            # COMMITTED before the broker is ever called -- see
            # app/command_ledger.py's module docstring. `signal.id` is
            # this entry's own natural retry identity (the same value
            # `order_family_id` already uses above); a second call for the
            # exact same signal/account/quantity/symbol replays this row's
            # tracked outcome instead of submitting a second broker order.
            ledger_key = f"entry:{account.account_id}:{symbol}:{signal.id}"
            ledger_fingerprint = command_ledger.compute_fingerprint(
                {
                    "command_type": "entry",
                    "account_id": account.account_id,
                    "symbol": symbol,
                    "side": order_signal.side.value,
                    "quantity": quantity,
                    "signal_id": signal.id,
                }
            )
            ledger_entry = self.store.open_command_ledger_entry(
                idempotency_key=ledger_key,
                command_type=CommandType.ENTRY,
                account_id=account.account_id,
                environment=command_ledger.current_environment(),
                request_fingerprint=ledger_fingerprint,
            )
            if command_ledger.is_duplicate_submission(ledger_entry.uncertainty_state):
                # A prior attempt under this exact key already ran (or is
                # running) -- never submit a second broker order for it.
                # The broker was already released this reservation's fate
                # one way or another on that first attempt, so release here
                # too rather than double-reserve.
                self.capital_allocator.release(account.account_id, notional)
                logger.info(
                    "duplicate entry command idempotency_key=%s account=%s symbol=%s -- replaying "
                    "tracked state=%s instead of resubmitting",
                    ledger_key,
                    account.account_id,
                    symbol,
                    ledger_entry.uncertainty_state.value,
                )
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.PENDING,
                    signal_id=signal.id,
                    broker_order_id=ledger_entry.remote_identifiers.get("broker_order_id"),
                    message=(
                        f"duplicate command (idempotency_key={ledger_key}); tracked state="
                        f"{ledger_entry.uncertainty_state.value}, not resubmitted"
                    ),
                )
                results.append(result)
                self._export_routing_outcome(
                    signal, outcome="duplicate_command", account=account, order_status=result.status, message=result.message
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
                self.store.mark_command_ledger_outcome(
                    ledger_key,
                    uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                    terminal_evidence=command_ledger.ambiguous_evidence_for_exception(exc),
                )
            else:
                outcome_state, outcome_remote, outcome_evidence = command_ledger.classify_order_result(result)
                self.store.mark_command_ledger_outcome(
                    ledger_key,
                    uncertainty_state=outcome_state,
                    remote_identifiers=outcome_remote,
                    terminal_evidence=outcome_evidence,
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
            reserved_quantity = None
            if result.status == OrderStatus.PENDING and result.broker_order_id is not None:
                reserved_notional = notional
                # TRK-Q1: the same reservation, in units instead of notional
                # -- see app/models.py's `QuantityBreakdown.reserved_quantity`
                # and this column's own SCHEMA comment in app/db.py. Follows
                # the identical broker_order_id-gated rule as
                # `reserved_notional` immediately above -- `0.0` (not the
                # full `quantity`), same as `notional` itself, whenever no
                # gate was actually configured for this account/owner (see
                # `_try_reserve_capital`'s `has_gate` branch), never a
                # fabricated full-quantity reservation for an order nothing
                # actually reserved capital against.
                reserved_quantity = quantity if notional else 0.0
            else:
                self.capital_allocator.release(account.account_id, notional)

            # TRK-Q1: how much of `quantity` the broker has actually
            # ACCEPTED an order for -- see app/quantity.py's
            # `acknowledged_quantity_for` for the exact rule (distinct from
            # how much has executed, computed just below).
            acknowledged_quantity = quantity_module.acknowledged_quantity_for(result, quantity)

            # AUD-01: `applied_quantity`/`confirmed_cumulative_fill`/
            # `applied_execution_delta`/`outstanding_possible_fill` --
            # see this module's own docstring section on the distinct-
            # field quantity model, and app/db.py's `save_order_result`
            # docstring for exactly what each of these four means.
            #
            # A FILLED result is a broker-confirmed synchronous fill --
            # `result.filled_quantity is None` there is a broker that
            # confirmed FILLED without reporting a quantity, so the
            # requested quantity is the only real number available (not
            # an optimistic guess: the broker DID confirm this happened).
            #
            # A PENDING result confirms NOTHING by itself. Only a real,
            # broker-reported `filled_quantity` alongside PENDING (a
            # genuine partial-fill progress report, EXE-04: explicit 0.0
            # is real progress too, not "unknown") is confirmed and
            # applied. `filled_quantity is None` on a PENDING result means
            # the broker hasn't said anything yet -- `actual_remaining_
            # ownership` must NOT change for it, full stop; the full
            # requested quantity is tracked ONLY as `outstanding_possible_
            # fill`, genuine uncertain exposure, never applied to the
            # position.
            applied_quantity: float | None = None
            confirmed_cumulative_fill: float | None = None
            outstanding_possible_fill = 0.0
            if result.status == OrderStatus.FILLED:
                applied_quantity = quantity if result.filled_quantity is None else result.filled_quantity
                confirmed_cumulative_fill = applied_quantity
            elif result.status == OrderStatus.PENDING:
                confirmed_cumulative_fill = result.filled_quantity
                if result.filled_quantity is not None:
                    applied_quantity = result.filled_quantity
                outstanding_possible_fill = quantity - (confirmed_cumulative_fill or 0.0)
            if applied_quantity is not None:
                self.store.record_fill(account.account_id, symbol, order_signal.side, applied_quantity)
            applied_execution_delta = applied_quantity if applied_quantity is not None else 0.0

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
                confirmed_cumulative_fill=confirmed_cumulative_fill,
                applied_execution_delta=applied_execution_delta,
                outstanding_possible_fill=outstanding_possible_fill,
                reserved_notional=reserved_notional,
                reserved_quantity=reserved_quantity,
                acknowledged_quantity=acknowledged_quantity,
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

    #: Sentinel `OrderResult.account_id` for a signal held out of live
    #: routing entirely by Track 12's cross-transport correlation
    #: (`_correlate_cross_transport`, CONFLICTING_SOURCE_DATA) -- there is
    #: no real destination account this rejection is "for" (it's rejected
    #: BEFORE `routing.destinations_for` is ever consulted), so this is a
    #: deliberately unmistakable, never-a-real-account-id marker, same
    #: "honest placeholder, not a fabricated real value" spirit as this
    #: engine's other sentinels.
    _CROSS_TRANSPORT_CONFLICT_ACCOUNT_ID = "__cross_transport_conflict__"

    #: Track 16 -- same "honest, never-a-real-account-id" sentinel
    #: convention as `_CROSS_TRANSPORT_CONFLICT_ACCOUNT_ID`, for a signal
    #: `_check_signal_freshness` holds/rejects out of live routing
    #: entirely before `routing.destinations_for` is ever consulted.
    _STALE_SIGNAL_ACCOUNT_ID = "__stale_signal_held__"
    #: Track 16 -- same convention, for a signal
    #: `ConflictResolutionPolicy.REQUIRE_MATCHING_SOURCES` holds pending a
    #: second, independent, corroborating source (see
    #: `_correlate_cross_transport`'s own wiring note).
    _INSUFFICIENT_CORROBORATION_ACCOUNT_ID = "__insufficient_corroboration__"

    def _check_signal_freshness(self, signal: Signal) -> list[OrderResult] | None:
        """Track 16: evaluates `signal` against its effective
        `app.signal_freshness.FreshnessConfig` (resolved from this
        signal's `source`'s `providers`/`sources` catalog rows, when one
        exists -- see `app/signal_freshness.py`'s own module docstring).

        Returns `None` -- "nothing to do, proceed exactly as before" --
        for every signal whose provider isn't registered at all (the
        strict-superset case), and for a genuinely fresh signal, and for
        a stale one whose configured behavior is `ACT_ANYWAY`.

        Returns a REAL final `list[OrderResult]` (a single REJECTED
        result, `HOLD`/`REJECT` both represented this way -- there is no
        separate "come back later" queue this engine polls, same as
        Track 12's `CONFLICTING_SOURCE_DATA`) when the signal must be
        held out of live routing. The signal itself is still persisted
        (already done by the caller, `_handle_signal`, before this is
        called) -- never silently dropped from the audit trail, only ever
        held out of the live pipeline, same "recorded, never routed"
        convention as `stale_backlog_import_only`/
        `CONFLICTING_SOURCE_DATA`."""
        provider_row = self.store.get_provider_catalog_entry(signal.source)
        source_row: dict | None = None
        if provider_row is not None and signal.channel_id is not None:
            for candidate_source in self.store.list_sources(provider_id=signal.source):
                if candidate_source.get("source_native_id") == signal.channel_id:
                    source_row = candidate_source
                    break
        freshness_config = signal_freshness.freshness_config_from_rows(provider_row, source_row)
        decision = signal_freshness.evaluate_signal_freshness(signal, freshness_config)
        if decision.ok:
            return None
        logger.warning(
            "signal id=%s source=%s held out of live routing by freshness policy: %s",
            signal.id,
            signal.source,
            decision.reason,
        )
        return [
            OrderResult(
                account_id=self._STALE_SIGNAL_ACCOUNT_ID,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message=f"STALE_SIGNAL ({decision.action.value if decision.action else 'HOLD'}): {decision.reason}",
            )
        ]

    async def _correlate_cross_transport(self, signal: Signal) -> list[OrderResult] | None:
        """Track 12: cross-transport signal correlation/dedup -- see
        `app/signal_correlation.py`'s own module docstring for the full
        design. Called from `_handle_signal` ONLY after the existing
        exact-identity dedup (`find_signal_id_by_provider_identity`)
        found nothing for this signal.

        Returns `None` when there is nothing to do here (no real
        provider identity to correlate from, no fingerprint-matching
        candidate within the window, or a matching candidate that
        corroborates -- in the corroborating case, `signal.id` has
        already been canonicalized onto the earlier signal's id as a
        side effect, exactly like the exact-identity dedup path does, so
        the caller's own subsequent SIG-01 replay-lookup catches it for
        free) -- the caller should keep processing `signal` normally.

        Returns a REAL final `list[OrderResult]` (a single REJECTED
        result tagged `CONFLICTING_SOURCE_DATA`) when a candidate shares
        this signal's fingerprint key but disagrees materially on
        price/side -- the caller must return this immediately and never
        proceed to route/submit the conflicting signal. The conflicting
        signal itself is still persisted (via `save_signal`, tagged
        `import_batch="cross_transport_conflict:{canonical_id}"` -- the
        same "recorded, audited, never live-routed" convention Track 10
        uses for `needs_review_incomplete_content`/`stale_backlog_
        import_only`), and a `signal_correlation_evidence` row is
        recorded either way (see `app/db.py`'s own table comment) so
        `SignalStore.list_conflicting_signal_correlations` can surface
        it for human review -- this codebase's existing "recorded but
        held out of the live pipeline for a human to look at" pattern is
        what this reuses; there is no separate, generic incident/review-
        queue table elsewhere in this codebase to extend instead (see
        this task's own final report for that design note)."""
        # Only a signal with REAL provider identity ever participates --
        # see this module's own docstring for why (keeps every existing
        # adapter/test fixture with channel_id=None completely
        # unaffected, and keeps this scoped to genuine transports, not
        # ad hoc Signal objects built in-process).
        if signal.channel_id is None or signal.message_id is None:
            return None

        from app.signal_correlation import (
            ConflictResolutionPolicy,
            CorrelationOutcome,
            classify_candidate,
            fingerprint_key,
            resolve_conflict,
        )

        # Track 16: a provider row's own correlation_window_seconds/
        # correlation_price_tolerance_pct OVERRIDE this module's global
        # defaults when one exists for this signal's source -- strict
        # superset, falls back to the exact same config.* defaults as
        # before for a signal with no registered provider (or one that
        # never set these fields). See app/signal_correlation.py's own
        # module docstring for the "not yet wired" note this closes out.
        provider_row = self.store.get_provider_catalog_entry(signal.source)
        window = config.SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS
        price_tolerance = config.SIGNAL_CORRELATION_PRICE_TOLERANCE_PCT
        conflict_policy = ConflictResolutionPolicy.HOLD
        if provider_row is not None:
            if provider_row.get("correlation_window_seconds") is not None:
                window = provider_row["correlation_window_seconds"]
            if provider_row.get("correlation_price_tolerance_pct") is not None:
                price_tolerance = provider_row["correlation_price_tolerance_pct"]
            raw_policy = provider_row.get("conflict_resolution_policy")
            if raw_policy:
                try:
                    conflict_policy = ConflictResolutionPolicy(raw_policy)
                except ValueError:
                    logger.warning(
                        "provider=%s has unrecognized conflict_resolution_policy=%r -- failing closed to HOLD",
                        signal.source,
                        raw_policy,
                    )
                    conflict_policy = ConflictResolutionPolicy.HOLD

        key = fingerprint_key(signal)
        received_at = signal.received_at if signal.received_at.tzinfo else signal.received_at.replace(tzinfo=timezone.utc)
        since = datetime.fromtimestamp(received_at.timestamp() - window, tz=timezone.utc)
        until = datetime.fromtimestamp(received_at.timestamp() + window, tz=timezone.utc)
        candidates = self.store.find_correlation_candidates(
            fingerprint_key=key, exclude_channel_id=signal.channel_id, since=since, until=until
        )

        any_corroborating_or_conflicting = False
        for candidate in candidates:
            candidate_price = candidate["price"]
            candidate_received_at = datetime.fromisoformat(candidate["received_at"])
            outcome = classify_candidate(
                new_price=signal.price,
                new_side=signal.side.value,
                new_received_at=received_at,
                candidate_price=candidate_price,
                candidate_side=candidate["side"],
                candidate_received_at=candidate_received_at,
                price_tolerance_pct=price_tolerance,
                window_seconds=window,
            )
            if outcome is None:
                continue  # not eligible to compare (missing price on either side) -- never guessed
            any_corroborating_or_conflicting = True
            self.store.record_signal_correlation_evidence(
                canonical_signal_id=candidate["id"],
                evidence_signal_id=signal.id,
                fingerprint_key=key,
                source=signal.source,
                channel_id=signal.channel_id,
                message_id=signal.message_id,
                price=signal.price,
                side=signal.side.value,
                received_at=received_at,
                match_type=outcome.value,
            )
            if outcome is CorrelationOutcome.CORROBORATING:
                logger.info(
                    "signal id=%s (channel=%s) corroborates already-recorded signal id=%s (channel=%s) -- "
                    "same fingerprint, price/side agree within tolerance; treating as the same underlying "
                    "trade, not submitting a second order",
                    signal.id,
                    signal.channel_id,
                    candidate["id"],
                    candidate["channel_id"],
                )
                signal.id = candidate["id"]
                return None
            # CONFLICTING: Track 16 -- resolve through this provider's own
            # ConflictResolutionPolicy (default HOLD, Track 12's original
            # behavior, unchanged for every unconfigured provider). See
            # app/signal_correlation.py's `resolve_conflict` for the full,
            # purely structural (never LLM/AI) decision.
            primary_native_id, primary_count = (None, 0)
            deterministic_native_id = None
            if conflict_policy is ConflictResolutionPolicy.REQUIRE_PRIMARY_SOURCE:
                primary_native_id, primary_count = self.store.get_primary_source_native_id(signal.source)
            elif conflict_policy is ConflictResolutionPolicy.PROVIDER_DETERMINISTIC:
                det_source_id = provider_row.get("deterministic_primary_source_id") if provider_row else None
                if det_source_id:
                    det_source = self.store.get_source(det_source_id)
                    deterministic_native_id = det_source.get("source_native_id") if det_source else None

            resolution = resolve_conflict(
                policy=conflict_policy,
                new_channel_id=signal.channel_id,
                candidate_channel_id=candidate["channel_id"],
                primary_source_native_id=primary_native_id,
                primary_source_count=primary_count,
                deterministic_source_native_id=deterministic_native_id,
            )

            if resolution.action == "trust_new":
                logger.warning(
                    "CONFLICTING_SOURCE_DATA resolved by policy=%s: trusting NEW signal id=%s (channel=%s) "
                    "over candidate id=%s (channel=%s) -- %s",
                    conflict_policy.value,
                    signal.id,
                    signal.channel_id,
                    candidate["id"],
                    candidate["channel_id"],
                    resolution.reason,
                )
                return None
            if resolution.action == "trust_candidate":
                logger.warning(
                    "CONFLICTING_SOURCE_DATA resolved by policy=%s: trusting CANDIDATE signal id=%s "
                    "(channel=%s) over new signal id=%s (channel=%s) -- %s",
                    conflict_policy.value,
                    candidate["id"],
                    candidate["channel_id"],
                    signal.id,
                    signal.channel_id,
                    resolution.reason,
                )
                signal.id = candidate["id"]
                return None

            # "hold" (the floor -- HOLD itself, or any other policy that
            # couldn't make its own specific determination): never
            # silently pick one side -- hold this signal out of live
            # routing entirely and surface it for human review (see this
            # method's own docstring).
            logger.warning(
                "CONFLICTING_SOURCE_DATA: signal id=%s (channel=%s, source=%s) shares fingerprint with "
                "already-recorded signal id=%s (channel=%s) but disagrees on price/side -- held out of "
                "live routing, recorded for human review (policy=%s: %s)",
                signal.id,
                signal.channel_id,
                signal.source,
                candidate["id"],
                candidate["channel_id"],
                conflict_policy.value,
                resolution.reason,
            )
            signal.import_batch = f"cross_transport_conflict:{candidate['id']}"
            self.store.save_signal(signal)
            return [
                OrderResult(
                    account_id=self._CROSS_TRANSPORT_CONFLICT_ACCOUNT_ID,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=(
                        f"CONFLICTING_SOURCE_DATA: shares fingerprint with signal id={candidate['id']!r} "
                        f"(channel_id={candidate['channel_id']!r}) but disagrees on price/side -- held for "
                        f"human review (policy={conflict_policy.value}: {resolution.reason}), never "
                        "auto-resolved by AI/LLM judgment"
                    ),
                )
            ]

        # Track 16: ConflictResolutionPolicy.REQUIRE_MATCHING_SOURCES --
        # the "current single-source-triggers-immediately behavior becomes
        # opt-in" case (see that enum member's own docstring). No
        # fingerprint-matching candidate was found at all (the ordinary,
        # single-source case that reaches here for every OTHER policy and
        # proceeds to route immediately) -- for THIS policy, hold this
        # first-seen source's signal until a second, independent,
        # corroborating source's signal actually arrives (which will find
        # THIS one as a candidate above and canonicalize onto it via the
        # ordinary CORROBORATING path -- see that branch). This never
        # applies once at least one candidate was found (corroborating OR
        # conflicting) above -- that path already ran its own, correct
        # (2-source) logic.
        if not any_corroborating_or_conflicting and conflict_policy is ConflictResolutionPolicy.REQUIRE_MATCHING_SOURCES:
            logger.info(
                "signal id=%s (channel=%s, source=%s) held pending a second, independent corroborating "
                "source -- ConflictResolutionPolicy.REQUIRE_MATCHING_SOURCES for this provider",
                signal.id,
                signal.channel_id,
                signal.source,
            )
            signal.import_batch = f"insufficient_corroboration:{key}"
            self.store.save_signal(signal)
            return [
                OrderResult(
                    account_id=self._INSUFFICIENT_CORROBORATION_ACCOUNT_ID,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=(
                        "INSUFFICIENT_CORROBORATION: ConflictResolutionPolicy.REQUIRE_MATCHING_SOURCES for "
                        f"provider={signal.source!r} -- held until a second, independent source corroborates "
                        "this fingerprint; never auto-resolved by AI/LLM judgment"
                    ),
                )
            ]
        return None

    #: `providers.certification_state` values (app.provider_catalog.
    #: CertificationState) that mean "not yet certified live" -- shadow
    #: mode runs for exactly these, per the user's own spec ("Wire this
    #: to trigger automatically for any provider/source whose
    #: certification_state is SHADOW (or lower)"). CERTIFIED is the one
    #: state shadow mode does NOT run for -- a fully certified provider
    #: has already been through this and every other certification
    #: check; continuing to shadow-evaluate it forever would just be
    #: noise, not signal.
    _SHADOW_MODE_CERTIFICATION_STATES = frozenset({"uncertified", "draft", "tested", "shadow"})

    def _maybe_run_shadow_mode(self, signal: Signal) -> None:
        """Track 17: computes and records shadow-mode results for
        `signal` when its provider (`providers.id == signal.source`) is
        registered and not yet `certification_state == 'certified'`. See
        `app/shadow_mode.py`'s own module docstring for the full design
        and the structural guarantee that this NEVER submits a real
        order -- `evaluate_shadow` returns plain, inert dataclasses; the
        only side effect here is `self.store.record_shadow_mode_result`,
        an INSERT into `shadow_mode_results`, never `save_order_result`
        or any broker call.

        A provider this engine has never heard of (most of this
        codebase's own test fixtures, and any deployment that hasn't
        adopted the Track 14 catalog yet) is silently skipped -- shadow
        mode is additive coverage for a REGISTERED, not-yet-certified
        provider, never a requirement every signal must satisfy.
        Exceptions are caught and logged, never raised: a shadow-mode
        computation failing must never be able to break real signal
        processing (see this method's own call site)."""
        try:
            provider = self.store.get_provider_catalog_entry(signal.source)
            if provider is None or provider.get("certification_state") not in self._SHADOW_MODE_CERTIFICATION_STATES:
                return
            intents = evaluate_shadow(
                signal, self.routing, certification_version=provider.get("certification_version")
            )
            for intent in intents:
                self.store.record_shadow_mode_result(to_result_row(intent))
        except Exception:  # noqa: BLE001 -- shadow mode must never break real signal processing
            logger.exception("shadow-mode evaluation failed for signal id=%s source=%s", signal.id, signal.source)

    def _reject(self, account: DestinationAccount, order_signal: Signal, message: str) -> OrderResult:
        return OrderResult(
            account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=order_signal.id, message=message
        )

    def _check_route_qualified(
        self, account: DestinationAccount, signal: Signal, broker: BrokerAdapter
    ) -> tuple[bool, OrderResult | None]:
        """Track 1b live-routing gate: refuse to route a live ENTRY through
        an execution route -- (adapter_type, route_key, asset_class,
        product_type), the exact tuple app/qualification.py's ladder is
        tracked per -- that has never had a human operator record
        `release_approved` for it (`POST /qualifications`, app/main.py).
        See app/qualification.py's own module docstring for why this is a
        deliberate human sign-off, never something this engine (or any
        other code) may set on its own.

        `adapter_type` is `account.broker` (the exact string key this
        engine's own `self.brokers` dict -- and app/main.py's
        `POST /qualifications` `brokers_by_name` -- is keyed by), and
        `route_key` is `account.account_id` -- the finest per-route
        distinction this schema actually carries (see
        `_UNDECLARED_ROUTE_PRODUCT_TYPE`'s own comment on `product_type`).

        PAPER accounts are exempt: `PaperBroker` never sends an order
        anywhere outside this process's own memory (see that class's own
        module docstring) -- there is no real live capital this gate is
        meant to protect on a paper route, and gating it would make the
        paper broker useless for the exact dev/test workflows it exists
        for. Checked by `isinstance`, not by the string name
        `account.broker` happens to be registered under, so this can't be
        bypassed by an operator naming some other, real adapter "paper".

        A CLOSE signal is NOT gated here -- same "entry pause, not exit
        block" reasoning as EXE-10's `account.enabled`/provider-disable
        handling elsewhere in this loop (see `_handle_signal`'s own call
        site): refusing to let an already-open live position on an
        unqualified route ever be closed through this engine would trap
        it, which is strictly more dangerous than the unqualified route
        existing in the first place. Only a live ENTRY -- new risk this
        route has never been signed off to carry -- is refused.
        """
        if isinstance(broker, PaperBroker):
            return True, None
        route_key = account.account_id
        asset_class = signal.asset_class.value
        approved = self.store.is_route_release_approved(
            adapter_type=account.broker,
            route_key=route_key,
            asset_class=asset_class,
            product_type=_UNDECLARED_ROUTE_PRODUCT_TYPE,
        )
        if not approved:
            return False, self._reject(
                account,
                signal,
                "route not qualified for live release: no 'release_approved' qualification recorded for "
                f"route (adapter_type='{account.broker}', route_key='{route_key}', "
                f"asset_class='{asset_class}', product_type='{_UNDECLARED_ROUTE_PRODUCT_TYPE}') -- "
                "see app/qualification.py; a human operator must record every ladder rung up through "
                "release_approved for this exact route via POST /qualifications before it may route a "
                "live order",
            )
        return True, None

    async def _check_risk_basis(
        self, account: DestinationAccount, order_signal: Signal, quantity: float
    ) -> tuple[bool, OrderResult | None]:
        """E03 (risk-basis sizing): `account.risk_percent_of_equity` gates
        this entry's risk-to-stop (|entry_price - stop_loss| * quantity)
        against that percentage of the account's real, just-fetched
        equity. Fails closed -- REJECTS -- for every one of:
        - no `stop_loss` on the signal (nothing to compute risk-to-stop
          against; never assumed to be "no stop" == "no risk").
        - no broker registered for this account, or that broker doesn't
          implement `get_account_balance` at all (`has_balance_capability`
          False) -- there is no real equity figure to check against.
        - `get_account_balance` returns `None`, or a real `AccountBalance`
          whose `equity` field is itself `None` (broker reachable but this
          particular figure genuinely unavailable, e.g. a spot account
          with no unified equity concept) -- same fail-closed treatment as
          a broker error: an unresolved risk gate is never a passed one.
        This call always fetches fresh (no cache), so there is no
        "stale equity" figure this could silently reuse -- see this
        module's own `_try_reserve_capital` caller for why a fresh fetch
        per admission is preferred over a cached one that could grow
        stale."""
        if account.risk_percent_of_equity is None:
            # Defensive/fail-closed, unreachable from `_try_reserve_capital`
            # (its own caller only invokes this when the field IS set) --
            # kept so this function is safe standalone and so mypy can
            # narrow the attribute to `float` below.
            return False, self._reject(
                account, order_signal, f"account '{account.account_id}' has no risk_percent_of_equity configured"
            )
        risk_percent_of_equity = account.risk_percent_of_equity
        if order_signal.stop_loss is None:
            return False, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has risk_percent_of_equity configured but this signal "
                "carries no stop_loss -- risk-to-stop can't be computed, refusing rather than admitting "
                "an unsized risk",
            )
        if order_signal.price is None:
            # Defensive/fail-closed: every real caller (`_try_reserve_capital`)
            # already rejects a priceless signal before this is ever reached
            # whenever ANY gate (including this one) is configured -- this
            # branch exists so this function is safe to call on its own
            # (never assumes a caller invariant it can't verify) and so
            # mypy can narrow `order_signal.price` to `float` below.
            return False, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has risk_percent_of_equity configured but this signal "
                "carries no price -- risk-to-stop can't be computed, refusing",
            )
        if (
            not math.isfinite(order_signal.price)
            or order_signal.price <= 0
            or not math.isfinite(order_signal.stop_loss)
            or order_signal.stop_loss <= 0
        ):
            # Fail-closed defense-in-depth: sources are expected to reject a
            # non-finite/zero/negative price or stop_loss before a Signal
            # ever reaches here (app/sources/text_parser.py,
            # app/sources/ninjatrader.py, app/sources/webhook.py's
            # _optional_positive_float), but this admission path must never
            # trust that blindly -- a price of 0 makes risk_notional 0 (an
            # unbounded admission), a NaN price makes the `>` ceiling
            # comparison below silently always False (never trips), and a
            # negative price/stop_loss corrupts the risk-to-stop figure.
            return False, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has risk_percent_of_equity configured but this signal's "
                f"price ({order_signal.price!r}) or stop_loss ({order_signal.stop_loss!r}) is not a finite "
                "positive number -- risk-to-stop can't be safely computed, refusing",
            )
        stop_loss = order_signal.stop_loss
        price = order_signal.price
        broker = self.brokers.get(account.broker)
        if broker is None or not broker.has_balance_capability:
            return False, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has risk_percent_of_equity configured but its broker "
                f"'{account.broker}' has no way to report real account equity -- refusing rather than "
                "sizing risk against an unverified/guessed equity figure",
            )
        balance = await broker.get_account_balance(account)
        if balance is None or balance.equity is None:
            return False, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has risk_percent_of_equity configured but its broker "
                "could not report a current equity figure right now -- refusing rather than sizing risk "
                "against a stale or invented one",
            )
        equity = balance.equity
        risk_notional = abs(price - stop_loss) * abs(quantity)
        risk_ceiling = equity * risk_percent_of_equity
        if risk_notional > risk_ceiling:
            return False, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' risk-to-stop ceiling ({risk_percent_of_equity:.4f} of "
                f"equity {equity:.2f} = {risk_ceiling:.2f}) would be exceeded by this entry's risk "
                f"({risk_notional:.2f}) -- refusing",
            )
        return True, None

    async def _check_buying_power(
        self, account: DestinationAccount, order_signal: Signal, quantity: float
    ) -> tuple[bool, OrderResult | None]:
        """Track 1b (audit finding: "AccountBalance.buying_power is
        fetched and displayed ... but is never used as an admission gate
        anywhere"): refuses an entry whose notional (|quantity| *
        |price|) would exceed this account's real, just-fetched
        broker-reported `buying_power`, wherever the broker can report
        one.

        Deliberately NOT conditioned on `account.max_notional_exposure`/
        `risk_percent_of_equity` being configured -- broker buying power
        is a real, hard constraint on what CAN be submitted regardless of
        whether the operator opted into either of this service's own
        ceilings, so this runs for every entry, on every account. It is
        independent of, and never a substitute for, `account.
        max_notional_exposure`/`risk_percent_of_equity`/the owner-wide
        ceiling below -- passing this check proves nothing about those,
        and a broker reporting ample buying power never overrides or
        loosens them.

        Mirrors `_check_risk_basis`'s fail-closed-everywhere-verifiable
        pattern, with one deliberate difference driven by this check
        being unconditional rather than opt-in: it fails OPEN (admits,
        logs that the check was skipped) wherever buying power genuinely
        can't be verified for this signal/account/broker --
        - no broker registered, or `broker.has_balance_capability` is
          `False` (no real `get_account_balance` override at all, e.g.
          CCXTBroker on a spot market -- see `AccountBalance.
          buying_power`'s own docstring on why that's not a universal
          concept).
        - `get_account_balance` returns `None`, or a real `AccountBalance`
          whose `buying_power` is itself `None` (broker reachable, this
          particular figure genuinely not reported for this account).
        - the signal carries no resolvable finite positive `price` --
          notional can't be computed. Unlike `_try_reserve_capital`'s own
          price checks (which REJECT because those gates are something
          the operator explicitly opted into for this account), this
          check applies unconditionally, so a priceless signal that would
          have sailed through with zero gates configured before this
          existed must not newly be rejected by a check nobody asked for.
        Every other case -- a real, current `buying_power` figure IS
        available -- fails CLOSED: `notional > buying_power` is refused,
        exactly like `_check_risk_basis` refuses when risk-to-stop would
        exceed its ceiling."""
        broker = self.brokers.get(account.broker)
        if broker is None or not broker.has_balance_capability:
            logger.info(
                "buying_power_check_skipped account=%s broker=%s reason=no_verified_balance_capability",
                account.account_id,
                account.broker,
            )
            return True, None
        if order_signal.price is None or not math.isfinite(order_signal.price) or order_signal.price <= 0:
            logger.info(
                "buying_power_check_skipped account=%s broker=%s reason=no_resolvable_price",
                account.account_id,
                account.broker,
            )
            return True, None
        balance = await broker.get_account_balance(account)
        if balance is None or balance.buying_power is None:
            logger.info(
                "buying_power_check_skipped account=%s broker=%s reason=buying_power_not_reported",
                account.account_id,
                account.broker,
            )
            return True, None
        notional = abs(quantity) * abs(order_signal.price)
        if notional > balance.buying_power:
            return False, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' insufficient buying power: broker-reported buying_power "
                f"({balance.buying_power:.2f}) is less than this entry's notional ({notional:.2f}) -- "
                "refusing",
            )
        return True, None

    async def _try_reserve_capital(
        self, account: DestinationAccount, order_signal: Signal, quantity: float
    ) -> tuple[bool, float, OrderResult | None]:
        """E03: admit this entry against every gate configured for this
        account and, if configured, this owner: `account.
        max_notional_exposure`, `account.risk_percent_of_equity`, and
        `self.max_owner_notional_exposure`. Returns (admitted,
        notional_reserved, rejection_or_None). `notional_reserved` is
        always the caller's responsibility to release via
        `self.capital_allocator.release(account.account_id, notional)`
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
        release when nothing was actually reserved, i.e. no gate was
        configured).

        Fail-closed, not fail-open, in every one of these cases (a release
        audit finding this module previously got wrong for the first two):
        - No gate at all configured for this account or owner-wide: no
          check to make, admits unconditionally, same as before any of
          this existed.
        - SOME gate IS configured but the signal has no `price`: used to
          silently SKIP the whole check (an unbounded admission, no
          different from having no ceiling at all). Now REJECTED outright
          -- this build has no independent current-market-price source to
          fall back to, and guessing one would be worse than refusing.
        - This account has open exposure this replay could not resolve
          (`ExposureReport.unresolved_symbols`, e.g. a fill row with a
          missing/invalid quantity or price): used to contribute 0.0 to
          confirmed exposure, i.e. treated as no exposure at all. Now
          REJECTED -- new admissions are blocked for this account until
          the position resolves (see app/capital_allocator.py's module
          docstring, point 2, for why this is safer than inventing a
          worst-case notional estimate here).

        Track 1b: also runs `_check_buying_power` first, unconditionally
        -- an independent admission gate against this account's real,
        broker-reported buying power, regardless of whether any of the
        gates above are configured for this account at all (see that
        method's own docstring). It never weakens or substitutes for the
        gates below -- both must pass; this one simply runs first, before
        anything here has taken a lock or reserved anything."""
        bp_ok, bp_rejection = await self._check_buying_power(account, order_signal, quantity)
        if not bp_ok:
            assert bp_rejection is not None
            return False, 0.0, bp_rejection

        has_gate = (
            account.max_notional_exposure is not None
            or account.risk_percent_of_equity is not None
            or self.max_owner_notional_exposure is not None
        )
        if not has_gate:
            return True, 0.0, None
        if order_signal.price is None:
            return False, 0.0, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has a capital/risk exposure gate configured "
                "(max_notional_exposure, risk_percent_of_equity, and/or an owner-wide ceiling) but this "
                "signal carries no price -- notional can't be computed and this build has no independent "
                "current-market-price source to fall back to, so admission is refused rather than "
                "silently skipping the check (see app/capital_allocator.py)",
            )
        if not math.isfinite(order_signal.price) or order_signal.price <= 0:
            # Fail-closed defense-in-depth, same rationale as the `price is
            # None` check just above: sources are expected to reject a
            # non-finite/zero/negative price before a Signal ever reaches
            # this admission path, but this gate must never trust that
            # blindly. Left unchecked: price=0 makes notional=0 (every
            # notional/risk ceiling below is trivially satisfied regardless
            # of real trade size), price=NaN makes every `>` ceiling
            # comparison below silently evaluate False (never trips), and
            # price<0 makes notional negative, corrupting
            # CapitalAllocator._pending's running total for this account.
            return False, 0.0, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has a capital/risk exposure gate configured but this "
                f"signal's price ({order_signal.price!r}) is not a finite positive number -- notional "
                "can't be safely computed, refusing rather than admitting an unbounded or corrupted "
                "reservation",
            )
        notional = abs(quantity) * abs(order_signal.price)

        owner_gated = self.max_owner_notional_exposure is not None
        if owner_gated:
            await self.capital_allocator.owner_lock.acquire()
        try:
            async with self.capital_allocator.account_lock(account.account_id):
                exposure = confirmed_open_notional(self.store, account.account_id)
                if exposure.has_unresolved:
                    return False, notional, self._reject(
                        account,
                        order_signal,
                        f"account '{account.account_id}' has open exposure for symbol(s) "
                        f"{exposure.unresolved_symbols} this replay could not resolve an average cost for -- "
                        "true current notional exposure is unknown and can't safely be treated as zero, "
                        "refusing new admissions for this account until it resolves",
                    )

                if account.max_notional_exposure is not None:
                    pending = self.capital_allocator.pending_reservation(account.account_id)
                    if exposure.notional + pending + notional > account.max_notional_exposure:
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            f"account '{account.account_id}' notional exposure ceiling "
                            f"({account.max_notional_exposure}) would be exceeded by this entry "
                            f"(confirmed={exposure.notional:.2f}, pending={pending:.2f}, "
                            f"requested={notional:.2f}) -- refusing",
                        )

                if account.risk_percent_of_equity is not None:
                    ok, rejection = await self._check_risk_basis(account, order_signal, quantity)
                    if not ok:
                        return False, notional, rejection

                if owner_gated:
                    owner_exposure = owner_wide_exposure(self.store, self.routing.accounts.values(), self.capital_allocator)
                    if owner_exposure.has_unresolved:
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            "owner-wide exposure ceiling is configured but at least one account has open "
                            f"exposure this replay could not resolve ({owner_exposure.unresolved_symbols}) -- "
                            "true owner-wide notional exposure is unknown, refusing new admissions until it "
                            "resolves",
                        )
                    # `owner_gated` (from `self.max_owner_notional_exposure is
                    # not None` above) guarantees this is a float here, but
                    # mypy can't narrow through the boolean flag -- assert
                    # rather than silently risk a `None` ceiling comparing
                    # as "always fits" if that ever changed.
                    assert self.max_owner_notional_exposure is not None
                    if owner_exposure.notional + notional > self.max_owner_notional_exposure:
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            f"owner-wide notional exposure ceiling ({self.max_owner_notional_exposure}) would "
                            f"be exceeded by this entry (owner-wide confirmed+pending="
                            f"{owner_exposure.notional:.2f}, requested={notional:.2f}) -- refusing",
                        )

                self.capital_allocator.reserve_locked(account.account_id, notional, signal_id=order_signal.id)
                return True, notional, None
        finally:
            if owner_gated:
                self.capital_allocator.owner_lock.release()

    def _resolve_close(
        self, signal: Signal, account: DestinationAccount, symbol: str, *, position: float | None = None
    ) -> tuple[Signal, float] | None:
        """`position`: the caller's already-known tracked position, reused
        instead of re-reading `SignalStore.get_position` a second time when
        the caller (`_resolve_and_submit_plain_close`) already read it once
        under the same (account_id, symbol) lock to run reconciliation
        against -- re-reading here could otherwise observe a DIFFERENT
        value if some other, non-close write (e.g. a concurrent entry fill
        for the same symbol) landed in between, silently resolving this
        close against a quantity reconciliation never actually checked.
        `None` (the default) preserves the original single-read behavior
        for every other caller."""
        if position is None:
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
    ) -> tuple[OrderResult, float | None, float | None, float, float, datetime, float | None]:
        """Returns (result, applied_quantity, confirmed_cumulative_fill,
        applied_execution_delta, outstanding_possible_fill, submitted_at,
        acknowledged_quantity).
        `applied_quantity` is what was actually applied to the tracked
        position (None if nothing was), for the caller to pass into
        `save_order_result`'s `applied_quantity` so the stored row matches
        what `record_fill` did (see that parameter's docstring for why the
        two must agree). The next three are AUD-01's distinct-field
        quantity model -- see handle_signal's identical computation and
        this module's own docstring section for the shared contract.
        `submitted_at` (PU-A2) is the real moment this call actually reached
        the broker, captured immediately before it -- see handle_signal's
        identical field for what it feeds into. `acknowledged_quantity`
        (TRK-Q1) is app/quantity.py's `acknowledged_quantity_for` --
        see handle_signal's identical computation. This call site never
        reserves capital for a close (see this module's own docstring,
        "Close signals"), so there is no `reserved_quantity` to report here.

        P0-2: this is the plain-account (non-managed_lifecycle) close's
        one real broker.place_order call site -- `_resolve_and_submit_
        plain_close`'s own `claim_close` already gives it cross-process
        exclusion per (account_id, symbol), but the command ledger's own
        idempotency check is a second, independent layer (a retried close
        signal reaching this call again AFTER the first one's claim was
        already released) rather than relying on that lock alone."""
        ledger_key = f"close:{account.account_id}:{symbol}:{order_signal.id}"
        ledger_fingerprint = command_ledger.compute_fingerprint(
            {
                "command_type": "close",
                "account_id": account.account_id,
                "symbol": symbol,
                "side": order_signal.side.value,
                "quantity": quantity,
                "signal_id": order_signal.id,
            }
        )
        ledger_entry = self.store.open_command_ledger_entry(
            idempotency_key=ledger_key,
            command_type=CommandType.CLOSE,
            account_id=account.account_id,
            environment=command_ledger.current_environment(),
            request_fingerprint=ledger_fingerprint,
        )
        if command_ledger.is_duplicate_submission(ledger_entry.uncertainty_state):
            logger.info(
                "duplicate close command idempotency_key=%s account=%s symbol=%s -- replaying "
                "tracked state=%s instead of resubmitting",
                ledger_key,
                account.account_id,
                symbol,
                ledger_entry.uncertainty_state.value,
            )
            result = OrderResult(
                account_id=account.account_id,
                status=OrderStatus.PENDING,
                signal_id=order_signal.id,
                broker_order_id=ledger_entry.remote_identifiers.get("broker_order_id"),
                message=(
                    f"duplicate command (idempotency_key={ledger_key}); tracked state="
                    f"{ledger_entry.uncertainty_state.value}, not resubmitted"
                ),
            )
            return result, None, None, 0.0, 0.0, datetime.now(timezone.utc), None

        submitted_at = datetime.now(timezone.utc)
        try:
            result = await broker.place_order(order_signal, account, quantity, symbol)
        except Exception as exc:  # noqa: BLE001 - one account's failure must not block others
            logger.exception("order failed for account=%s", account.account_id)
            result = OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=order_signal.id, message=str(exc)
            )
            self.store.mark_command_ledger_outcome(
                ledger_key,
                uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                terminal_evidence=command_ledger.ambiguous_evidence_for_exception(exc),
            )
        else:
            outcome_state, outcome_remote, outcome_evidence = command_ledger.classify_order_result(result)
            self.store.mark_command_ledger_outcome(
                ledger_key, uncertainty_state=outcome_state, remote_identifiers=outcome_remote, terminal_evidence=outcome_evidence
            )
        # AUD-01: mirrors handle_signal's identical branch exactly -- a
        # PENDING result with `filled_quantity is None` confirms nothing,
        # so `actual_remaining_ownership` (positions.net_quantity) must not
        # change for it; only a real, broker-reported quantity (FILLED, or
        # a genuine partial-fill progress report alongside PENDING -- EXE-04:
        # explicit 0.0 is real progress too) is applied.
        applied_quantity: float | None = None
        confirmed_cumulative_fill: float | None = None
        outstanding_possible_fill = 0.0
        if result.status == OrderStatus.FILLED:
            applied_quantity = quantity if result.filled_quantity is None else result.filled_quantity
            confirmed_cumulative_fill = applied_quantity
        elif result.status == OrderStatus.PENDING:
            confirmed_cumulative_fill = result.filled_quantity
            if result.filled_quantity is not None:
                applied_quantity = result.filled_quantity
            outstanding_possible_fill = quantity - (confirmed_cumulative_fill or 0.0)
        if applied_quantity is not None:
            self.store.record_fill(account.account_id, symbol, order_signal.side, applied_quantity)
        applied_execution_delta = applied_quantity if applied_quantity is not None else 0.0
        acknowledged_quantity = quantity_module.acknowledged_quantity_for(result, quantity)
        return (
            result,
            applied_quantity,
            confirmed_cumulative_fill,
            applied_execution_delta,
            outstanding_possible_fill,
            submitted_at,
            acknowledged_quantity,
        )

    async def _reconcile_before_plain_close(
        self, signal: Signal, account: DestinationAccount, symbol: str, local_position: float, broker: BrokerAdapter
    ) -> OrderResult | None:
        """P0-5: a plain (non-managed_lifecycle) account's CLOSE resolves
        against this service's own locally tracked position
        (`SignalStore.get_position` -- see this module's own docstring,
        "Close signals"), never a live read of the broker's actual book.
        That's an acceptable simplification only as long as nothing else
        can move that account's real position without this service
        knowing -- which is exactly what can stop being true after a
        manual intervention, an external fill placed directly at the
        broker, a corporate action, or ordinary reconciliation lag (a
        PENDING order whose terminal status this service hasn't polled
        yet). Before a plain close is allowed to proceed, this requires
        ONE of:

          (a) A fresh broker position readback
              (`BrokerAdapter.get_broker_position`) that matches
              `local_position` within `_positions_reconcile`'s tolerance --
              only real when `broker.has_position_readback_capability` is
              True (a REAL, verified adapter override -- see
              app/brokers/base.py's "computed, not declared" section, not
              a name/imported-SDK claim). This is the preferred path and
              is checked first.

          (b) This account's own explicit, narrow, OFF-BY-DEFAULT
              `DestinationAccount.exclusive_writer_qualified` flag -- an
              operator's deliberate, per-account assertion that nothing
              else writes to this specific broker account's position, so
              the local tracked quantity genuinely IS authoritative. Only
              consulted when (a) isn't available (no verified readback
              capability, or the readback itself came back unknown) --
              this method never falls back to (b) just because (a)
              happened to disagree; a disagreement is reported as a
              reconciliation failure, not silently downgraded to "trust
              the flag instead."

        Returns a REJECTED `OrderResult` (never raises, never proceeds) if
        NEITHER holds -- this is a fail-closed gate: an unreconciled,
        unqualified plain close must be blocked with a clear, actionable
        reason, not allowed through on an unproven local projection.
        Returns `None` when the close may proceed.
        """
        if broker.has_position_readback_capability:
            broker_position = await broker.get_broker_position(account, symbol)
            if broker_position is not None:
                if _positions_reconcile(broker_position, local_position):
                    return None
                return OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=(
                        f"broker-reported position ({broker_position}) for account "
                        f"'{account.account_id}' symbol '{symbol}' does not match this service's "
                        f"own tracked position ({local_position}) -- refusing to close against a "
                        "stale/unreconciled local projection. Possible causes: manual intervention, "
                        "an external fill placed directly at the broker, a corporate action, or "
                        "reconciliation lag. Reconcile the account (or correct the tracked position) "
                        "before retrying this close."
                    ),
                )
            # `broker_position is None`: a broker that CLAIMS the
            # capability (overrides the method) but genuinely couldn't
            # answer this specific query right now. Treat exactly like "no
            # verified capability" -- fall through to the
            # exclusive-writer-qualified check below rather than assume
            # anything about the real book.
        if account.exclusive_writer_qualified:
            return None
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.REJECTED,
            signal_id=signal.id,
            message=(
                f"cannot reconcile account '{account.account_id}' symbol '{symbol}' before closing: "
                f"broker '{account.broker}' has no verified position-readback capability (or its "
                "readback returned unknown), and this account is not marked "
                "exclusive_writer_qualified. Refusing to close against this service's own locally "
                "tracked position alone. Either use a broker adapter with a real "
                "get_broker_position implementation, or set exclusive_writer_qualified=True on this "
                "account ONLY if you are certain nothing else can write to its position outside "
                "Signal Copier."
            ),
        )

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
                local_position = self.store.get_position(account.account_id, symbol)
                if local_position == 0:
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

                # P0-5: reconcile BEFORE resolving/submitting anything --
                # see _reconcile_before_plain_close's own docstring for the
                # full contract. Reuses local_position (already read above,
                # under this same lock) rather than letting _resolve_close
                # read it again, which could observe a different value.
                reconciliation_rejection = await self._reconcile_before_plain_close(
                    signal, account, symbol, local_position, broker
                )
                if reconciliation_rejection is not None:
                    self.store.save_order_result(reconciliation_rejection, purpose="close", family_id=None)
                    return reconciliation_rejection

                resolved = self._resolve_close(signal, account, symbol, position=local_position)
                assert resolved is not None  # local_position != 0 already checked above

                order_signal, quantity = resolved
                (
                    result,
                    applied_quantity,
                    confirmed_cumulative_fill,
                    applied_execution_delta,
                    outstanding_possible_fill,
                    submitted_at,
                    acknowledged_quantity,
                ) = await self._submit_order(order_signal, quantity, account, symbol, broker)
                self.store.save_order_result(
                    result,
                    broker=account.broker,
                    symbol=symbol,
                    side=order_signal.side,
                    requested_quantity=quantity,
                    applied_quantity=applied_quantity,
                    confirmed_cumulative_fill=confirmed_cumulative_fill,
                    applied_execution_delta=applied_execution_delta,
                    outstanding_possible_fill=outstanding_possible_fill,
                    acknowledged_quantity=acknowledged_quantity,
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
        # Multi-provider representability: a signal with an ORDERED
        # `targets` collection (see `Signal.targets`'s own docstring) is
        # now representable here directly -- one `Target(action=SELL)`
        # per level, in the same order, each with its own `reduce_fraction`
        # when the source gave one. Full multi-target ROUTING/sizing
        # semantics (e.g. resolving an absolute `quantity` on a level
        # against this account's own sized `quantity` above) stay out of
        # scope for this pass; this only makes an incoming multi-target
        # signal representable and non-crashing to consume, degrading to
        # the existing single-take_profit behavior when `targets` is
        # empty (every producer this task didn't touch).
        if signal.targets:
            # Known, disclosed gap (full multi-target execution logic is
            # out of scope for this pass): a level that carries a
            # `quantity` but no `fraction` (see `ProfitTarget`'s own
            # docstring -- each is independent/optional) resolves here to
            # `reduce_fraction=None`, which `PositionLifecycleManager`
            # itself already treats as a real, harmless 0.0-fraction
            # no-op-on-fire SELL (see its own `target.reduce_fraction or
            # 0.0`), never a crash -- it just doesn't yet reduce the
            # position at that level. A real per-level `quantity`
            # resolved against this account's own sized `quantity` (not
            # merely converted to a fraction here) needs its own,
            # separately-scoped follow-up.
            targets = [
                Target(trigger_price=level.price, action=TargetAction.SELL, reduce_fraction=level.fraction)
                for level in signal.targets
            ]
        elif signal.take_profit is not None:
            targets = [Target(trigger_price=signal.take_profit, action=TargetAction.SELL, reduce_fraction=1.0)]
        else:
            targets = []

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

        # P0-2: pre-effect durable command-ledger intent -- see
        # app/command_ledger.py's module docstring and handle_signal's
        # identical wiring for the plain-account entry path. `signal.id`
        # (== `entry_signal.id` above) is this entry's natural retry
        # identity.
        ledger_key = f"entry:{account.account_id}:{symbol}:{signal.id}"
        ledger_fingerprint = command_ledger.compute_fingerprint(
            {
                "command_type": "entry",
                "account_id": account.account_id,
                "symbol": symbol,
                "side": entry_signal.side.value,
                "quantity": quantity,
                "signal_id": signal.id,
            }
        )
        ledger_entry = self.store.open_command_ledger_entry(
            idempotency_key=ledger_key,
            command_type=CommandType.ENTRY,
            account_id=account.account_id,
            environment=command_ledger.current_environment(),
            request_fingerprint=ledger_fingerprint,
        )
        if command_ledger.is_duplicate_submission(ledger_entry.uncertainty_state):
            self.capital_allocator.release(account.account_id, notional)
            logger.info(
                "duplicate managed entry command idempotency_key=%s account=%s symbol=%s -- replaying "
                "tracked state=%s instead of resubmitting",
                ledger_key,
                account.account_id,
                symbol,
                ledger_entry.uncertainty_state.value,
            )
            return (
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.PENDING,
                    signal_id=signal.id,
                    broker_order_id=ledger_entry.remote_identifiers.get("broker_order_id"),
                    message=(
                        f"duplicate command (idempotency_key={ledger_key}); tracked state="
                        f"{ledger_entry.uncertainty_state.value}, not resubmitted"
                    ),
                ),
                None,
                None,
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
            self.store.mark_command_ledger_outcome(
                ledger_key,
                uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                terminal_evidence=command_ledger.ambiguous_evidence_for_exception(exc),
            )
            return (
                OrderResult(
                    account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id, message=str(exc)
                ),
                submitted_at,
                None,
            )

        # P0-2: one real broker answer landed (didn't raise) -- classify
        # and record it against the pre-effect row opened above, whatever
        # branch below then does with it.
        _ledger_state, _ledger_remote, _ledger_evidence = command_ledger.classify_order_result(result)
        self.store.mark_command_ledger_outcome(
            ledger_key, uncertainty_state=_ledger_state, remote_identifiers=_ledger_remote, terminal_evidence=_ledger_evidence
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
                    account,
                    symbol,
                    result.broker_order_id,
                    quantity,
                    reserved_notional=notional,
                    # TRK-Q1: same `notional`-gated rule as the plain path
                    # (see handle_signal's identical comment) -- 0.0, not
                    # the full quantity, when no gate was actually
                    # configured for this account/owner.
                    reserved_quantity=quantity if notional else 0.0,
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
        # Cross-process/cross-host fencing (app/writer_lease.py) -- same
        # fail-closed check as `_handle_signal`, and for the same reason:
        # this is the OTHER top-level entry point that can reach a broker
        # write (the dashboard's "Exit now"/"Flatten" actions), so it
        # needs its own independent check rather than relying on whatever
        # called it having already checked.
        self.lease_guard.require_active()
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
