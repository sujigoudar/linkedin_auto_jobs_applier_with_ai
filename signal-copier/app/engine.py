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
import hashlib
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from signal_platform_contracts import Environment, EventEnvelope, EvidenceClass

from app import command_ledger, config
from app import quantity as quantity_module
from app import signal_freshness
from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker
from app.capital_allocator import (
    CapitalAllocator,
    confirmed_open_notional,
    confirmed_strategy_notional,
    owner_wide_exposure,
)
from app.daily_loss_limiter import DailyLossLimiter
from app.margin_call_detector import MarginCallDetector
from app.db import SignalStore
from app.export_events import (
build_execution_applied_envelope,
    build_routing_admission_outcome_envelope,
    build_source_event_envelope,
    build_source_receipt_envelope,
)
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, Target, TargetAction, TrailingPolicy
from app.logging_config import bind_signal_context
from app.models import (
    AssetClass,
    CommandType,
    DestinationAccount,
    Intent,
    OrderResult,
    OrderStatus,
    ProfitTarget,
    Side,
    Signal,
    SourceEvent,
    UncertaintyState,
)
from app.providers import ProviderRegistry, SettingsOverride
from app.risk import (
    UnsizedEntryError,
    size_for_account,
    size_for_account_with_mode,
    symbol_for_account,
    contract_multiplier,
)
from app.routing import RoutingConfig
from app.shadow_mode import evaluate_shadow, to_result_row
from app.writer_lease import NullLeaseGuard, WriterLeaseGuard
from app.workflow.admission import AdmissionInputs, evaluate_admission
from app.workflow.budget import HierarchicalBudget, BudgetScope, ResourceVector, BrokerSnapshot, ReservationState, UNLIMITED_CENTS
from app.workflow.intents import OrderIntent, Outbox
from app.workflow.money import ceil_cents

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
#: field added; nothing here can honestly support that today. TRK-27:
#: since nothing here CAN honor two product_types sharing one route_key,
#: `SignalStore.record_route_qualification` (app/db.py) now refuses to
#: even let an operator record qualification history that way -- see its
#: own docstring's point 4 -- so this schema gap can no longer be
#: silently misconfigured into "an unqualified product riding on a
#: qualified account's route-qualification state"; it is a loud rejection
#: at write time instead.
_UNDECLARED_ROUTE_PRODUCT_TYPE = "default"


@dataclass(frozen=True)
class _ManagedOrderOutcome:
    """TRK-22: the widened return of `_handle_managed_entry`/
    `_handle_managed_close`/`_handle_managed_signal` -- adds AUD-01's
    distinct-field quantity model (see `_submit_order`'s own docstring for
    the shared contract) on top of the pre-existing `(result, submitted_at,
    protection_confirmed_at)` triple, so `_handle_signal`'s managed-
    lifecycle branch and `close_position` can finally pass these through to
    `save_order_result` the same way the plain-account path already does.

    A private, internal-plumbing type (not a public contract -- see
    app/models.py's `QuantityBreakdown` for the public, broader 7-field
    snapshot this is deliberately NOT reusing: that type's
    `reserved_quantity`/`protected_quantity`/etc. fields have no
    equivalent here, and forcing this narrower, save_order_result-shaped
    bundle into it would mean either fabricating those or leaving them
    permanently None on a "real" model).

    Every field here mirrors `_submit_order`'s own per-field honesty rule
    exactly: `None` (never a fabricated number) whenever this specific
    call never reached that stage or has nothing real to report (a
    pre-submission rejection inside `_handle_managed_entry`/
    `_handle_managed_close` itself, e.g. `validate_plan` failing or "no
    open position to close", never reaches a broker at all, so every field
    below stays `None` for it -- exactly `_handle_signal`'s own established
    convention for its equivalent pre-submission rejections). Once a real
    submission attempt is made (broker.place_order, or
    `PositionLifecycleManager.request_exit`, was actually called),
    `applied_execution_delta`/`outstanding_possible_fill` are always real
    floats (`0.0`, not `None`, when this save genuinely applied/has
    nothing to report), matching `save_order_result`'s own documented
    contract for those two fields."""

    result: OrderResult
    submitted_at: datetime | None
    protection_confirmed_at: datetime | None
    applied_quantity: float | None = None
    confirmed_cumulative_fill: float | None = None
    applied_execution_delta: float | None = None
    outstanding_possible_fill: float | None = None
    acknowledged_quantity: float | None = None


def _positions_reconcile(broker_position: float, local_position: float) -> bool:
    tolerance = _RECONCILIATION_ABS_TOLERANCE + _RECONCILIATION_REL_TOLERANCE * max(
        abs(broker_position), abs(local_position)
    )
    return abs(broker_position - local_position) <= tolerance


# Thin alias for backward compatibility; use contract_multiplier() from app/risk.py instead
_get_contract_multiplier = contract_multiplier


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
        # Daily loss limit enforcement (see app/daily_loss_limiter.py): circuit
        # breaker that rejects new entries if daily loss exceeds threshold.
        # Fail-closed: any error determining equity/loss leaves trading halted.
        self.daily_loss_limiter = DailyLossLimiter(store=store, brokers=brokers)
        # Margin call detection and persistence (E04 bounded): monitors account
        # maintenance requirements and persists alerts when equity approaches
        # broker thresholds. Fail-closed: inability to determine margin state
        # prevents trading to avoid silent failures.
        self.margin_call_detector = MarginCallDetector(store=store)
        # WC-20 STEP 4: Hierarchical budget allocator with atomic resource
        # reservation (§6.2–6.3). Ensures all resource checks (cash, margin,
        # notional, risk) apply simultaneously at owner, account, portfolio,
        # sleeve, provider, underlying and cluster levels before any broker
        # effect occurs. Claims opportunity atomically to prevent duplicate
        # admission from concurrent workers.
        self.hierarchical_budget = HierarchicalBudget(store=store)
        # WC-33: Outbox for durable order intents and dispatch coordination.
        # Decouples intent creation from broker dispatch for SUBMISSION_UNKNOWN
        # recovery. Enqueues an intent, claims it for dispatch, and records
        # the response in separate transactions (§6.3).
        self.outbox = Outbox(store)
        # Wired in after construction (app/lifecycle/manager.py's own
        # __init__ can't take this: main.py often constructs a
        # PositionLifecycleManager before this Engine, and so before this
        # CapitalAllocator, exists) -- lets `resolve_pending_entry` release
        # a managed entry's reservation once its outcome is confirmed
        # terminal. See PendingEntry's docstring for why.
        self.lifecycle_manager.capital_allocator = self.capital_allocator

        # WP-38 (G-C-24): initialize paper broker's persistent order ID sequences
        # from the stored account configs (see PaperBroker._order_id_sequence).
        paper_broker = brokers.get("paper")
        if paper_broker is not None:
            from app.brokers.paper import PaperBroker
            if isinstance(paper_broker, PaperBroker):
                for account in routing.accounts.values():
                    if account.broker == "paper" and account.paper_order_id_sequence is not None:
                        paper_broker.set_order_id_sequence(account.account_id, account.paper_order_id_sequence)

    def _persist_paper_order_id_sequence(self, result: OrderResult, account: DestinationAccount) -> None:
        """WP-38 (G-C-24): persist the updated paper broker order ID sequence
        after a fill. Only called for paper broker accounts."""
        paper_broker = self.brokers.get("paper")
        if paper_broker is not None:
            from app.brokers.paper import PaperBroker
            if isinstance(paper_broker, PaperBroker):
                seq = paper_broker.get_order_id_sequence(account.account_id)
                self.store.update_account_paper_order_id_sequence(account.account_id, seq)

    @staticmethod
    def _opportunity_id_for(signal: Signal, account: DestinationAccount, single_ids: set[str]) -> str:
        """WC-33/WC-31 opportunity identity for budget reservations and order intents.

        ALLOC-01: a canonical signal selects ONE eligible account, so for the
        single-selection pool the signal id itself is the opportunity and the
        unique opportunity claim is exactly the "never two intents for one
        signal" guarantee. An explicit `delivery_mode="replicate"` destination
        is a deliberate, configured fan-out: each replica account is its own
        opportunity (`<signal_id>:<account_id>`), otherwise the second replica
        would be rejected as a DUPLICATE of the first."""
        if account.account_id in single_ids:
            return signal.id
        return f"{signal.id}:{account.account_id}"

    async def _check_and_reserve_resources(
        self,
        signal_id: str,
        account: DestinationAccount,
        quantity: float,
        signal: Signal,
        broker: BrokerAdapter,
    ) -> tuple[bool, str | None, str | None]:
        """WC-33: Check and reserve hierarchical resources atomically.

        Constructs a budget scope and resource vector for the account/signal/quantity,
        fetches the broker snapshot (equity, margin, buying power), and calls
        HierarchicalBudget.check_and_reserve to ensure all hierarchical levels
        (owner, account, portfolio, sleeve, provider, underlying, cluster) have
        sufficient resources before any broker effect occurs.

        Implementation (WC-33 STEP A):
        - Resolve price using _resolve_price_for_gating (same price used by _try_reserve_capital)
        - Fail closed when price is None and any level has finite limit configured
        - Calculate resource vector: cash = ceil(qty × price × multiplier), notional = cash
        - planned_risk = |price - stop_loss| × qty × multiplier when stop_loss set, else 0
        - initial_margin based on margin_type (cash venues: 0, margin venues: ceil(cash / max_gross_leverage))
        - Call check_and_reserve atomically

        Args:
            signal_id: The opportunity id claimed for deduplication: the signal id for the
                single-selection pool, "<signal_id>:<account_id>" for an explicit replica
                (see `_opportunity_id_for`).
            account: The destination account
            quantity: The calculated order quantity (float)
            signal: The signal being routed
            broker: The broker adapter for this account

        Returns:
            (reserved_ok, error_message, reservation_id) where:
            - reserved_ok=True and reservation_id is set if resources reserved
            - reserved_ok=False with error_message if reservation blocked
            - reservation_id is None if not reserved
        """
        try:
            # WC-33 STEP A: Calculate resource vector from sized order

            # Resolve price using the same logic as _try_reserve_capital
            price, price_source = await self._resolve_price_for_gating(broker, signal)

            # Get contract multiplier
            mult, spec_error, _spec_note = _get_contract_multiplier(signal)
            if spec_error is not None:
                return False, f"resource reservation blocked: {spec_error}", None
            contract_multiplier = mult

            # Fetch broker snapshot (equity, margin, buying power)
            balance = await broker.get_account_balance(account)
            broker_snapshot = None
            if balance is not None:
                # Convert to integer cents, using to_cents (which is exact)
                buying_power_cents = ceil_cents(balance.buying_power) if balance.buying_power is not None else None
                # Fall back to cash if buying_power is not provided
                if buying_power_cents is None and balance.cash is not None:
                    buying_power_cents = ceil_cents(balance.cash)
                equity_cents = ceil_cents(balance.equity) if balance.equity is not None else None
                maintenance_cents = ceil_cents(balance.maintenance_margin) if balance.maintenance_margin is not None else None

                broker_snapshot = BrokerSnapshot(
                    buying_power=buying_power_cents,
                    equity=equity_cents,
                    maintenance=maintenance_cents,
                    reflected_intent_ids=None,  # TODO: populate from active orders
                    as_of=datetime.now(timezone.utc),
                )

            # Construct budget scope (all hierarchical levels)
            scope = BudgetScope(
                owner="owner",  # TODO: derive from config or account metadata
                physical_account_id=account.account_id,
                portfolio_id=None,  # TODO: support multi-portfolio
                sleeve_id=None,  # TODO: support sleeves
                provider=signal.source,
                analyst=signal.analyst,  # Can be None
                underlying=signal.symbol,
                cluster=None,  # TODO: support correlated risk clusters
            )

            # Construct resource vector for the sized entry
            # WC-33 STEP A: Calculate from sized order using integer cents

            # Cash need: ceil(quantity × price × contract_multiplier)
            # Always round UP for conservative cash requirement
            if price is not None and price > 0:
                cash_needed_decimal = Decimal(str(quantity)) * Decimal(str(price)) * Decimal(str(contract_multiplier))
                cash_needed_cents = ceil_cents(cash_needed_decimal)
                notional_cents = cash_needed_cents
            else:
                # No resolvable price: check if any level has finite limit
                # If so, reject; if all unlimited, proceed with cash=0
                cash_needed_cents = 0
                notional_cents = 0

                # Check if any hierarchical level has finite limit
                has_finite_limit = False
                # TODO: query store for owner/account/portfolio/sleeve/provider/underlying limits
                # For now, assume if we reach here and price is None, we can't proceed if there are limits
                if has_finite_limit or account.max_notional_exposure is not None:
                    return False, "no resolvable price for reservation", None

            # Planned risk: |price - stop_loss| × quantity × multiplier (in cents)
            planned_risk_cents = 0
            if signal.stop_loss is not None and signal.stop_loss > 0 and price is not None and price > 0:
                # Calculate planned risk in cents
                risk_per_unit = abs(Decimal(str(price)) - Decimal(str(signal.stop_loss)))
                planned_risk_decimal = risk_per_unit * Decimal(str(quantity)) * Decimal(str(contract_multiplier))
                planned_risk_cents = ceil_cents(planned_risk_decimal)

            # Initial margin calculation
            # For cash venues: 0
            # For margin venues with max_gross_leverage: ceil(cash / max_gross_leverage)
            # TODO: determine margin_type from physical account metadata
            initial_margin_cents = 0
            # NOTE: margin_type is not available on DestinationAccount yet;
            # assuming cash venue for now (initial_margin_cents = 0)

            need = ResourceVector(
                cash=cash_needed_cents,
                buying_power=None,  # Let check_and_reserve validate against broker snapshot
                initial_margin=initial_margin_cents,
                maintenance=None,  # Validated from broker snapshot
                notional=notional_cents,
                planned_risk=planned_risk_cents,
                stress_risk=None,  # TODO: calculate from stress scenarios
                close_quantity=0,  # TODO: track closeable inventory
                slots=1,  # One order slot required
            )

            # ALLOC-07 crash boundaries: a redelivery of the SAME opportunity
            # on the SAME physical account (a restart after a crash between
            # this reservation and the durable command-ledger intent, or a
            # replayed delivery) resumes the reservation it already holds;
            # the command ledger downstream decides whether a broker call may
            # still happen. The budget layer itself stays strict (any second
            # claim is DUPLICATE), so a different account is still refused.
            existing = self.store.get_active_reservation_for_opportunity(signal_id)
            if existing is not None and existing.physical_account_id == account.account_id:
                logger.info(
                    "resource_reservation resumed signal=%s account=%s reservation_id=%s state=%s",
                    signal_id,
                    account.account_id,
                    existing.reservation_id,
                    existing.state,
                )
                return True, None, existing.reservation_id

            # Call check_and_reserve
            reservation_result = self.hierarchical_budget.check_and_reserve(
                opportunity_id=signal_id,
                scope=scope,
                need=need,
                snapshot=broker_snapshot,
            )

            logger.info(
                "resource_reservation signal=%s account=%s ok=%s binding_level=%s reservation_id=%s",
                signal_id,
                account.account_id,
                reservation_result.ok,
                reservation_result.binding_level,
                reservation_result.reservation_id,
            )

            if reservation_result.ok:
                return True, None, reservation_result.reservation_id
            else:
                reason = reservation_result.reason.value if reservation_result.reason else "unknown"
                return False, f"resource reservation blocked: {reason}", None

        except Exception as e:
            logger.exception("resource_reservation failed signal=%s account=%s", signal_id, account.account_id)
            return False, f"resource reservation check failed: {str(e)}", None

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
        # WP-38 (G-C-13): use per-account evidence class, fall back to global config
        evidence_class_str = account.evidence_class or config.RELAY_EVIDENCE_CLASS
        return build_execution_applied_envelope(
            result,
            account=account,
            symbol=symbol,
            side=side,
            asset_class=asset_class,
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[evidence_class_str],
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
        # WP-38 (G-C-13): use per-account evidence class when available, fall back to global
        evidence_class_str = (account.evidence_class if account is not None else None) or config.RELAY_EVIDENCE_CLASS
        envelope = build_routing_admission_outcome_envelope(
            signal,
            outcome=outcome,
            source_stream=source_stream,
            export_sequence=self.store.next_export_sequence(source_stream),
            producer_id=config.RELAY_PRODUCER_ID,
            evidence_class=EvidenceClass[evidence_class_str],
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

    async def handle_signal(self, signal: Signal, dry_run: bool = False) -> list[OrderResult]:
        """C22: binds correlation fields (signal_id, source, symbol, side)
        onto every structlog call made anywhere during this signal's
        processing (see app/logging_config.py's bind_signal_context) --
        deliberately independent of this codebase's existing plain stdlib
        `logging.getLogger(__name__)` calls, which are unaffected either
        way. The actual routing/sizing/submission logic lives in
        _handle_signal below, unchanged.

        WC-20 STEP 5: When dry_run=True, calculates all effects but stops
        before any broker call, records decision traces, and returns results
        without placing orders. Used for pre-flight checks and simulation."""
        with bind_signal_context(
            signal_id=signal.id, source=signal.source, symbol=signal.symbol, side=signal.side.value
        ):
            structured_logger.info("signal_received", quantity=signal.quantity, analyst=signal.analyst, dry_run=dry_run)
            results = await self._handle_signal(signal, dry_run=dry_run)
            structured_logger.info(
                "signal_processed",
                destination_count=len(results),
                statuses=[r.status.value for r in results],
                dry_run=dry_run,
            )
            return results

    @staticmethod
    def _protective_level_error(signal: Signal) -> str | None:
        """Why this entry's stop/target sit on the wrong side of its entry price, or None.

        Only checked when the entry price is known and the direction is
        unambiguous (ENTRY_LONG, ENTRY_SHORT, or ADD on the buy side)."""
        price = signal.price
        if price is None or price <= 0:
            return None
        if signal.intent == Intent.ENTRY_LONG or (signal.intent == Intent.ADD and signal.side == Side.BUY):
            is_long = True
        elif signal.intent == Intent.ENTRY_SHORT:
            is_long = False
        else:
            return None
        problems = []
        stop, target = signal.stop_loss, signal.take_profit
        if stop is not None and stop > 0:
            if is_long and stop >= price:
                problems.append(f"stop_loss {stop} must be below the entry price {price} for a long entry")
            if not is_long and stop <= price:
                problems.append(f"stop_loss {stop} must be above the entry price {price} for a short entry")
        if target is not None and target > 0:
            if is_long and target <= price:
                problems.append(f"take_profit {target} must be above the entry price {price} for a long entry")
            if not is_long and target >= price:
                problems.append(f"take_profit {target} must be below the entry price {price} for a short entry")
        return "; ".join(problems) or None

    async def _account_capital_exhausted(self, account: DestinationAccount) -> bool:
        """True only when the broker reports capital for this account and, after
        netting capital held by unfilled reservations, nothing is left.

        Deliberately False for a missing adapter or an unreported balance: those
        already fail closed downstream with their own specific reasons (missing
        adapter -> ERROR; no buying_power and no ceiling -> refusing entry), and
        masking them with a generic budget reason would hide the real cause."""
        broker = self.brokers.get(account.broker)
        if broker is None or not broker.has_balance_capability:
            return False
        try:
            balance = await broker.get_account_balance(account)
        except Exception:
            return False
        if balance is None:
            return False
        capital = balance.buying_power if balance.buying_power is not None else balance.cash
        if capital is None:
            return False
        capital_cents = int(Decimal(str(capital)) * 100)
        reserved_cents = self.store.reserved_capital_cents(account.account_id)
        return capital_cents - reserved_cents <= 0

    async def _derive_admission_inputs(self, signal: Signal, single_candidates: list) -> AdmissionInputs:
        """WC-32: Derive real admission inputs evaluated per candidate.

        For each candidate account, determines halt status, margin regime, uncertain
        effect, and budget state. Per-candidate exclusions are recorded as decision
        traces. Returns AdmissionInputs with only eligible candidates remaining.

        Args:
            signal: The signal being processed.
            single_candidates: List of DestinationAccount candidates.

        Returns:
            AdmissionInputs with real values computed from store/broker state.
        """
        # Authorization and interpretation are signal-wide (not per-candidate)
        auth = "authorized" if self.routing.pool_for(signal.source, signal.symbol) is not None else "unauthorized"
        interp = "entry"

        # Per-candidate evaluation: filter out blocked candidates and record traces
        eligible_accounts = []
        excluded_candidates = []

        for rank, account in enumerate(single_candidates):
            excluded_reasons = []
            physical_account_id = account.account_id

            # Check halt status (account, portfolio, owner)
            # 1. Check account halt
            account_halt = self.store.active_halt_for("account", physical_account_id)
            if account_halt:
                excluded_reasons.append(f"HALTED:{account_halt.get('reason', 'account halt')}")

            # 2. Check portfolio halt (look up portfolio_id from portfolio_backings)
            if not account_halt:
                # Find portfolio for this physical_account_id
                portfolio_row = None
                try:
                    with self.store._connect() as conn:
                        portfolio_row = conn.execute(
                            "SELECT portfolio_id FROM portfolio_backings WHERE physical_account_id = ? LIMIT 1",
                            (physical_account_id,)
                        ).fetchone()
                except Exception:
                    pass  # No portfolio backing found

                if portfolio_row:
                    portfolio_id = portfolio_row[0]
                    portfolio_halt = self.store.active_halt_for("portfolio", portfolio_id)
                    if portfolio_halt:
                        excluded_reasons.append(f"HALTED:{portfolio_halt.get('reason', 'portfolio halt')}")

            # 3. Check owner halt
            if not excluded_reasons:
                owner_halt = self.store.active_halt_for("owner", "owner")
                if owner_halt:
                    excluded_reasons.append(f"HALTED:{owner_halt.get('reason', 'owner halt')}")

            # 4. Check daily loss limit breach (sets a halt if breached)
            if not excluded_reasons:
                daily_loss_limit_pct = account.daily_loss_limit_percent or config.DEFAULT_DAILY_LOSS_LIMIT_PERCENT
                if daily_loss_limit_pct:
                    loss_check_error = await self.daily_loss_limiter.check_daily_loss_limit(account, daily_loss_limit_pct)
                    if loss_check_error:
                        # Persist a halt for this account
                        self.store.set_trading_halt("account", physical_account_id, loss_check_error, source="daily_loss_limiter")
                        excluded_reasons.append(f"HALTED:{loss_check_error}")

            # Check margin regime (all applicable reasons are collected, not just the first)
            regime_row = self.store.get_margin_regime(physical_account_id)
            if regime_row:
                regime = regime_row["regime"]
            else:
                # Determine regime based on broker environment
                try:
                    broker = self.brokers.get(account.broker)
                    if broker:
                        env = broker.venue_environment(account)
                        if env == "live":
                            # A live venue with no declared regime blocks
                            # new exposure (spec I17, §9).
                            regime = "unknown"
                        else:
                            # paper/sandbox carry no PDT/intraday regime.
                            # An adapter that cannot name its environment
                            # ("unknown") is kept off live routes by the
                            # WP-33 environment-qualification gate, not by
                            # relabelling it as a margin-regime block.
                            regime = f"not_applicable_{env}"
                    else:
                        # No adapter registered for this account's broker:
                        # the regime cannot be evaluated at all, and the
                        # entry path below reports the missing adapter as
                        # an ERROR (its existing contract). Do not relabel
                        # a configuration error as a margin-regime block.
                        regime = "not_evaluated_no_adapter"
                except Exception:
                    regime = "unknown"

            if regime == "unknown":
                excluded_reasons.append("REGIME_UNKNOWN")

            # Check uncertain effect (unresolved command ledger entries)
            unresolved = self.store.list_unresolved_command_ledger_entries(account.account_id)
            # Spec §6.3: an UNCERTAIN effect is a submission whose broker
            # outcome is genuinely unknown (UNKNOWN_AMBIGUOUS, or a
            # PENDING_SUBMISSION row with no response yet). A
            # SUBMITTED_UNCONFIRMED row is a KNOWN accepted order with a
            # broker id: its exposure is already held by the capital
            # reservation, so it is not an uncertain effect.
            for entry in unresolved:
                if entry.command_type in (CommandType.ENTRY, CommandType.CLOSE) and entry.uncertainty_state in (
                    UncertaintyState.UNKNOWN_AMBIGUOUS,
                    UncertaintyState.PENDING_SUBMISSION,
                ):
                    excluded_reasons.append("UNCERTAIN_EFFECT")
                    break

            # Check budget state
            scope = BudgetScope(
                owner="owner",  # Default owner id (may be overridden by config)
                physical_account_id=physical_account_id,
                portfolio_id=None,
                sleeve_id=None,
                provider=signal.source,
                analyst=signal.analyst,
                underlying=signal.symbol,
                cluster=None,
            )

            try:
                remaining_dict = self.hierarchical_budget.remaining(scope)
                # Check if any level has zero or negative remaining cents
                for _level, remaining_cents in remaining_dict.items():
                    if remaining_cents is not None and remaining_cents <= 0:
                        excluded_reasons.append("BUDGET_NOT_ADMISSIBLE")
                        break

                # A budget is never unlimited. With no finite configured limit at
                # any level (the store reports 2**62 minus usage there) and no
                # account ceiling, the bound is the account's available capital:
                # buying power (cash plus margin on a margin account) or cash.
                # Exhausted capital blocks here; unreported capital is refused by
                # the order-time buying-power gate with its own specific reason.
                unbounded = all(
                    c is None or c >= UNLIMITED_CENTS // 2 for c in remaining_dict.values()
                )
                has_account_ceiling = (
                    account.max_notional_exposure is not None
                    or account.risk_percent_of_equity is not None
                )
                if (
                    "BUDGET_NOT_ADMISSIBLE" not in excluded_reasons
                    and unbounded
                    and not has_account_ceiling
                    and await self._account_capital_exhausted(account)
                ):
                    excluded_reasons.append("BUDGET_NOT_ADMISSIBLE")
            except Exception:
                excluded_reasons.append("BUDGET_NOT_ADMISSIBLE")

            # Record decision trace for this candidate
            if excluded_reasons:
                # Candidate is excluded
                reason_str = "|".join(excluded_reasons)
                self.store.insert_decision_trace(
                    signal_id=signal.id,
                    physical_account_id=physical_account_id,
                    candidate_rank=rank,
                    feasible=False,
                    reason=reason_str,
                    selected=False,
                )
                excluded_candidates.append((account, reason_str))
            else:
                # Candidate is eligible
                eligible_accounts.append(account.account_id)

        # Keep the per-candidate detail for the operator-facing rejection
        # message (the gate itself only sees the reason codes).
        self._last_admission_exclusions = [
            f"{account.account_id}: {reason_str}" for account, reason_str in excluded_candidates
        ]

        # Determine overall values for the admission gate.
        # The gate blocks only if NO candidates remain (all excluded) with a given blocking reason.
        overall_halt = "clear"
        overall_regime = "legacy_pdt_verified"
        overall_uncertain = False
        overall_budget = "enough"

        # Only set blocking reasons if all candidates are excluded
        if not eligible_accounts:
            for _, reason_str in excluded_candidates:
                if "HALTED" in reason_str:
                    overall_halt = "account_halt"
                if "REGIME_UNKNOWN" in reason_str:
                    overall_regime = "unknown"
                if "UNCERTAIN_EFFECT" in reason_str:
                    overall_uncertain = True
                if "BUDGET_NOT_ADMISSIBLE" in reason_str:
                    overall_budget = "not_enough"

        # NO_ELIGIBLE_ROUTE means no candidate existed at all. When candidates
        # existed but every one was excluded, the real exclusion reasons are
        # the blockers; hand the gate the original candidate ids so it does
        # not add a misleading NO_ELIGIBLE_ROUTE on top of them.
        gate_candidates = eligible_accounts or [a.account_id for a in single_candidates]

        return AdmissionInputs(
            authorization=auth,
            interpretation=interp,
            eligible_physical_accounts=gate_candidates,
            budget_state=overall_budget,
            margin_regime=overall_regime,
            halt=overall_halt,
            uncertain_effect=overall_uncertain,
        )

    async def _handle_signal(self, signal: Signal, dry_run: bool = False) -> list[OrderResult]:
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
        # ALLOC-01: orders that exist only because candidate accounts were
        # rejected BEFORE submission (intent still 'claimed'/'selected')
        # are not a completed decision -- resume the allocation instead of
        # replaying a partial one. A committed/skipped intent (or a signal
        # with no intent: CLOSE / replicate-only) replays exactly as before.
        prior_intent = self.store.get_allocation_intent(signal.id)
        if already_processed and (prior_intent is None or prior_intent["state"] in ("committed", "skipped")):
            logger.info(
                "signal id=%s already produced %d order result(s); replaying them instead of "
                "re-submitting to every destination",
                signal.id,
                len(already_processed),
            )
            return [_order_result_from_row(row) for row in already_processed]

        # WP-11 (A-02/A-11): Edit and delete handling -- detect when this
        # signal is an edit/revision of an earlier message and handle
        # amendment instead of placing new entries.
        if signal.original_message_id is not None:
            edit_results = await self._handle_signal_edit(signal)
            if edit_results is not None:
                return edit_results

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
        #
        # ALLOC-01: for an ENTRY, `single`-mode rules' destinations are
        # ALTERNATIVES for ONE intended trade (priority order). Exactly one
        # is selected, BEFORE submission, under a durable allocation intent
        # created before any account-specific execution. `replicate`-mode
        # destinations are the only explicit fan-out. A CLOSE keeps its
        # account-scoped behavior (each account exits only what it owns).
        single_ids: set[str] = set()
        allocation_intent: dict | None = None
        results: list[OrderResult] = []
        if signal.side == Side.CLOSE:
            destinations = self.routing.destinations_for(signal.source, signal.symbol, include_disabled=True)
        else:
            pool = self.routing.pool_for(signal.source, signal.symbol, include_disabled=False)
            single_candidates = pool.single
            replicate_candidates = [a for a in pool.replicate if a.account_id not in {c.account_id for c in single_candidates}]

            # WC-20 step 1: Identity collapse (I02) + decision traces
            # For entry intents, collapse duplicate bindings to the same physical account
            if single_candidates and signal.side != Side.CLOSE:
                # Build mapping: physical_account_id -> first config_account
                physical_to_config: dict[str, DestinationAccount] = {}
                all_candidates = []  # For decision_traces

                for config_account in sorted(single_candidates, key=lambda a: a.account_id):
                    # Look up binding for this config account
                    binding = self.store.get_binding_for_config_account(config_account.account_id)
                    if binding is not None:
                        physical_account_id = binding["physical_account_id"]
                    else:
                        # No binding row: config account is its own physical account
                        physical_account_id = config_account.account_id

                    all_candidates.append({
                        "config_account": config_account,
                        "physical_account_id": physical_account_id,
                    })

                    # Keep first by stable order
                    if physical_account_id not in physical_to_config:
                        physical_to_config[physical_account_id] = config_account

                # Persist decision_traces for all candidates (even duplicates)
                for rank, candidate in enumerate(all_candidates):
                    is_selected = candidate["physical_account_id"] in physical_to_config and \
                                  physical_to_config[candidate["physical_account_id"]].account_id == candidate["config_account"].account_id
                    self.store.insert_decision_trace(
                        signal_id=signal.id,
                        physical_account_id=candidate["physical_account_id"],
                        candidate_rank=rank,
                        feasible=True,  # Initial; will be updated after sizing
                        reason="initial_candidate",
                        selected=is_selected,
                    )

                # Replace single_candidates with deduplicated list (keep first by account_id)
                single_candidates = [physical_to_config[pid] for pid in sorted(physical_to_config.keys())]

            # WC-20 step 2: Admission evaluation
            # Evaluate if entry is admissible before sizing
            # The gate guards NEW ENTRIES only. Intents that manage an existing
            # position (EXIT/REDUCE/STOP_UPDATE/TARGET_UPDATE/CANCEL) are handled
            # by their own per-account handlers below and are not entries, so
            # they bypass this gate rather than being rejected by it. Intent.SELL
            # is ambiguous (exit-or-short-entry, resolved per account in WP-09)
            # and ADD is an entry-sized order, so both are admitted as entries.
            _entry_like_intents = (Intent.ENTRY_LONG, Intent.ENTRY_SHORT, Intent.SELL, Intent.ADD)

            # A protective level on the wrong side of the entry price is not a
            # protection: a long's stop above its entry (or target below it)
            # would trigger the moment the position opens. Reject before any
            # sizing, reservation or broker call.
            protective_error = self._protective_level_error(signal)
            if protective_error is not None and signal.side != Side.CLOSE:
                for account in [*single_candidates, *replicate_candidates]:
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message=f"invalid protective levels: {protective_error}",
                    )
                    self.store.save_order_result(result, purpose="entry", family_id=signal.id)
                    results.append(result)
                    self._export_routing_outcome(
                        signal,
                        outcome="rejected",
                        account=account,
                        order_status=result.status,
                        message=result.message,
                    )
                if results:
                    return results

            # Check if allocation intent already exists (recovery/replay scenario)
            # This must be checked BEFORE the admission gate, since recovered intents
            # bypass the gate (the risk was already accepted in the earlier run).
            existing_allocation = None
            if single_candidates and signal.side != Side.CLOSE and signal.intent in _entry_like_intents:
                existing_allocation = self.store.get_allocation_intent(signal.id)

            # Apply admission gate only for NEW entries (no existing allocation intent)
            if single_candidates and signal.side != Side.CLOSE and signal.intent in _entry_like_intents and existing_allocation is None:
                # WC-32: Derive real admission inputs from store/broker state
                admission_inputs = await self._derive_admission_inputs(signal, single_candidates)
                admission_decision = evaluate_admission(admission_inputs)

                # Save original candidates before filtering (needed if all are excluded)
                original_candidates = single_candidates

                # Filter single_candidates to only include eligible candidates
                single_candidates = [
                    a for a in single_candidates
                    if a.account_id in admission_inputs.eligible_physical_accounts
                ]

                # If not admitted, reject all candidates
                if not admission_decision.admit_new_entry:
                    blocking_reasons_str = ", ".join(admission_decision.blocking_reasons)
                    logger.info(
                        "entry rejected by admission gate for signal=%s: %s",
                        signal.id,
                        blocking_reasons_str,
                    )
                    # Traces already persisted from identity collapse step
                    # Reject all original candidates (not just filtered ones)
                    exclusion_detail = "; ".join(getattr(self, "_last_admission_exclusions", []) or [])
                    rejection_message = f"admission rejected: {blocking_reasons_str}"
                    if exclusion_detail:
                        rejection_message += f" -- {exclusion_detail}"
                    for account in original_candidates:
                        result = OrderResult(
                            account_id=account.account_id,
                            status=OrderStatus.REJECTED,
                            signal_id=signal.id,
                            message=rejection_message,
                        )
                        self.store.save_order_result(result, purpose="entry", family_id=signal.id)
                        results.append(result)
                        self._export_routing_outcome(
                            signal,
                            outcome="rejected",
                            account=account,
                            order_status=result.status,
                            message=result.message,
                        )
                    return results

            if single_candidates:
                allocation_intent = self.store.claim_allocation_intent(
                    signal.id,
                    strategy_key=signal.source,
                    symbol=signal.symbol,
                    side=signal.side.value,
                    candidates=[a.account_id for a in single_candidates],
                )
                bound = allocation_intent["selected_account_id"]
                if bound is not None:
                    # Already bound (restart, duplicate delivery, another
                    # worker): this intent may only ever use that account.
                    single_candidates = [a for a in single_candidates if a.account_id == bound]
                    if not single_candidates:
                        # The bound account left the approved pool (config
                        # edit). Do NOT pick another: report and stop.
                        logger.warning(
                            "allocation intent %s bound to %s which is no longer in the approved pool; "
                            "not re-selecting",
                            allocation_intent["intent_id"],
                            bound,
                        )
                single_ids = {a.account_id for a in single_candidates}
            destinations = [*single_candidates, *replicate_candidates]
        if not destinations:
            logger.info("no destinations configured for source=%s symbol=%s", signal.source, signal.symbol)
            if allocation_intent is not None:
                self.store.skip_allocation_intent(signal.id, reason="no permitted destination in the approved pool")
            self._export_routing_outcome(signal, outcome="not_routed")
            return []

        committed_account_id: str | None = (
            allocation_intent["selected_account_id"]
            if allocation_intent is not None and allocation_intent["state"] in ("selected", "committed")
            else None
        )
        for raw_account in destinations:
            if raw_account.account_id in single_ids and committed_account_id is not None and (
                raw_account.account_id != committed_account_id
            ):
                continue
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

            # WP-09: Resolve SELL signals against the account's book
            # When intent is SELL (ambiguous exit), check if the account holds a
            # same-symbol LONG. If yes, convert to EXIT. If no, check allow_short.
            working_signal = signal
            if signal.intent == Intent.SELL:
                symbol = symbol_for_account(signal, account)
                # Check for open position: plain or managed
                position_quantity = self.store.get_position(account.account_id, symbol)
                existing_lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
                has_managed_lifecycle = (
                    existing_lifecycle is not None
                    and existing_lifecycle.plan.side == Side.BUY
                )

                if position_quantity is not None and position_quantity > 0:
                    # Account holds a LONG: treat as EXIT
                    working_signal = replace(working_signal, side=Side.CLOSE)
                elif has_managed_lifecycle:
                    # Managed account with open BUY lifecycle: treat as EXIT
                    working_signal = replace(working_signal, side=Side.CLOSE)
                elif account.allow_short:
                    # No long and shorts allowed: convert to ENTRY_SHORT
                    working_signal = replace(working_signal, intent=Intent.ENTRY_SHORT)
                else:
                    # No long and shorts disallowed: REJECTED
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message=f"sell on account '{account.account_id}' with no long position and allow_short=false",
                    )
                    self.store.save_order_result(result, purpose="entry", family_id=signal.id)
                    results.append(result)
                    self._export_routing_outcome(
                        signal,
                        outcome="rejected",
                        account=account,
                        order_status=result.status,
                        message=result.message,
                    )
                    continue

            # WP-09: Reject explicit ENTRY_SHORT on accounts with allow_short=False
            if working_signal.intent == Intent.ENTRY_SHORT and not account.allow_short:
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=f"entry short on account '{account.account_id}' with allow_short=false",
                )
                self.store.save_order_result(result, purpose="entry", family_id=signal.id)
                results.append(result)
                self._export_routing_outcome(
                    signal,
                    outcome="rejected",
                    account=account,
                    order_status=result.status,
                    message=result.message,
                )
                continue

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
            order_purpose = "close" if working_signal.side == Side.CLOSE else "entry"
            order_family_id: str | None = signal.id if order_purpose == "entry" else None

            broker = self.brokers.get(account.broker)
            if broker is None:
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"no broker adapter registered for '{account.broker}'",
                )
                mult, _, _ = contract_multiplier(signal)
                self.store.save_order_result(result, purpose=order_purpose, family_id=order_family_id, contract_multiplier=mult)
                results.append(result)
                self._export_routing_outcome(
                    signal,
                    outcome=_OUTCOME_BY_ORDER_STATUS[result.status],
                    account=account,
                    order_status=result.status,
                    message=result.message,
                )
                continue

            # WP-13: Handle STOP_UPDATE and TARGET_UPDATE intents before entry/close routing
            if signal.intent == Intent.STOP_UPDATE or signal.intent == Intent.TARGET_UPDATE:
                symbol = symbol_for_account(signal, account)

                if signal.intent == Intent.STOP_UPDATE:
                    lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
                    if lifecycle is not None:
                        result = await self.lifecycle_manager.update_stop_price(account, symbol, signal.stop_loss, signal_id=signal.id)
                    else:
                        result = OrderResult(
                            account_id=account.account_id,
                            status=OrderStatus.REJECTED,
                            signal_id=signal.id,
                            message=f"stop update needs a managed lifecycle for {symbol} on {account.account_id}",
                        )
                    self.store.save_order_result(
                        result,
                        broker=account.broker,
                        symbol=symbol,
                        side=signal.side,
                        purpose="stop_update",
                        family_id=None,
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

                if signal.intent == Intent.TARGET_UPDATE:
                    lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
                    if lifecycle is not None:
                        result = await self.lifecycle_manager.update_targets(account, symbol, signal.targets, signal_id=signal.id)
                    else:
                        result = OrderResult(
                            account_id=account.account_id,
                            status=OrderStatus.REJECTED,
                            signal_id=signal.id,
                            message=f"target update needs a managed lifecycle for {symbol} on {account.account_id}",
                        )
                    self.store.save_order_result(
                        result,
                        broker=account.broker,
                        symbol=symbol,
                        side=signal.side,
                        purpose="target_update",
                        family_id=None,
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

            if working_signal.side != Side.CLOSE:
                # Track 1b: refuse a live ENTRY before anything else broker/
                # asset-class-specific is even checked -- see
                # `_check_route_qualified`'s own docstring for exactly what
                # this gates, why CLOSE is exempt, and why PAPER accounts
                # are exempt.
                route_qualified, qualification_rejection = self._check_route_qualified(account, working_signal, broker)
                if not route_qualified:
                    assert qualification_rejection is not None
                    mult, _, _ = contract_multiplier(signal)
                    self.store.save_order_result(
                        qualification_rejection,
                        broker=account.broker,
                        purpose=order_purpose,
                        family_id=order_family_id,
                        contract_multiplier=mult,
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

            # Daily loss limit check: refuse an ENTRY if account has breached
            # its daily loss ceiling (circuit breaker, fail-closed). CLOSE
            # signals bypass this to allow closing/hedging after loss limits hit.
            if working_signal.side != Side.CLOSE:
                daily_loss_limit_percent = account.daily_loss_limit_percent or config.DEFAULT_DAILY_LOSS_LIMIT_PERCENT
                daily_loss_error = await self.daily_loss_limiter.check_daily_loss_limit(account, daily_loss_limit_percent)
                if daily_loss_error is not None:
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message=daily_loss_error,
                    )
                    mult, _, _ = contract_multiplier(signal)
                    self.store.save_order_result(
                        result,
                        broker=account.broker,
                        purpose=order_purpose,
                        family_id=order_family_id,
                        contract_multiplier=mult,
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

            # E04 (bounded): margin call detection and alert persistence. Check if
            # account has breached maintenance requirements, and record alert if so.
            # This is a fail-closed gate: if margin state CAN be determined but
            # indicates a margin call, we reject the signal. If margin data is
            # unavailable (all None values), we pass through, as the broker adapter
            # hasn't integrated margin state reporting yet. Only check for entry
            # signals; CLOSE signals are allowed through to permit hedging.
            if working_signal.side != Side.CLOSE:
                # Check for unresolved margin call alerts first. If any exist,
                # block new entries to prevent trading on a margin-call account.
                unresolved_alerts = self.margin_call_detector.get_unresolved_margin_calls(
                    account.account_id
                )
                if unresolved_alerts:
                    alert_ids = [a["id"] for a in unresolved_alerts]
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message=f"Cannot trade: account has {len(unresolved_alerts)} unresolved margin call alert(s) "
                        f"(ids: {alert_ids}). Resolve margin call(s) before entering new positions.",
                    )
                    mult, _, _ = contract_multiplier(signal)
                    self.store.save_order_result(
                        result,
                        broker=account.broker,
                        purpose=order_purpose,
                        family_id=order_family_id,
                        contract_multiplier=mult,
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

                # Feed balance.equity and balance.maintenance_margin from get_account_balance
                # into the detector. Fail closed when a margin account reports equity but no
                # maintenance figure.
                balance = await broker.get_account_balance(account)
                current_equity = None
                maintenance_requirement = None

                if balance is not None:
                    current_equity = balance.equity
                    maintenance_requirement = balance.maintenance_margin

                    # A balance with equity but no maintenance figure is an
                    # adapter/account that does not track margin (cash
                    # accounts, the paper simulator): the margin-call gate
                    # cannot run and says so; it does NOT reject, because the
                    # buying-power, loss-limit and exposure gates still apply.
                    # The readiness checklist reports "margin: not_tracked".
                    if current_equity is not None and maintenance_requirement is None:
                        logger.info(
                            "margin_check_skipped account=%s broker=%s reason=maintenance_margin_not_reported",
                            account.account_id,
                            account.broker,
                        )

                # Check margin state with the detector only when the venue
                # reports a maintenance figure; without one there is no margin
                # state to evaluate (see the skip above).
                margin_error = (
                    self.margin_call_detector.check_and_persist_margin_call(
                        account=account,
                        current_equity=current_equity,
                        maintenance_requirement=maintenance_requirement,
                        excess_margin=None,  # Detector will calculate if needed
                        broker=account.broker,
                    )
                    if maintenance_requirement is not None
                    else None
                )
                if margin_error is not None:
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message=margin_error,
                    )
                    mult, _, _ = contract_multiplier(signal)
                    self.store.save_order_result(
                        result,
                        broker=account.broker,
                        purpose=order_purpose,
                        family_id=order_family_id,
                        contract_multiplier=mult,
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

            # E09 (bounded): account liquidation circuit breaker. Check if account
            # equity meets minimum threshold. This is a fail-closed gate: if equity
            # is below threshold or cannot be determined, we reject the signal to
            # prevent liquidation. Only check for entry signals; CLOSE signals are
            # allowed through to permit position reduction.
            if working_signal.side != Side.CLOSE:
                min_equity_threshold = account.min_equity_threshold
                if min_equity_threshold is not None:
                    liquidation_error = await self.daily_loss_limiter.check_min_equity_threshold(
                        account, min_equity_threshold
                    )
                    if liquidation_error is not None:
                        result = OrderResult(
                            account_id=account.account_id,
                            status=OrderStatus.REJECTED,
                            signal_id=signal.id,
                            message=liquidation_error,
                        )
                        mult, _, _ = contract_multiplier(signal)
                        self.store.save_order_result(
                            result,
                            broker=account.broker,
                            purpose=order_purpose,
                            family_id=order_family_id,
                            contract_multiplier=mult,
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
                mult, _, _ = contract_multiplier(signal)
                self.store.save_order_result(
                    result, broker=account.broker, purpose=order_purpose, family_id=order_family_id, contract_multiplier=mult
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

            # C-01: Reject LIMIT/STOP entries for adapters that don't support them
            # (fail-closed). Until an adapter implements and declares limit/stop
            # support, every entry must be MARKET (the default when unspecified).
            if working_signal.side != Side.CLOSE and not broker.can_trade_entry_order_type(working_signal.entry_order_type):
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=(
                        f"broker '{account.broker}' does not support entry_order_type="
                        f"'{working_signal.entry_order_type.value if working_signal.entry_order_type else 'market'}' — refusing to route this signal here"
                    ),
                )
                mult, _, _ = contract_multiplier(signal)
                self.store.save_order_result(
                    result, broker=account.broker, purpose=order_purpose, family_id=order_family_id, contract_multiplier=mult
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

            # D-07/F-01: for CLOSE signals, determine the exit path by
            # lifecycle existence, not by the managed_lifecycle flag.
            # If a lifecycle exists, it must be closed through the managed
            # path. Otherwise, use the plain path. This prevents orphaning
            # protective stops or stranding positions when the flag is
            # changed mid-position.
            use_managed_path = account.managed_lifecycle
            if working_signal.side == Side.CLOSE:
                existing_lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
                use_managed_path = existing_lifecycle is not None

            if use_managed_path:
                existing_lifecycle = None
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
                managed_bound = False
                if order_purpose == "entry" and account.account_id in single_ids:
                    if not self.store.bind_allocation_intent(signal.id, account.account_id):
                        logger.info("allocation intent for signal=%s is bound elsewhere; skipping account=%s", signal.id, account.account_id)
                        continue
                    managed_bound = True
                managed_outcome = await self._handle_managed_signal(
                    working_signal,
                    account,
                    symbol,
                    dry_run=dry_run,
                    opportunity_id=self._opportunity_id_for(signal, account, single_ids),
                )
                result = managed_outcome.result
                if managed_bound:
                    if managed_outcome.submitted_at is None:
                        # Rejected before ever reaching a broker: no
                        # exposure exists, so the next approved
                        # alternative may be tried.
                        self.store.release_allocation_binding(signal.id, account.account_id)
                    else:
                        committed_account_id = account.account_id
                # TRK-23: the audit's HIGH-severity gap -- a managed-
                # lifecycle fill never built/exported an EXECUTION_APPLIED
                # envelope at all (only its own internal `orders` row via
                # TRK-22's fields above), leaving signal-portfolio-
                # commercial's `Book.PLATFORM` ledger silently missing the
                # majority of real trading activity. `side` must be the
                # resolved BUY/SELL actually sent to the broker, never
                # `Side.CLOSE` (see `build_execution_applied_envelope`'s
                # own docstring) -- for an entry, `working_signal.side` already is
                # that (a CLOSE signal never reaches this branch as an
                # entry); for a close, `existing_lifecycle.exit_side`
                # (captured above, BEFORE a fully-flattening close can
                # delete this lifecycle's state) is the only real resolved
                # side available here -- mirrors `close_position`'s own
                # `resolved_side` for the identical reason. `None` (no
                # envelope built) when there's no lifecycle to resolve a
                # close's side from at all -- exactly the "no open position
                # to close" rejection, which never reaches FILLED anyway,
                # so `_build_export_envelope` would have returned `None`
                # regardless.
                export_side = (
                    working_signal.side
                    if order_purpose != "close"
                    else (existing_lifecycle.exit_side if existing_lifecycle is not None else None)
                )
                export_envelope = (
                    self._build_export_envelope(
                        result,
                        account=account,
                        symbol=symbol,
                        side=export_side,
                        asset_class=(
                            existing_lifecycle.plan.asset_class
                            if existing_lifecycle is not None
                            else signal.asset_class
                        ),
                        originating_source_event_id=signal.id,
                        originating_analyst_id=signal.analyst,
                    )
                    if export_side is not None
                    else None
                )
                # TRK-22: threads AUD-01's distinct-field quantity model
                # through for a managed-lifecycle order -- previously this
                # call never passed applied_quantity/confirmed_cumulative_
                # fill/applied_execution_delta/outstanding_possible_fill/
                # acknowledged_quantity at all, leaving every managed
                # order's `orders.applied_execution_delta` permanently NULL
                # (see `_ManagedOrderOutcome`'s own docstring for exactly
                # what each field means for a managed order and why). Same
                # kwarg shape as `_submit_order`'s own callers use for the
                # plain-account path.
                self.store.save_order_result(
                    result,
                    broker=account.broker,
                    symbol=symbol,
                    # The journal must carry the resolved BUY/SELL (the same
                    # value the export already uses): a `close` side is
                    # skipped by every replay, which blanks realized P&L,
                    # marks the symbol unresolved and locks the capital gate.
                    side=export_side if export_side is not None else working_signal.side,
                    requested_quantity=None,
                    applied_quantity=managed_outcome.applied_quantity,
                    confirmed_cumulative_fill=managed_outcome.confirmed_cumulative_fill,
                    applied_execution_delta=managed_outcome.applied_execution_delta,
                    outstanding_possible_fill=managed_outcome.outstanding_possible_fill,
                    acknowledged_quantity=managed_outcome.acknowledged_quantity,
                    export_envelope=export_envelope,
                    submitted_at=managed_outcome.submitted_at,
                    protection_confirmed_at=managed_outcome.protection_confirmed_at,
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

            if working_signal.side == Side.CLOSE:
                # DB-0X: a plain (non-managed_lifecycle) account has no
                # tracked lifecycle object linking this close back to
                # whichever entry fill(s) produced the position it's
                # closing -- `_resolve_and_submit_plain_close` itself saves
                # this order with purpose='close' and family_id=None (the
                # honest default already set above), not re-derived here.
                result = await self._resolve_and_submit_plain_close(working_signal, account, symbol, broker)
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

            try:
                # WC-20 STEP 3: Sizing modes (fixed/multiplier/risk_fraction)
                # Fetch equity and buying_power if needed (required for risk_fraction mode)
                equity = None
                buying_power = None
                if account.sizing_mode == "risk_fraction":
                    balance = await broker.get_account_balance(account)
                    if balance is not None:
                        equity = balance.equity
                        buying_power = balance.buying_power

                # Apply sizing mode and get quantity
                quantity_result, sizing_error = size_for_account_with_mode(
                    working_signal, account, equity, buying_power
                )
                if sizing_error is not None:
                    raise UnsizedEntryError(sizing_error)
                if quantity_result is None:
                    raise UnsizedEntryError("sizing returned None without error message")

                order_signal, quantity = working_signal, quantity_result

                # WP-17 (B-04): normalize quantity to venue precision after sizing
                normalized_qty = broker.normalize_quantity(account, symbol, quantity)
                if normalized_qty is None:
                    raise UnsizedEntryError(f"quantity step unknown for {symbol} on {account.broker}")
                if normalized_qty <= 0:
                    raise UnsizedEntryError(
                        f"quantity {quantity} rounds to {normalized_qty:.8g} below venue minimum for {symbol}"
                    )
                quantity = normalized_qty
            except UnsizedEntryError as e:
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=str(e),
                )
                self.store.save_order_result(
                    result,
                    broker=account.broker,
                    symbol=symbol,
                    side=working_signal.side,
                    requested_quantity=None,
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

            if account.account_id in single_ids:
                # ALLOC-01 commit point: bind this intent to ONE account
                # before the durable command-ledger intent and the broker
                # call. If another worker/delivery already bound a
                # different account, this one must not execute.
                if not self.store.bind_allocation_intent(signal.id, account.account_id):
                    self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
                    logger.info(
                        "allocation intent for signal=%s is bound elsewhere; not executing on account=%s",
                        signal.id,
                        account.account_id,
                    )
                    continue
                committed_account_id = account.account_id

            # WC-33 STEP B: Check and reserve hierarchical resources before
            # command ledger entry. This ensures all budget levels are checked
            # atomically before any broker effect occurs.
            opportunity_id = self._opportunity_id_for(signal, account, single_ids)
            resources_ok, resource_error, reservation_id = await self._check_and_reserve_resources(
                signal_id=opportunity_id,
                account=account,
                quantity=quantity,
                signal=order_signal,
                broker=broker,
            )
            if not resources_ok:
                assert resource_error is not None
                # Release the capital reserved by _try_reserve_capital
                self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
                # Create rejection result
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=f"resource reservation blocked: {resource_error}",
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
            # ALLOC-05: an EXISTING row still in PENDING_SUBMISSION means an
            # earlier attempt (a crashed process, or a concurrent worker)
            # wrote its intent and we cannot know whether its broker call
            # happened. That is an ambiguous submission, never "brand new":
            # resubmitting could place the same exposure twice.
            crash_window_duplicate = (
                not ledger_entry.newly_opened
                and ledger_entry.uncertainty_state == UncertaintyState.PENDING_SUBMISSION
            )
            if crash_window_duplicate:
                self.store.mark_command_ledger_outcome(
                    ledger_key,
                    uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                    terminal_evidence={
                        "broker_status": "intent_found_without_outcome",
                        "signal_id": signal.id,
                        "reserved_notional": notional,
                    },
                )
                ledger_entry.uncertainty_state = UncertaintyState.UNKNOWN_AMBIGUOUS
            if crash_window_duplicate or command_ledger.is_duplicate_submission(ledger_entry.uncertainty_state):
                # A prior attempt under this exact key already ran (or is
                # running) -- never submit a second broker order for it.
                # The broker was already released this reservation's fate
                # one way or another on that first attempt, so release here
                # too rather than double-reserve.
                self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
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
                if crash_window_duplicate:
                    # The earlier attempt never exported an outcome; surface
                    # the unresolved obligation. A plain replay of an already
                    # exported outcome is deliberately NOT re-exported ("duplicate_command" is not a
                    # valid routing outcome and would raise).
                    self._export_routing_outcome(
                        signal, outcome="error", account=account, order_status=result.status, message=result.message
                    )
                continue

            # WC-33 STEP C: Durable intent before dispatch
            # Get contract multiplier value for policy hash
            mult, _, _ = _get_contract_multiplier(order_signal)

            # Build OrderIntent with policy hash from sizing inputs
            policy_input = {
                "side": order_signal.side.value,
                "quantity": quantity,
                "multiplier": mult,
                "price": order_signal.price,
            }
            policy_hash = hashlib.sha256(json.dumps(policy_input, sort_keys=True).encode()).hexdigest()

            # Handle fractional quantities: if not whole number, store in price_constraints
            if isinstance(quantity, float) and not quantity.is_integer():
                intent_quantity = 0
                quantity_fractional = quantity
            else:
                intent_quantity = int(quantity)
                quantity_fractional = None

            # Build price constraints and protection recipe
            price_constraints = {"entry": order_signal.price}
            if order_signal.stop_loss is not None:
                price_constraints["stop_loss"] = order_signal.stop_loss
            if order_signal.take_profit is not None:
                price_constraints["take_profit"] = order_signal.take_profit
            if quantity_fractional is not None:
                price_constraints["quantity_fractional"] = quantity_fractional

            protection_recipe = None
            if order_signal.stop_loss is not None or order_signal.take_profit is not None:
                protection_recipe = {}
                if order_signal.stop_loss is not None:
                    protection_recipe["stop_loss"] = order_signal.stop_loss
                # TODO: map take_profit to targets if needed

            # Create OrderIntent (WC-33 STEP C)
            intent = OrderIntent.create(
                opportunity_id=opportunity_id,
                physical_account_id=account.account_id,  # Using account_id as physical_account_id for now
                binding_id=account.account_id,  # Using account_id as binding_id for now
                client_correlation_id=ledger_key,
                policy_hash=policy_hash,
                quantity=intent_quantity,
                price_constraints=price_constraints,
                protection_recipe=protection_recipe,
                reservation_id=reservation_id,
            )

            # PU-A2: the real moment this engine actually calls the broker --
            # the "decision -> submission" boundary app/execution_quality.py's
            # stage breakdown reports, captured immediately before the call
            # so nothing else on this path (routing, sizing, the capital-
            # admission check above) is folded into it.
            # WC-33: Honest dry_run: stop AFTER intent is built, BEFORE outbox.enqueue
            submitted_at = datetime.now(timezone.utc)
            ambiguous_submission = False
            if dry_run:
                # WC-33 STEP E: Honest dry_run
                # Stop after intent is built, before outbox.enqueue
                # Transition reservation to RELEASED with dry_run evidence
                if reservation_id is not None:
                    self.hierarchical_budget.transition(
                        reservation_id,
                        ReservationState.RELEASED,
                        evidence={"dry_run": True},
                    )
                # Release capital allocation
                self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
                # Update decision trace reason with planned details
                # TODO: implement update_decision_trace_reason in store and call it here
                # For now, log the plan details
                logger.info(
                    "dry_run mode: planned signal=%s account=%s qty=%s price=%s reservation=%s",
                    signal.id,
                    account.account_id,
                    quantity,
                    order_signal.price,
                    reservation_id,
                )
                # Return honest PENDING result without going through normal fill processing
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.PENDING,
                    signal_id=signal.id,
                    message="dry_run: planned, not dispatched",
                    filled_quantity=None,
                    filled_price=None,
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

            # WC-33 STEP C: Enqueue intent and claim for dispatch
            try:
                self.outbox.enqueue(intent)
                logger.info(
                    "intent_enqueued signal=%s account=%s intent_id=%s",
                    signal.id,
                    account.account_id,
                    intent.intent_id,
                )
            except Exception as e:
                logger.exception("failed to enqueue intent signal=%s account=%s", signal.id, account.account_id)
                # Release reservations on enqueue failure
                self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
                if reservation_id is not None:
                    self.hierarchical_budget.transition(
                        reservation_id,
                        ReservationState.RELEASED,
                        evidence={"error": str(e)},
                    )
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"intent enqueue failed: {str(e)}",
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

            # Claim the outbox item for dispatch
            worker_lease_id = self.lease_guard.lease_id if hasattr(self.lease_guard, 'lease_id') else "engine"
            try:
                claimed_item = self.outbox.claim_next(worker_lease_id)
                if claimed_item is None:
                    logger.error("failed to claim outbox item signal=%s", signal.id)
                    raise RuntimeError("outbox item not found after enqueue")
            except Exception as e:
                logger.exception("failed to claim outbox item signal=%s", signal.id)
                # Release reservations on claim failure
                self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
                if reservation_id is not None:
                    self.hierarchical_budget.transition(
                        reservation_id,
                        ReservationState.RELEASED,
                        evidence={"error": str(e)},
                    )
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"outbox claim failed: {str(e)}",
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

            # WC-33 STEP C: After outbox claim (durable intent persisted), transition to COMMITTED_TO_PENDING_ORDER
            # This marks the submission as committed before broker call
            if reservation_id is not None:
                try:
                    self.hierarchical_budget.transition(
                        reservation_id,
                        ReservationState.COMMITTED_TO_PENDING_ORDER,
                        evidence={"outbox_intent_id": intent.intent_id},
                    )
                except Exception as e:
                    logger.exception("failed to transition to COMMITTED_TO_PENDING_ORDER signal=%s", signal.id)
                    # Release reservations and capital on transition failure
                    self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.ERROR,
                        signal_id=signal.id,
                        message=f"reservation transition failed: {str(e)}",
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

            try:
                order_signal.client_order_id = ledger_key
                result = await broker.place_order(order_signal, account, quantity, symbol)
                # WC-33 STEP D: Record response in outbox and transition reservation based on result status
                outbox_response = {
                    "status": result.status.value,
                    "broker_order_id": result.broker_order_id,
                    "message": result.message,
                }
                self.outbox.record_response(intent.intent_id, outbox_response)
                # WC-33 STEP D: Transition reservation based on result status
                if reservation_id is not None:
                    if result.status == OrderStatus.FILLED:
                        # Filled: go from COMMITTED_TO_PENDING_ORDER → FILLED_EXPOSURE
                        self.hierarchical_budget.transition(
                            reservation_id,
                            ReservationState.FILLED_EXPOSURE,
                            evidence={"broker_order_id": result.broker_order_id, "filled_quantity": result.filled_quantity},
                        )
                    elif result.status == OrderStatus.PENDING:
                        # Already in COMMITTED_TO_PENDING_ORDER, no further transition needed
                        pass
                    elif result.status == OrderStatus.REJECTED:
                        # Rejected: go from COMMITTED_TO_PENDING_ORDER → RELEASED
                        self.hierarchical_budget.transition(
                            reservation_id,
                            ReservationState.RELEASED,
                            evidence={"reason": "broker_rejected"},
                        )
            except Exception as exc:  # noqa: BLE001 - one account's failure must not block others
                logger.exception("order failed for account=%s", account.account_id)
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=str(exc),
                )
                ambiguous_submission = True
                # WC-33 STEP D: Record response and transition to UNKNOWN_HELD on exception
                try:
                    outbox_response = {
                        "exception": str(exc),
                        "status": "error",
                        "message": result.message,
                    }
                    self.outbox.record_response(intent.intent_id, outbox_response)
                except Exception:
                    logger.exception("failed to record outbox response for failed order")
                # WC-33 STEP D: Transition to UNKNOWN_HELD (held, not released automatically)
                if reservation_id is not None:
                    try:
                        self.hierarchical_budget.transition(
                            reservation_id,
                            ReservationState.UNKNOWN_HELD,
                            evidence={"exception": str(exc)},
                        )
                    except Exception:
                        logger.exception("failed to transition reservation to UNKNOWN_HELD")
                self.store.mark_command_ledger_outcome(
                    ledger_key,
                    uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                    terminal_evidence={
                        **command_ledger.ambiguous_evidence_for_exception(exc),
                        "signal_id": signal.id,
                        "reserved_notional": notional,
                    },
                )
            else:
                outcome_state, outcome_remote, outcome_evidence = command_ledger.classify_order_result(result)
                ambiguous_submission = outcome_state == UncertaintyState.UNKNOWN_AMBIGUOUS
                if ambiguous_submission:
                    outcome_evidence = {**outcome_evidence, "signal_id": signal.id, "reserved_notional": notional}
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
            elif ambiguous_submission and notional:
                # ALLOC-05: an ambiguous submission (timeout / lost response /
                # ERROR / PENDING with nothing to poll) may have been
                # accepted at the venue. It is an unresolved OBLIGATION: the
                # capital and strategy reservations stay held until
                # `resolve_unknown_submission` (or reconciliation that
                # records the real fill) settles it. Releasing here would let
                # the same capacity be spent twice.
                logger.warning(
                    "ambiguous submission for signal=%s account=%s: holding reservation of %.2f until resolved",
                    signal.id,
                    account.account_id,
                    notional,
                )
            else:
                self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)

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

            # WP-38 (G-C-24): persist paper broker order ID sequence after a FILLED result
            if result.status == OrderStatus.FILLED and account.broker == "paper":
                self._persist_paper_order_id_sequence(result, account)

            export_envelope = self._build_export_envelope(
                result,
                account=account,
                symbol=symbol,
                side=order_signal.side,
                asset_class=order_signal.asset_class,
                originating_source_event_id=signal.id,
                originating_analyst_id=signal.analyst,
            )
            mult, _, spec_note = contract_multiplier(order_signal)
            # Append spec note to result message if present (e.g., "fx unit assumed: units")
            if spec_note is not None and result.status in (OrderStatus.FILLED, OrderStatus.PENDING):
                result = replace(result, message=f"{result.message} ({spec_note})")
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
                contract_multiplier=mult,
            )
            # D-01: Save bracket child leg orders (stop and take-profit)
            # as separate orders rows so they can be polled in reconciliation
            if result.status == OrderStatus.FILLED and result.child_order_ids and order_purpose == "entry":
                for child_type, child_broker_order_id in result.child_order_ids.items():
                    if child_type == "stop":
                        child_purpose = "stop_exit"
                    elif child_type == "take_profit":
                        child_purpose = "target_exit"
                    else:
                        continue  # Unknown child type, skip
                    self.store.save_child_order_result(
                        account_id=account.account_id,
                        broker=account.broker,
                        symbol=symbol,
                        quantity=quantity,
                        broker_order_id=child_broker_order_id,
                        purpose=child_purpose,
                        family_id=signal.id,  # use the original signal id as family
                    )
            results.append(result)
            self._export_routing_outcome(
                signal,
                outcome=_OUTCOME_BY_ORDER_STATUS[result.status],
                account=account,
                order_status=result.status,
                message=result.message,
            )

        if allocation_intent is not None and single_ids:
            if committed_account_id is not None:
                self.store.commit_allocation_intent(signal.id, committed_account_id)
            else:
                self.store.skip_allocation_intent(
                    signal.id,
                    reason="no approved account could take this trade: "
                    + "; ".join(f"{r.account_id}: {r.message}" for r in results if r.account_id in single_ids),
                    trace=[
                        {"account_id": r.account_id, "status": r.status.value, "message": r.message}
                        for r in results
                    ],
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

    async def _handle_signal_edit(self, signal: Signal) -> list[OrderResult] | None:
        """WP-11 (A-02/A-11): Handle edited/revised signals.

        When signal.original_message_id is set, this signal is an edit of an
        earlier message. Instead of placing new entry orders, we detect the
        original signal and prevent duplicate entries.

        NOTE (Track 5, point 6): An edit with a DISTINCT revision_id is a NEW
        provider event. Only treat it as an amendment if the revision_id is the
        same (or both None). This preserves the audited behavior that distinct
        revisions (from Telegram MTProto or other versioning systems) are not
        collapsed with their originals.

        Returns a list of OrderResults marking edit amendments, or None if no
        original signal is found or if the revision_id is distinct (in which case
        normal routing continues).
        """
        # Find the original signal(s) that this edits
        original_signal_ids = self.store.find_signals_by_original_message_id(
            channel_id=signal.channel_id,
            original_message_id=signal.original_message_id,
        )
        if not original_signal_ids:
            # No original signal found; treat this as a regular new signal
            return None

        # Get the original signal to check its revision_id
        # If the edited signal has a DISTINCT revision_id, it's a NEW provider event
        # and should not be treated as an amendment.
        original_signal = self.store.get_signal(original_signal_ids[0])
        if original_signal and original_signal.get("revision_id") != signal.revision_id:
            # Distinct revision_id means this is a NEW signal in the provider's eyes
            # (e.g., Telegram edit with new MTProto revision). Let normal routing proceed.
            return None

        # Collect all accounts that have entry orders from the original signal(s)
        accounts_with_entries: set[str] = set()
        for orig_sig_id in original_signal_ids:
            orders = self.store.list_orders_for_signal(orig_sig_id)
            for order_row in orders:
                account_id = order_row["account_id"]
                # Only track ENTRY-purpose orders with FILLED or PENDING status
                if order_row.get("purpose") in (None, "entry"):
                    if order_row["status"] in (OrderStatus.FILLED.value, OrderStatus.PENDING.value):
                        accounts_with_entries.add(account_id)

        # If no existing entry orders, this edit cannot be applied; treat as new signal
        if not accounts_with_entries:
            return None

        # Mark this edit as applied (prevents duplicate entries)
        results: list[OrderResult] = []
        for account_id in accounts_with_entries:
            result = OrderResult(
                account_id=account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message="edit applied to existing position; no new entry",
            )
            results.append(result)

        return results if results else None

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
        # Exits are never fingerprint-correlated: a duplicate CLOSE is
        # recognised position-aware by the lifecycle manager (TRK-27
        # `check_duplicate_exit`) and by the plain "no open position" path,
        # and a legitimate second exit after a re-entry within the window
        # must never be suppressed by a fingerprint match.
        if signal.side == Side.CLOSE or signal.intent in (Intent.EXIT, Intent.REDUCE, Intent.CANCEL):
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
            if outcome is CorrelationOutcome.CORROBORATING and signal.price is None:
                # A-14: two PRICELESS alerts with the same fingerprint inside
                # the window are the same real-world event, but with no price
                # to agree on this layer cannot vouch for a replay of the
                # earlier order as this message's outcome. Explicit HOLD:
                # record the correlation, submit nothing, and say so.
                logger.info(
                    "signal id=%s (channel=%s) is a priceless corroboration of signal id=%s (channel=%s) -- "
                    "same (source, symbol, side) fingerprint within the window; held, no new order (A-14)",
                    signal.id,
                    signal.channel_id,
                    candidate["id"],
                    candidate["channel_id"],
                )
                signal.import_batch = f"cross_transport_priceless_corroboration:{candidate['id']}"
                self.store.save_signal(signal)
                return [
                    OrderResult(
                        account_id=self._CROSS_TRANSPORT_CONFLICT_ACCOUNT_ID,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message=(
                            f"held: priceless signal corroborates already-recorded signal id={candidate['id']!r} "
                            f"(channel_id={candidate['channel_id']!r}) -- same fingerprint within the correlation "
                            "window and no price to verify; no new order submitted (A-14 HOLD)"
                        ),
                    )
                ]
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

    async def _resolve_price_for_gating(
        self, broker: BrokerAdapter, order_signal: Signal
    ) -> tuple[float | None, str | None]:
        """B-06: Resolve entry price for pre-flight gating, using broker live
        quotes when available as a primary source, falling back to the signal's
        message price.

        Returns:
            (price, source) where:
            - price: float | None — the resolved price, or None if unresolvable
            - source: str | None — "broker_quote" if from broker, "signal" if from
              message, None if unresolvable
        """
        # Try broker quote first if capability available
        if broker.has_quote_capability:
            try:
                quote = await broker.get_quote(order_signal.symbol)
                if quote is not None and math.isfinite(quote) and quote > 0:
                    return quote, "broker_quote"
            except Exception:
                # Log but don't fail; fall back to signal price
                logger.debug(
                    "broker quote fetch failed for symbol=%s broker=%s; falling back to signal price",
                    order_signal.symbol,
                    broker.__class__.__name__,
                    exc_info=True,
                )

        # Fall back to signal price
        if order_signal.price is not None and math.isfinite(order_signal.price) and order_signal.price > 0:
            return order_signal.price, "signal"

        # No resolvable price from either source
        return None, None

    def _check_route_qualified(
        self, account: DestinationAccount, signal: Signal, broker: BrokerAdapter
    ) -> tuple[bool, OrderResult | None]:
        """Track 1b live-routing gate: refuse to route a live ENTRY through
        an execution route -- (adapter_type, route_key, asset_class,
        product_type, environment), the exact tuple app/qualification.py's
        ladder is tracked per -- that has never had a human operator record
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
        `environment` is the resolved venue environment (paper/live/sandbox)
        from the broker; a route is only release-approved for the environment
        it was qualified in.

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
        environment = broker.venue_environment(account)
        approved = self.store.is_route_release_approved(
            adapter_type=account.broker,
            route_key=route_key,
            asset_class=asset_class,
            product_type=_UNDECLARED_ROUTE_PRODUCT_TYPE,
            environment=environment,
        )
        if not approved:
            return False, self._reject(
                account,
                signal,
                "route not qualified for live release: no 'release_approved' qualification recorded for "
                f"route (adapter_type='{account.broker}', route_key='{route_key}', "
                f"asset_class='{asset_class}', product_type='{_UNDECLARED_ROUTE_PRODUCT_TYPE}', "
                f"environment='{environment}') -- "
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
        # B-03: apply contract multiplier to risk calculation
        mult, spec_error, spec_note = _get_contract_multiplier(order_signal)
        if spec_error is not None:
            return False, self._reject(account, order_signal, spec_error)
        contract_multiplier = mult
        risk_notional = abs(price - stop_loss) * abs(quantity) * contract_multiplier
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

        WP-32: Fail closed for accounts whose adapter reports no
        buying-power figure AND have no ceiling (max_notional_exposure or
        risk_percent_of_equity). If an account has a ceiling, skip the
        check and rely on that ceiling instead. Paper broker reports
        buying_power=cash, so it always has a reportable figure.

        - Broker has no balance capability AND no ceiling: FAIL CLOSED
          (reject the entry).
        - Broker has no balance capability BUT has ceiling: SKIP (rely
          on ceiling).
        - Broker reports no buying_power AND no ceiling: FAIL CLOSED
          (reject the entry).
        - Broker reports no buying_power BUT has ceiling: SKIP (rely
          on ceiling).
        - Broker reports buying_power AND price is resolvable: FAIL
          CLOSED if notional > buying_power.
        - Signal has no resolvable price: SKIP (notional can't be
          computed; unlike _try_reserve_capital's opt-in gates, this
          check applies unconditionally and a priceless signal must not
          newly be rejected by a check nobody asked for)."""
        # Check if account has any ceiling configured
        has_ceiling = (
            account.max_notional_exposure is not None
            or account.risk_percent_of_equity is not None
        )

        broker = self.brokers.get(account.broker)
        if broker is None or not broker.has_balance_capability:
            if has_ceiling:
                # Has ceiling: skip check, rely on ceiling
                logger.info(
                    "buying_power_check_skipped account=%s broker=%s reason=no_verified_balance_capability ceiling_configured",
                    account.account_id,
                    account.broker,
                )
                return True, None
            else:
                # No ceiling: fail closed
                return False, self._reject(
                    account,
                    order_signal,
                    f"account '{account.account_id}' adapter '{account.broker}' has no verified balance capability "
                    "and no capital ceiling is configured (max_notional_exposure or risk_percent_of_equity) -- "
                    "refusing entry for risk safety; either configure a ceiling or use an adapter that reports "
                    "account balance",
                )

        # B-06: Resolve price using broker quote capability when available
        price, price_source = await self._resolve_price_for_gating(broker, order_signal)
        if price is None:
            logger.info(
                "buying_power_check_skipped account=%s broker=%s reason=no_resolvable_price",
                account.account_id,
                account.broker,
            )
            return True, None

        balance = await broker.get_account_balance(account)
        if balance is None or balance.buying_power is None:
            if has_ceiling:
                # Has ceiling: skip check, rely on ceiling
                logger.info(
                    "buying_power_check_skipped account=%s broker=%s reason=buying_power_not_reported ceiling_configured",
                    account.account_id,
                    account.broker,
                )
                return True, None
            else:
                # No ceiling: fail closed
                return False, self._reject(
                    account,
                    order_signal,
                    f"account '{account.account_id}' broker '{account.broker}' does not report a buying_power "
                    "figure and no capital ceiling is configured (max_notional_exposure or risk_percent_of_equity) -- "
                    "refusing entry for risk safety; either configure a ceiling or use an adapter/account that "
                    "reports buying power",
                )

        # B-03: apply contract multiplier to buying power check
        mult, spec_error, spec_note = _get_contract_multiplier(order_signal)
        if spec_error is not None:
            return False, self._reject(account, order_signal, spec_error)
        contract_multiplier = mult
        notional = abs(quantity) * abs(price) * contract_multiplier
        if notional > balance.buying_power:
            return False, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' insufficient buying power: broker-reported buying_power "
                f"({balance.buying_power:.2f}) is less than this entry's notional ({notional:.2f}) "
                f"(price_source={price_source}) -- refusing",
            )
        return True, None

    def resolve_unknown_submission(self, idempotency_key: str, *, outcome: str, evidence: str) -> bool:
        """ALLOC-05: settle one ambiguous ENTRY submission with independent
        evidence. Only `outcome="not_placed"` is supported: the operator (or
        a reconciliation readback) has confirmed the broker holds no order
        or fill for it, so the held capital/strategy reservation is released
        exactly once and the ledger row becomes REJECTED_CONFIRMED.

        A submission that DID reach the venue is not released here: its
        fill must be recorded through reconciliation so confirmed exposure
        replaces the reservation. Releasing it on an operator's word alone
        would drop real exposure from the books. Returns False when the
        entry is unknown, not an ENTRY, or already resolved."""
        if outcome != "not_placed":
            raise ValueError("only outcome='not_placed' can release a reservation; record real fills via reconciliation")
        entry = self.store.get_command_ledger_entry(idempotency_key)
        if entry is None or entry.command_type != CommandType.ENTRY:
            return False
        if entry.uncertainty_state != UncertaintyState.UNKNOWN_AMBIGUOUS or entry.resolved_at is not None:
            return False
        signal_id = entry.terminal_evidence.get("signal_id")
        notional = entry.terminal_evidence.get("reserved_notional") or 0.0
        if notional and signal_id:
            self.capital_allocator.release(entry.account_id, float(notional), signal_id=str(signal_id))
        self.store.mark_command_ledger_outcome(
            idempotency_key,
            uncertainty_state=UncertaintyState.REJECTED_CONFIRMED,
            terminal_evidence={"resolution": "not_placed", "operator_evidence": evidence},
        )
        return True

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

        strategy_budget = self.store.get_strategy_budget(order_signal.source)
        strategy_ceiling = strategy_budget["max_notional"] if strategy_budget is not None else None
        has_gate = (
            account.max_notional_exposure is not None
            or account.risk_percent_of_equity is not None
            or self.max_owner_notional_exposure is not None
            or strategy_ceiling is not None
            or account.max_gross_leverage is not None
        )
        if not has_gate:
            return True, 0.0, None

        # B-06: Resolve price using broker quote capability when available
        price_source = None
        broker = self.brokers.get(account.broker)

        # First, check if signal has a price that is not a finite positive number
        if order_signal.price is not None and (not math.isfinite(order_signal.price) or order_signal.price <= 0):
            # Signal price exists but is not valid (0, negative, NaN, inf)
            return False, 0.0, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has a capital/risk exposure gate configured "
                "(max_notional_exposure, risk_percent_of_equity, and/or an owner-wide ceiling) but this "
                "signal carries a price that is not a finite positive number ({order_signal.price}) -- "
                "admission is refused rather than silently skipping the check (see app/capital_allocator.py)",
            )

        if broker is not None:
            price, price_source = await self._resolve_price_for_gating(broker, order_signal)
        else:
            # No broker available; use signal price only
            if order_signal.price is not None and math.isfinite(order_signal.price) and order_signal.price > 0:
                price = order_signal.price
                price_source = "signal"
            else:
                price = None

        if price is None:
            return False, 0.0, self._reject(
                account,
                order_signal,
                f"account '{account.account_id}' has a capital/risk exposure gate configured "
                "(max_notional_exposure, risk_percent_of_equity, and/or an owner-wide ceiling) but this "
                "signal carries no price -- neither broker quote (if capability available) nor message "
                "price can be resolved, so notional can't be computed; admission is refused rather than "
                "silently skipping the check (see app/capital_allocator.py)",
            )

        # B-03: apply contract multiplier to capital allocation
        mult, spec_error, spec_note = _get_contract_multiplier(order_signal)
        if spec_error is not None:
            return False, 0.0, self._reject(account, order_signal, spec_error)
        contract_multiplier = mult

        # A-09: Chase guard - validate price age and deviation before sizing.
        # Both gates are opt-in (None = disabled) and fail closed when enabled.
        if config.SIGNAL_MAX_PRICE_AGE_SECONDS is not None:
            signal_received = order_signal.received_at
            if signal_received.tzinfo is None:
                signal_received = signal_received.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - signal_received).total_seconds()
            if age_seconds > config.SIGNAL_MAX_PRICE_AGE_SECONDS:
                return False, 0.0, self._reject(
                    account,
                    order_signal,
                    f"price too old ({age_seconds:.0f}s > {config.SIGNAL_MAX_PRICE_AGE_SECONDS:.0f}s) "
                    f"-- rejected by SIGNAL_MAX_PRICE_AGE_SECONDS gate",
                )

        if (
            config.SIGNAL_MAX_PRICE_DEVIATION_PCT is not None
            and broker is not None
            and order_signal.price is not None
        ):
            ref_price = broker.get_reference_price(order_signal.symbol)
            if ref_price is not None and ref_price > 0:
                deviation_pct = abs(order_signal.price - ref_price) / ref_price * 100
                if deviation_pct > config.SIGNAL_MAX_PRICE_DEVIATION_PCT:
                    return False, 0.0, self._reject(
                        account,
                        order_signal,
                        f"price deviation {deviation_pct:.1f}% exceeds limit {config.SIGNAL_MAX_PRICE_DEVIATION_PCT:.1f}% "
                        f"(signal={order_signal.price}, reference={ref_price}) -- rejected with price_reference=broker_quote",
                    )

        notional = abs(quantity) * abs(price) * contract_multiplier

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
                        price_note = f" (price from {price_source})" if price_source else ""
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            f"account '{account.account_id}' notional exposure ceiling "
                            f"({account.max_notional_exposure}) would be exceeded by this entry "
                            f"(confirmed={exposure.notional:.2f}, pending={pending:.2f}, "
                            f"requested={notional:.2f}{price_note}) -- refusing",
                        )

                if account.max_gross_leverage is not None:
                    # B-11: enforce leverage cap
                    broker = self.brokers.get(account.broker)
                    if broker is None:
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            f"account '{account.account_id}' broker '{account.broker}' not found",
                        )
                    balance = await broker.get_account_balance(account)
                    if balance is None or balance.equity is None:
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            f"max_gross_leverage is set but the adapter reports no equity for "
                            f"account '{account.account_id}' -- refusing",
                        )

                    maint = balance.maintenance_margin or 0.0
                    pending = self.capital_allocator.pending_reservation(account.account_id)
                    max_allowed_notional = account.max_gross_leverage * (balance.equity - maint)
                    total_notional = exposure.notional + pending + notional

                    if total_notional > max_allowed_notional:
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            f"account '{account.account_id}' gross leverage ceiling would be exceeded by this entry: "
                            f"confirmed={exposure.notional:.2f}, pending={pending:.2f}, requested={notional:.2f}, "
                            f"total={total_notional:.2f} exceeds max allowed={max_allowed_notional:.2f} "
                            f"(leverage={account.max_gross_leverage}, equity={balance.equity:.2f}, "
                            f"maintenance_margin={maint:.2f}) -- refusing",
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

                if strategy_ceiling is not None:
                    # ALLOC-03: strategy-level admission, atomic across
                    # accounts and processes (see
                    # CapitalAllocator.reserve_with_strategy_ceiling).
                    strategy_exposure = confirmed_strategy_notional(self.store, order_signal.source)
                    if strategy_exposure.has_unresolved:
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            f"strategy '{order_signal.source}' has open exposure "
                            f"{strategy_exposure.unresolved_symbols} this replay could not resolve -- true "
                            "strategy notional is unknown, refusing new admissions until it resolves",
                        )
                    ok, confirmed, pending = self.capital_allocator.reserve_with_strategy_ceiling(
                        account.account_id,
                        notional,
                        signal_id=order_signal.id,
                        strategy_key=order_signal.source,
                        ceiling=strategy_ceiling,
                    )
                    if not ok:
                        return False, notional, self._reject(
                            account,
                            order_signal,
                            f"strategy '{order_signal.source}' notional ceiling ({strategy_ceiling}) would be "
                            f"exceeded by this entry (confirmed={confirmed:.2f}, pending={pending:.2f}, "
                            f"requested={notional:.2f}) across all accounts -- refusing",
                        )
                    return True, notional, None
                self.capital_allocator.reserve_locked(account.account_id, notional, signal_id=order_signal.id)
                return True, notional, None
        finally:
            if owner_gated:
                self.capital_allocator.owner_lock.release()

    def _requested_exit_quantity(self, signal: Signal, owned: float) -> float | None:
        """WP-10 (D-05/D-11): compute the requested exit quantity from a CLOSE signal.

        Returns the requested quantity (to be capped at owned), or None if no
        explicit request is given (meaning full close). Raises ValueError if an
        explicit request would be <= 0 (caller should reject).

        Args:
            signal: The CLOSE signal, which may carry quantity or reduce_fraction
            owned: The current position size (absolute value)

        Returns:
            - A positive float if signal.quantity or signal.reduce_fraction is set
              (the requested quantity before capping)
            - None if neither is set (full close implied)

        Raises:
            ValueError: if signal.quantity or signal.reduce_fraction is explicitly set but <= 0
        """
        # If signal has an explicit quantity, validate and use it
        if signal.quantity is not None:
            if signal.quantity <= 0:
                raise ValueError(f"CLOSE quantity must be > 0, got {signal.quantity}")
            return signal.quantity
        # If signal has a reduce_fraction, validate and compute requested from it
        if signal.reduce_fraction is not None:
            if signal.reduce_fraction <= 0:
                raise ValueError(f"CLOSE reduce_fraction must be > 0, got {signal.reduce_fraction}")
            return signal.reduce_fraction * owned
        # No explicit request means full close
        return None

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
        for every other caller.

        WP-10 (D-05/D-11): honor signal.quantity and signal.reduce_fraction;
        cap the requested quantity at abs(position), and reject when
        requested <= 0."""
        if position is None:
            position = self.store.get_position(account.account_id, symbol)
        if abs(position) < 1e-8:  # Use tolerance-based comparison instead of exact equality
            return None

        closing_side = Side.SELL if position > 0 else Side.BUY
        owned = abs(position)

        # WP-10 (D-05/D-11): compute requested quantity
        try:
            requested = self._requested_exit_quantity(signal, owned)
        except ValueError:
            # Invalid quantity or reduce_fraction: caller should reject this
            return None

        # Determine final quantity: cap requested at owned, or use full close if no request
        if requested is not None:
            quantity = min(requested, owned)
        else:
            # No explicit request means full close
            quantity = owned

        # WP-02 (C-02): strip stop_loss/take_profit/targets from close
        # signals to prevent adapters from building reverse-side bracket
        # legs that would open new positions after the close executes.
        # WP-14 (C-03/C-04): set intent=EXIT so adapters emit the correct
        # close intent (e.g., "Sell to Close" for Tastytrade, "flat" for NinjaTrader).
        resolved_signal = Signal(
            source=signal.source,
            symbol=signal.symbol,
            side=closing_side,
            asset_class=signal.asset_class,
            intent=Intent.EXIT,
            quantity=quantity,
            price=signal.price,
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
            order_signal.client_order_id = ledger_key
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
        # WP-38 (G-C-24): persist paper broker order ID sequence after a FILLED result
        # (this is _submit_order, called from plain close and managed entry paths)
        if result.status == OrderStatus.FILLED and account.broker == "paper":
            self._persist_paper_order_id_sequence(result, account)

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

    def _gate_close_by_provider_ownership(
        self, signal: Signal, account: DestinationAccount, symbol: str, local_position: float
    ) -> tuple[float, OrderResult | None]:
        """Track 18: the per-provider ownership gate for a plain-account
        close, consulted BEFORE `_resolve_close` builds an opposing order.
        `RoutingConfig` legitimately allows several different providers to
        route to the SAME destination account/symbol (see app/routing.py's
        module docstring) -- but `positions` is tracked ONLY per
        (account_id, symbol), pooled across every provider routed there,
        with no separate per-provider ownership ledger. Before this
        existed, a provider with ZERO attributable quantity in a pooled
        position could still close/reduce ANOTHER provider's entire
        position, with no rejection at all.

        Reuses `SignalStore.get_provider_position_ownership` -- the SAME
        computation (`orders.applied_execution_delta`, signed by side,
        joined through `signals.source`) Track 16's `GET /positions/
        {symbol}/provider-allocations` visibility endpoint already uses,
        never a second, parallel ledger that could drift out of sync with
        what that endpoint reports.

        Returns `(quantity_to_close, rejection)`: exactly one of
        `quantity_to_close > 0` or `rejection is not None`.
        `quantity_to_close` is capped to THIS provider's own attributable
        share -- never more, even when the pooled account position has
        more available (e.g. account owns 150, provider A built 100 and
        provider B built 50: provider B's full-exit signal caps to 50).

        When NO order/signal attribution exists at all for this
        account/symbol (`total_attributed == 0` -- e.g. a position
        reconciled or seeded outside this service's own tracked order
        pipeline, see `DestinationAccount.exclusive_writer_qualified`),
        there is no competing-provider data to gate against: this returns
        the full `local_position` unchanged, exactly matching this
        method's pre-Track-18 behavior for that case -- gating here would
        regress an already-supported, unrelated scenario rather than fix
        the multi-provider pooling gap this method exists for."""
        this_provider, total_attributed = self.store.get_provider_position_ownership(
            account.account_id, symbol, signal.source
        )
        if total_attributed == 0:
            return local_position, None
        same_direction_owned = this_provider if local_position > 0 else -this_provider
        quantity_to_close = min(max(0.0, same_direction_owned), abs(local_position))
        if quantity_to_close <= 0:
            rejection = OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message=(
                    f"EXIT RECEIVED / Provider: {signal.source} / Owned quantity: 0 / "
                    "NO ORDER CREATED / Reason: NO_PROVIDER_POSITION -- this provider has no "
                    f"attributable quantity in account '{account.account_id}' symbol '{symbol}' "
                    "(the tracked position here was built by a different provider); refusing to "
                    "close/reduce it. See GET /positions/{symbol}/provider-allocations for the "
                    "current per-provider breakdown."
                ),
            )
            return 0.0, rejection
        capped_position = quantity_to_close if local_position > 0 else -quantity_to_close
        return capped_position, None

    async def _resolve_and_submit_plain_close(
        self,
        signal: Signal,
        account: DestinationAccount,
        symbol: str,
        broker: BrokerAdapter,
        *,
        enforce_provider_ownership: bool = True,
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
        one process.

        `enforce_provider_ownership` (Track 18, default True): gates the
        resolved close to `signal.source`'s own attributable share of this
        pooled position -- see `_gate_close_by_provider_ownership`'s own
        docstring. The one caller that passes False is `close_position`'s
        manual dashboard "Exit now"/"Flatten" action, which is explicitly
        NOT provider-scoped (see that method's own docstring, "bypasses
        routing rules entirely") -- gating it against `close_signal.source`
        (a synthetic reason string like "manual_exit", never a real
        provider) would incorrectly reject the one action meant to flatten
        the WHOLE pooled position regardless of which provider(s) built
        it.

        D-07/F-01: Fail closed if a managed lifecycle exists for this
        account/symbol. The exit path is determined by lifecycle existence,
        not by the account's managed_lifecycle flag. If a lifecycle exists,
        the close must be routed through the managed path, not plain."""
        lock = self._plain_close_locks[(account.account_id, symbol)]
        async with lock:
            # D-07/F-01: refuse if a managed lifecycle exists for this position.
            # The exit path is determined by lifecycle existence, not the flag.
            lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
            if lifecycle is not None:
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=(
                        f"account '{account.account_id}' symbol '{symbol}' has an open managed lifecycle -- "
                        f"this close must be routed through the managed lifecycle path, not plain. "
                        f"The exit path is determined by lifecycle existence, not by the managed_lifecycle flag. "
                        f"This should not happen if the managed flag is in sync with lifecycle state; "
                        f"please check the account configuration and lifecycle state."
                    ),
                )
                self.store.save_order_result(result, purpose="close", family_id=None)
                return result
            if not self.store.claim_close(account.account_id, symbol):
                return OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message="a close for this account/symbol is already in progress elsewhere",
                )
            try:
                local_position = self.store.get_position(account.account_id, symbol)
                if abs(local_position) < 1e-8:  # Use tolerance-based comparison instead of exact equality
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

                # Track 18: per-provider ownership gate -- caps the close
                # to `signal.source`'s own attributable share of this
                # pooled position (or rejects outright with
                # NO_PROVIDER_POSITION if it owns none of it), BEFORE
                # `_resolve_close` builds an opposing order. See
                # `_gate_close_by_provider_ownership`'s own docstring.
                close_position_value = local_position
                if enforce_provider_ownership:
                    close_position_value, ownership_rejection = self._gate_close_by_provider_ownership(
                        signal, account, symbol, local_position
                    )
                    if ownership_rejection is not None:
                        self.store.save_order_result(ownership_rejection, purpose="close", family_id=None)
                        return ownership_rejection

                resolved = self._resolve_close(signal, account, symbol, position=close_position_value)
                if resolved is None:
                    # WP-10: _resolve_close returns None for two cases:
                    # 1. Position is essentially 0 (already returned earlier, shouldn't reach here)
                    # 2. Requested quantity is <= 0 (explicit rejection)
                    # Distinguish them by checking if close_position_value is non-zero.
                    if abs(close_position_value) >= 1e-8:
                        # Case 2: requested quantity was <= 0
                        rejection = OrderResult(
                            account_id=account.account_id,
                            status=OrderStatus.REJECTED,
                            signal_id=signal.id,
                            message=(
                                f"CLOSE requested quantity is invalid (requested <= 0); "
                                f"signal.quantity={signal.quantity}, signal.reduce_fraction={signal.reduce_fraction}"
                            ),
                        )
                        self.store.save_order_result(rejection, purpose="close", family_id=None)
                        return rejection
                    # Case 1: shouldn't reach here due to earlier check
                    else:
                        result = OrderResult(
                            account_id=account.account_id,
                            status=OrderStatus.REJECTED,
                            signal_id=signal.id,
                            message="no open position to close",
                        )
                        self.store.save_order_result(result, purpose="close", family_id=None)
                        return result

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
                # WP-26/E-06: Match plain-account closes to their entry by FIFO
                # so plain accounts contribute to trade episodes
                close_family_id = None
                if result.status == OrderStatus.FILLED:
                    close_family_id = self.store.get_oldest_entry_signal_id(
                        account.account_id, symbol, order_signal.side
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
                    acknowledged_quantity=acknowledged_quantity,
                    submitted_at=submitted_at,
                    purpose="close",
                    family_id=close_family_id,
                )
                return result
            finally:
                self.store.release_close(account.account_id, symbol)

    async def _handle_managed_signal(
        self,
        signal: Signal,
        account: DestinationAccount,
        symbol: str,
        *,
        enforce_provider_ownership: bool = True,
        dry_run: bool = False,
        opportunity_id: str | None = None,
    ) -> _ManagedOrderOutcome:
        """Route a BUY/SELL/CLOSE signal for a `managed_lifecycle` account through
        `PositionLifecycleManager` instead of the plain broker.place_order path.

        Returns a `_ManagedOrderOutcome` -- `submitted_at`/`protection_confirmed_at`
        are PU-A2's execution-quality timestamps, both None for a CLOSE (an exit,
        not an entry: nothing here submits a fresh protective stop for it, and
        app/execution_quality.py's protection stage is entry-only -- see
        _handle_managed_close). TRK-22: the remaining fields are AUD-01's
        distinct-field quantity model, threaded through from whichever of
        `_handle_managed_entry`/`_handle_managed_close` actually ran -- see
        `_ManagedOrderOutcome`'s own docstring. `enforce_provider_ownership`
        (Track 18) is forwarded to `_handle_managed_close` unchanged -- see its
        own docstring; entries have no equivalent gate (there is nothing pooled
        yet to gate an entry against). `dry_run` skips broker submission for both
        entry and close."""
        if signal.side == Side.CLOSE:
            return await self._handle_managed_close(
                signal, account, symbol, enforce_provider_ownership=enforce_provider_ownership, dry_run=dry_run
            )
        return await self._handle_managed_entry(signal, account, symbol, dry_run=dry_run, opportunity_id=opportunity_id)

    def _compute_default_target_fractions(self, targets: list[ProfitTarget]) -> list[float | None]:
        """Compute equal-split default fractions for targets that don't have them.

        WP-13: When a signal's targets carry no fraction, size them equal-split with
        the last level taking the remainder (3 levels → 1/3, 1/3, rest).

        Returns a list of fractions (one per target), where each is either:
        - The target's original fraction (if it had one)
        - A computed equal-split fraction (if it didn't have one)
        """
        if not targets:
            return []

        # Check if any target needs a computed fraction
        needs_computation = [t.fraction is None for t in targets]
        if not any(needs_computation):
            # All targets already have fractions, return as-is
            return [t.fraction for t in targets]

        # Compute equal-split fractions: base is 1.0 / count
        count = len(targets)
        base_fraction = 1.0 / count
        fractions: list[float | None] = []

        for i, target in enumerate(targets):
            if target.fraction is not None:
                # Target already has a fraction, use it
                fractions.append(target.fraction)
            elif i < count - 1:
                # Not the last target, use base fraction
                fractions.append(base_fraction)
            else:
                # Last target, gets the remainder
                remaining = 1.0 - (base_fraction * (count - 1))
                fractions.append(remaining)

        return fractions

    async def _handle_managed_entry(
        self,
        signal: Signal,
        account: DestinationAccount,
        symbol: str,
        dry_run: bool = False,
        opportunity_id: str | None = None,
    ) -> _ManagedOrderOutcome:
        # WC-33/WC-31: see `_opportunity_id_for` -- the caller passes the
        # replicate-scoped id when this account is an explicit replica.
        opportunity_id = opportunity_id or signal.id
        broker = self.brokers.get(account.broker)
        if broker is None:
            # TRK-22: a pre-submission rejection inside this method itself --
            # never reached a broker, same "all quantity fields stay None"
            # convention `_handle_signal`'s own equivalent pre-submission
            # rejections already use (see e.g. its own "no broker adapter
            # registered" branch).
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"no broker adapter registered for '{account.broker}'",
                ),
                None,
                None,
            )

        try:
            quantity = size_for_account(signal, account)
            # WP-17 (B-04): normalize quantity to venue precision after sizing
            normalized_qty = broker.normalize_quantity(account, symbol, quantity)
            if normalized_qty is None:
                raise UnsizedEntryError(f"quantity step unknown for {symbol} on {account.broker}")
            if normalized_qty <= 0:
                raise UnsizedEntryError(
                    f"quantity {quantity} rounds to {normalized_qty:.8g} below venue minimum for {symbol}"
                )
            quantity = normalized_qty
        except UnsizedEntryError as e:
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=str(e),
                ),
                None,
                None,
            )
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
            # WP-13: Default target sizing — when targets carry no fraction,
            # size them equal-split with the last level taking the remainder
            # (3 levels → 1/3, 1/3, rest). A single take_profit → one target at 1.0.
            fractions = self._compute_default_target_fractions(signal.targets)
            targets = [
                Target(trigger_price=level.price, action=TargetAction.SELL, reduce_fraction=fraction)
                for level, fraction in zip(signal.targets, fractions, strict=True)
            ]
        elif signal.take_profit is not None:
            targets = [Target(trigger_price=signal.take_profit, action=TargetAction.SELL, reduce_fraction=1.0)]
        else:
            targets = []

        # D-12: Resolve trailing stop configuration from signal
        trailing = None
        trail_percent = None
        if signal.trail_amount is not None:
            trailing = TrailingPolicy(trail_distance=signal.trail_amount)
        elif signal.trail_percent is not None:
            # Deferred: trail_distance will be calculated after entry fills
            # when we know the entry price (trail_distance = entry_price * trail_percent)
            trailing = TrailingPolicy(trail_distance=0.0)  # Placeholder; will be updated in on_entry_fill
            trail_percent = signal.trail_percent

        plan = PositionPlan(
            account_id=account.account_id,
            symbol=symbol,
            side=signal.side,
            planned_quantity=quantity,
            asset_class=signal.asset_class,
            broker=account.broker,
            initial_stop=signal.stop_loss,
            targets=targets,
            trailing=trailing,
            trail_percent=trail_percent,
            time_exit=signal.time_exit_at,
            # DB-0X: this position's own real entry signal id, carried for
            # its whole lifetime so a later CLOSE for this same
            # (account_id, symbol) can report the same `orders.family_id`
            # -- see PositionPlan.entry_signal_id's own docstring.
            entry_signal_id=signal.id,
        )

        error = self.lifecycle_manager.validate_plan(plan)
        if error is not None:
            # TRK-22: same pre-submission "never reached a broker" convention
            # as the "no broker adapter" branch above.
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id, message=error
                ),
                None,
                None,
            )

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
        # identity. Check for duplicate BEFORE reserving capital to prevent
        # double-release possibility.
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
            logger.info(
                "duplicate managed entry command idempotency_key=%s account=%s symbol=%s -- replaying "
                "tracked state=%s instead of resubmitting",
                ledger_key,
                account.account_id,
                symbol,
                ledger_entry.uncertainty_state.value,
            )
            # TRK-22: this exact save replays a prior attempt's tracked
            # state rather than submitting/applying anything new -- mirrors
            # `_submit_order`'s own duplicate-command branch exactly:
            # applied_quantity/confirmed_cumulative_fill/acknowledged_quantity
            # stay None (nothing new confirmed BY THIS call), while
            # applied_execution_delta/outstanding_possible_fill are the real
            # 0.0 "this save applied/has nothing new to report" facts.
            return _ManagedOrderOutcome(
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
                applied_execution_delta=0.0,
                outstanding_possible_fill=0.0,
            )

        # Only reserve capital AFTER confirming this is not a duplicate
        admitted, notional, rejection = await self._try_reserve_capital(account, signal, quantity)
        if not admitted:
            assert rejection is not None  # _try_reserve_capital always sets this when admitted is False
            # TRK-22: same convention -- capital admission is refused before
            # any broker call. The command-ledger row opened above (for
            # idempotency) must not linger as PENDING_SUBMISSION: nothing was
            # submitted, so it is a confirmed rejection with zero broker effect
            # (otherwise WC-32's UNCERTAIN_EFFECT gate would block the account).
            self.store.mark_command_ledger_outcome(
                ledger_key,
                uncertainty_state=UncertaintyState.REJECTED_CONFIRMED,
                terminal_evidence={"reason": "capital_admission_refused", "message": rejection.message},
            )
            return _ManagedOrderOutcome(rejection, None, None)

        # WC-33 STEP B: Check and reserve hierarchical resources before
        # lifecycle plan starts. This ensures all budget levels are checked
        # atomically before any broker effect occurs.
        resources_ok, resource_error, reservation_id = await self._check_and_reserve_resources(
            signal_id=opportunity_id,
            account=account,
            quantity=quantity,
            signal=signal,  # Use original signal which has stop_loss/take_profit
            broker=broker,
        )
        if not resources_ok:
            assert resource_error is not None
            # Release the capital reserved by _try_reserve_capital
            self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
            # Mark command ledger as rejected (confirmed rejection, not pending)
            self.store.mark_command_ledger_outcome(
                ledger_key,
                uncertainty_state=UncertaintyState.REJECTED_CONFIRMED,
                terminal_evidence={"reason": "resource_reservation_blocked", "message": resource_error},
            )
            # TRK-22: same convention as capital admission refusal
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=f"resource reservation blocked: {resource_error}",
                ),
                None,
                None,
            )

        self.lifecycle_manager.start_plan(plan)

        # WC-33 STEP C: Build OrderIntent before broker dispatch
        import hashlib
        import json

        # Get contract multiplier for policy hash
        mult, _, _ = _get_contract_multiplier(signal)

        # Build policy hash from sizing inputs
        policy_input = {
            "side": signal.side.value,
            "quantity": quantity,
            "multiplier": mult,
            "price": signal.price,
        }
        policy_hash = hashlib.sha256(json.dumps(policy_input, sort_keys=True).encode()).hexdigest()

        # Handle fractional quantities
        if isinstance(quantity, float) and not quantity.is_integer():
            intent_quantity = 0
            quantity_fractional = quantity
        else:
            intent_quantity = int(quantity)
            quantity_fractional = None

        # Build price constraints and protection recipe (use original signal, not entry_signal)
        price_constraints = {"entry": signal.price}
        if signal.stop_loss is not None:
            price_constraints["stop_loss"] = signal.stop_loss
        if signal.take_profit is not None:
            price_constraints["take_profit"] = signal.take_profit
        if quantity_fractional is not None:
            price_constraints["quantity_fractional"] = quantity_fractional

        protection_recipe = None
        if signal.stop_loss is not None or signal.take_profit is not None:
            protection_recipe = {}
            if signal.stop_loss is not None:
                protection_recipe["stop_loss"] = signal.stop_loss

        # Create OrderIntent
        intent = OrderIntent.create(
            opportunity_id=opportunity_id,
            physical_account_id=account.account_id,
            binding_id=account.account_id,
            client_correlation_id=ledger_key,
            policy_hash=policy_hash,
            quantity=intent_quantity,
            price_constraints=price_constraints,
            protection_recipe=protection_recipe,
            reservation_id=reservation_id,
        )

        # PU-A2: submission moment for this managed entry -- see
        # handle_signal's identical field for what it feeds into.
        submitted_at = datetime.now(timezone.utc)

        # WC-33 STEP E: Honest dry_run - stop AFTER intent is built, BEFORE outbox.enqueue
        if dry_run:
            # Release the reservation with dry_run evidence
            if reservation_id is not None:
                self.hierarchical_budget.transition(
                    reservation_id,
                    ReservationState.RELEASED,
                    evidence={"dry_run": True},
                )
            # Release capital allocation
            self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
            logger.info(
                "dry_run mode: planned signal=%s account=%s qty=%s price=%s reservation=%s",
                signal.id,
                account.account_id,
                quantity,
                entry_signal.price,
                reservation_id,
            )
            # TRK-22: dry_run result with no durable intent/outbox rows
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.PENDING,
                    signal_id=signal.id,
                    message="dry_run: planned, not dispatched",
                    filled_quantity=None,
                    filled_price=None,
                ),
                None,
                None,
                applied_execution_delta=0.0,
                outstanding_possible_fill=0.0,
            )

        # WC-33 STEP C: Enqueue intent before broker dispatch
        try:
            self.outbox.enqueue(intent)
            logger.info(
                "intent_enqueued signal=%s account=%s intent_id=%s",
                signal.id,
                account.account_id,
                intent.intent_id,
            )
        except Exception as e:
            logger.exception("failed to enqueue intent signal=%s account=%s", signal.id, account.account_id)
            # Release reservations on enqueue failure
            self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
            if reservation_id is not None:
                self.hierarchical_budget.transition(
                    reservation_id,
                    ReservationState.RELEASED,
                    evidence={"error": str(e)},
                )
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"intent enqueue failed: {str(e)}",
                ),
                submitted_at,
                None,
                applied_execution_delta=0.0,
                outstanding_possible_fill=0.0,
            )

        # Claim the outbox item for dispatch
        worker_lease_id = self.lease_guard.lease_id if hasattr(self.lease_guard, 'lease_id') else "engine"
        try:
            claimed_item = self.outbox.claim_next(worker_lease_id)
            if claimed_item is None:
                logger.error("failed to claim outbox item signal=%s", signal.id)
                raise RuntimeError("outbox item not found after enqueue")
        except Exception as e:
            logger.exception("failed to claim outbox item signal=%s", signal.id)
            # Release reservations on claim failure
            self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
            if reservation_id is not None:
                self.hierarchical_budget.transition(
                    reservation_id,
                    ReservationState.RELEASED,
                    evidence={"error": str(e)},
                )
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"outbox claim failed: {str(e)}",
                ),
                submitted_at,
                None,
                applied_execution_delta=0.0,
                outstanding_possible_fill=0.0,
            )

        # WC-33 STEP C: Transition to COMMITTED_TO_PENDING_ORDER before broker call
        if reservation_id is not None:
            try:
                self.hierarchical_budget.transition(
                    reservation_id,
                    ReservationState.COMMITTED_TO_PENDING_ORDER,
                    evidence={"outbox_intent_id": intent.intent_id},
                )
            except Exception as e:
                logger.exception("failed to transition to COMMITTED_TO_PENDING_ORDER signal=%s", signal.id)
                # Release reservations and capital on transition failure
                self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
                return _ManagedOrderOutcome(
                    OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.ERROR,
                        signal_id=signal.id,
                        message=f"reservation transition failed: {str(e)}",
                    ),
                    submitted_at,
                    None,
                    applied_execution_delta=0.0,
                    outstanding_possible_fill=0.0,
                )

        try:
            entry_signal.client_order_id = ledger_key
            result = await broker.place_order(entry_signal, account, quantity, symbol)
            # WC-33 STEP D: Record response in outbox and transition reservation based on result status
            outbox_response = {
                "status": result.status.value,
                "broker_order_id": result.broker_order_id,
                "message": result.message,
            }
            self.outbox.record_response(intent.intent_id, outbox_response)
            # WC-33 STEP D: Transition reservation based on result status
            if reservation_id is not None:
                if result.status == OrderStatus.FILLED:
                    # Filled: go from COMMITTED_TO_PENDING_ORDER → FILLED_EXPOSURE
                    self.hierarchical_budget.transition(
                        reservation_id,
                        ReservationState.FILLED_EXPOSURE,
                        evidence={"broker_order_id": result.broker_order_id, "filled_quantity": result.filled_quantity},
                    )
                elif result.status == OrderStatus.PENDING:
                    # Already in COMMITTED_TO_PENDING_ORDER, no further transition needed
                    pass
                elif result.status == OrderStatus.REJECTED:
                    # Rejected: go from COMMITTED_TO_PENDING_ORDER → RELEASED
                    self.hierarchical_budget.transition(
                        reservation_id,
                        ReservationState.RELEASED,
                        evidence={"reason": "broker_rejected"},
                    )
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
            self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
            # WC-33 STEP D: Record response and transition to UNKNOWN_HELD on exception
            try:
                outbox_response = {
                    "exception": str(exc),
                    "status": "error",
                    "message": str(exc),
                }
                self.outbox.record_response(intent.intent_id, outbox_response)
            except Exception:
                logger.exception("failed to record outbox response for failed order")
            # WC-33 STEP D: Transition to UNKNOWN_HELD (held, not released automatically)
            if reservation_id is not None:
                try:
                    self.hierarchical_budget.transition(
                        reservation_id,
                        ReservationState.UNKNOWN_HELD,
                        evidence={"exception": str(exc)},
                    )
                except Exception:
                    logger.exception("failed to transition reservation to UNKNOWN_HELD")
            self.lifecycle_manager.register_pending_entry(account, symbol, None, quantity)
            self.store.mark_command_ledger_outcome(
                ledger_key,
                uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
                terminal_evidence=command_ledger.ambiguous_evidence_for_exception(exc),
            )
            # TRK-22: a genuinely ambiguous ERROR (see the comment above) --
            # mirrors `_submit_order`'s own exception-branch classification
            # exactly: nothing confirmed applied/acknowledged by THIS call,
            # and (the order being terminal from this call's own point of
            # view) nothing outstanding for it to track either.
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id, message=str(exc)
                ),
                submitted_at,
                None,
                applied_execution_delta=0.0,
                outstanding_possible_fill=0.0,
                acknowledged_quantity=0.0,
            )

        # P0-2: one real broker answer landed (didn't raise) -- classify
        # and record it against the pre-effect row opened above, whatever
        # branch below then does with it.
        _ledger_state, _ledger_remote, _ledger_evidence = command_ledger.classify_order_result(result)
        self.store.mark_command_ledger_outcome(
            ledger_key, uncertainty_state=_ledger_state, remote_identifiers=_ledger_remote, terminal_evidence=_ledger_evidence
        )

        # TRK-22: AUD-01's distinct-field quantity model for this managed
        # entry -- the same FILLED/PENDING classification `_submit_order`
        # uses (see that method's own docstring for the shared contract),
        # adapted to this method's own, pre-existing fill-application gates
        # (`on_entry_fill` for FILLED, `resolve_pending_entry`'s own `> 0`
        # guard for a PENDING result's synchronous partial fill) rather than
        # a second, duplicate `record_fill` call -- these fields only ever
        # report what this method's OWN calls above already applied, never
        # re-derive or re-apply it.
        applied_quantity: float | None = None
        confirmed_cumulative_fill: float | None = None
        outstanding_possible_fill = 0.0
        protection_confirmed_at: datetime | None = None
        if result.status == OrderStatus.REJECTED:
            # A broker-confirmed rejection (or a client-side validation
            # failure that never reached the network) is the one case that
            # definitely never happened -- nothing to protect, so nothing
            # to keep registered.
            self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
            self.lifecycle_manager.unregister_plan(account.account_id, symbol)
            # Reservation already transitioned to RELEASED in the try block above
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
            self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
            # Reservation already transitioned to UNKNOWN_HELD in the except block above
            self.lifecycle_manager.register_pending_entry(account, symbol, None, quantity)
        elif result.status == OrderStatus.FILLED:
            # Confirmed exposure now includes this fill, so the provisional
            # reservation's job is done.
            self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
            # Reservation already transitioned to FILLED_EXPOSURE in the try block above
            filled_quantity = result.filled_quantity if result.filled_quantity is not None else quantity
            self.store.record_fill(account.account_id, symbol, signal.side, filled_quantity)
            # WP-38 (G-C-24): Persist paper broker order ID sequence after fills
            if account.broker == "paper":
                self._persist_paper_order_id_sequence(result, account)
            # TRK-22: `filled_quantity` above is exactly what was just
            # applied via `record_fill` -- same FILLED convention
            # `_submit_order` uses (a broker-confirmed FILLED with no
            # reported `filled_quantity` falls back to the full requested
            # `quantity`, never guessed as 0).
            applied_quantity = filled_quantity
            confirmed_cumulative_fill = filled_quantity
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
            outstanding_possible_fill = 0.0
        elif result.status == OrderStatus.PENDING:
            # TRK-22: `confirmed_cumulative_fill` is the broker-reported fact
            # regardless of whether it was applied (pass it through as-is,
            # same as `_submit_order`); `outstanding_possible_fill` is what
            # could still fill later. Both computed BEFORE the branch body
            # below decides whether to actually apply anything, since they
            # describe the broker's answer, not this method's reaction to it.
            confirmed_cumulative_fill = result.filled_quantity
            outstanding_possible_fill = quantity - (confirmed_cumulative_fill or 0.0)
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
                self.capital_allocator.release(account.account_id, notional, signal_id=signal.id)
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
                # TRK-22: this is the ONE case `resolve_pending_entry` (this
                # exact call, above) actually invoked `record_fill` for a
                # PENDING managed entry -- since this is always the FIRST
                # `resolve_pending_entry` call for a brand new
                # `register_pending_entry` (just above), its own
                # `pending.confirmed_filled_quantity` starts at 0.0, so the
                # `newly_applied` delta it records is exactly
                # `result.filled_quantity`. Never set for the
                # `filled_quantity is None or == 0.0` case below this `if` --
                # `resolve_pending_entry` was correctly NOT called for it, so
                # nothing was actually applied to report.
                applied_quantity = result.filled_quantity
                # PU-A2: resolve_pending_entry may have placed/confirmed the
                # protective stop synchronously (same reasoning as the
                # FILLED branch above) -- re-read the lifecycle rather than
                # assume, since resolve_pending_entry doesn't return one.
                lifecycle_now = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
                if lifecycle_now is not None:
                    protection_confirmed_at = lifecycle_now.stop.confirmed_at

        applied_execution_delta = applied_quantity if applied_quantity is not None else 0.0
        # TRK-Q1: same utility `_submit_order` uses -- see its own docstring
        # for exactly what "acknowledged" means and doesn't.
        acknowledged_quantity = quantity_module.acknowledged_quantity_for(result, quantity)
        return _ManagedOrderOutcome(
            result,
            submitted_at,
            protection_confirmed_at,
            applied_quantity=applied_quantity,
            confirmed_cumulative_fill=confirmed_cumulative_fill,
            applied_execution_delta=applied_execution_delta,
            outstanding_possible_fill=outstanding_possible_fill,
            acknowledged_quantity=acknowledged_quantity,
        )

    async def _handle_managed_close(
        self,
        signal: Signal,
        account: DestinationAccount,
        symbol: str,
        source: str = "provider_exit",
        *,
        enforce_provider_ownership: bool = True,
        dry_run: bool = False,
    ) -> _ManagedOrderOutcome:
        """Returns a `_ManagedOrderOutcome` like `_handle_managed_entry`, for
        the same call-site shape -- but a CLOSE is an exit, not an entry:
        nothing here confirms a fresh protective stop for it, so
        `protection_confirmed_at` is always None (PU-A2's protection stage
        is entry-only in this codebase today). `submitted_at` is also None
        here -- unlike the plain-account close path (see `_submit_order`),
        `request_exit`'s own submission call is inside
        app/lifecycle/manager.py, not this method, so there is no real
        submission instant available at this call site to report honestly.

        TRK-22: this method does NOT reuse `_submit_order` for its
        classification, unlike the plain-account close path -- `request_exit`
        (below) is `PositionLifecycleManager`'s own single execution-
        application owner for a managed exit fill (see its own docstring and
        `_apply_exit_fill`'s), with its OWN real `broker.place_order` call
        site (`_submit_exit_order`, inside app/lifecycle/manager.py) and its
        own `record_fill` call(s) already wired through `_apply_exit_fill`/
        `resolve_pending_exit`. `_submit_order` is this engine's OWN direct
        `broker.place_order` call for the plain-account close path; there is
        no broker call in this method to point `_submit_order` at, and
        duplicating its classification here would either re-call
        `broker.place_order` a second time (never -- would double-submit) or
        require refactoring `request_exit`'s own internals to return a
        `_submit_order`-shaped tuple, a bigger refactor across
        app/lifecycle/manager.py than this task scopes. This method instead
        classifies `request_exit`'s returned `OrderResult` using the exact
        same FILLED/PENDING rule `_submit_order` uses, against `available`
        (the quantity this method itself requested) as the base -- see the
        classification block below for the one disclosed approximation this
        implies (`available` vs. `request_exit`'s own internal, possibly
        narrower `ReductionPlan.requested_quantity`).

        `source` here is `request_exit`'s exit-KIND label (e.g.
        "provider_exit", the manual-flatten reason) -- NOT necessarily the
        real provider identity; `PositionLifecycleManager._submit_exit_
        order`/`_persist_self_initiated_exit` always save the resulting
        order's own `signals.source` as the synthetic `"lifecycle_manager"`
        regardless of what's passed here (self-initiated stop/target/
        trailing/time exits have no provider to attribute to at all). Track
        18's ownership gate below therefore reads `signal.source` (this
        CLOSE signal's REAL provider, e.g. "tradingview") instead, and only
        for GATING which quantity this call may request -- never passed
        through to `request_exit` itself, so it can't be confused with an
        exit-kind label downstream.

        `enforce_provider_ownership` (Track 18, default True): rejects this
        CLOSE outright if `signal.source` isn't the provider that owns this
        position's CURRENT lifecycle -- see the gating block below for
        exactly how that's determined and why it's a binary allow/reject
        here rather than the plain path's proportional cap. The one caller
        that passes False is `close_position`'s manual dashboard "Exit
        now"/"Flatten" action -- see that method's own docstring for why
        gating it against a synthetic reason string would be wrong."""
        # TRK-22: every pre-submission rejection in this method (this one,
        # "no shares available to sell", and the ownership-gate rejection
        # below) never reaches `request_exit`/a broker call -- all quantity
        # fields stay None, the same convention `_handle_managed_entry`'s
        # own pre-submission rejections use.
        lifecycle = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
        if lifecycle is None or lifecycle.closed:
            # TRK-27: before falling through to the generic "nothing to
            # close" rejection, check whether this CLOSE is a genuine
            # duplicate of an exit that already resolved for this exact
            # (account_id, symbol) -- e.g. a provider re-sending the same
            # real-world exit through a second collector/transport, with a
            # different channel_id/message_id, arriving after the first
            # exit already fully resolved. This early-return branch is
            # exactly where that case used to be silently indistinguishable
            # from a bare "no open position" rejection -- see
            # `PositionLifecycleManager.check_duplicate_exit`'s own
            # docstring for the exact mechanism and its documented scope
            # (this is a lower-level mirror of the equivalent check inside
            # `request_exit` itself, needed here too since this method
            # returns before ever calling `request_exit` in this branch).
            duplicate = self.lifecycle_manager.check_duplicate_exit(account, symbol)
            if duplicate is not None:
                # `check_duplicate_exit` is also called from inside
                # `request_exit` itself (a manager-internal call site with
                # no real `Signal` of its own to attribute to -- see its
                # sibling rejections there), so it always returns
                # `signal_id=""`. THIS call site's result DOES get
                # persisted via `save_order_result` (see `_handle_signal`'s
                # managed-lifecycle branch), whose `orders.signal_id` is a
                # real foreign key into `signals` (DB-01) -- an empty
                # string here would fail that constraint, since (unlike
                # `request_exit`'s other internal rejections) this result
                # is not a pure in-memory return value. Re-attribute it to
                # THIS real CLOSE signal's own id, already persisted by
                # `_handle_signal` before this method ever runs.
                return _ManagedOrderOutcome(replace(duplicate, signal_id=signal.id), None, None)
            return _ManagedOrderOutcome(
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
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message="no shares available to sell",
                ),
                None,
                None,
            )

        # WP-10 (D-05/D-11): compute requested quantity based on signal.quantity
        # or signal.reduce_fraction, capped at available
        try:
            requested = self._requested_exit_quantity(signal, lifecycle.confirmed_owned_quantity)
        except ValueError as e:
            return _ManagedOrderOutcome(
                OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=f"CLOSE requested quantity is invalid: {str(e)}",
                ),
                None,
                None,
            )

        if requested is not None:
            exit_quantity = min(requested, available)
        else:
            # No explicit request means full close
            exit_quantity = available

        if enforce_provider_ownership:
            # Track 18: managed_lifecycle's own EXE-09 entry gate
            # (`PositionLifecycleManager.validate_plan`) already refuses a
            # second, concurrent entry for the same (account_id, symbol)
            # while one is already active -- so unlike a plain account, a
            # managed lifecycle's CURRENT position is never pooled across
            # more than one provider at a time; it belongs entirely to
            # whichever provider's signal started it. That real, existing
            # invariant is what this checks -- NOT `orders.applied_
            # execution_delta` (Track 16's own attribution computation,
            # reused by the plain-account gate in
            # `_gate_close_by_provider_ownership`). TRK-22 fixed the
            # pre-existing gap this comment used to describe (this engine's
            # managed-lifecycle `save_order_result` calls, in `_handle_
            # signal`'s loop and `close_position`, now DO populate that
            # field -- see `_ManagedOrderOutcome` and this method's own
            # classification block below), so that data now exists -- but
            # this gate deliberately still doesn't use it: switching to a
            # proportional cap the way the plain-account gate does is a
            # real behavior change (binary allow/reject -> a computed
            # share) outside this task's own scope, not merely "the data
            # wasn't available yet." `lifecycle.plan.entry_signal_id` is the
            # reliable, ALREADY-
            # EXISTING source of truth for "which provider owns this
            # lifecycle" instead (see its own docstring in app/lifecycle/
            # models.py -- already used by `close_position`'s `family_id`
            # lookup for the identical purpose): always a real signal id
            # for a lifecycle started through the normal entry path (see
            # `_handle_managed_entry`'s `PositionPlan(entry_signal_id=
            # signal.id)`), so `owning_source is None` here only for a
            # lifecycle this engine didn't itself start -- fails closed
            # (rejected) exactly like a real ownership mismatch, never
            # guessed.
            owning_source = None
            if lifecycle.plan.entry_signal_id:
                entry_signal_row = self.store.get_signal(lifecycle.plan.entry_signal_id)
                if entry_signal_row is not None:
                    owning_source = entry_signal_row["source"]
            if owning_source != signal.source:
                return _ManagedOrderOutcome(
                    OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message=(
                            f"EXIT RECEIVED / Provider: {signal.source} / Owned quantity: 0 / "
                            "NO ORDER CREATED / Reason: NO_PROVIDER_POSITION -- this provider does not "
                            f"own the active lifecycle for account '{account.account_id}' symbol "
                            f"'{symbol}' (it was started by a different provider, or its owning "
                            "provider could not be confirmed); refusing to close/reduce it."
                        ),
                    ),
                    None,
                    None,
                )

        # `request_exit` is now the single execution-application owner for this
        # fill (see PositionLifecycleManager._apply_exit_fill): it applies the
        # confirmed delta to SignalStore itself, once, whether the exit fills
        # synchronously or is later resolved via resolve_pending_exit --
        # applying an optimistic guess here too was exactly the
        # "PENDING commitment recorded as a completed sale" bug this closes
        # (a partial fill followed by a cancelled remainder used to leave the
        # tracked position flat/wrong forever, since nothing ever corrected
        # this optimistic write). `exit_quantity` (computed above from
        # signal.quantity/reduce_fraction) is unchanged by Track 18's
        # gate above (a binary allow/reject, not a proportional cap -- see
        # that block's own comment for why).
        result = await self.lifecycle_manager.request_exit(account, symbol, exit_quantity, source=source)
        # TRK-23: Ensure the result has the correct signal_id for this CLOSE signal,
        # since request_exit doesn't know about the signal context (it only knows
        # account/symbol/quantity). This is essential for the export envelope and
        # order journal to correctly attribute the close to this signal.
        result = replace(result, signal_id=signal.id)


        # TRK-22: AUD-01's distinct-field quantity model for this managed
        # close -- see this method's own docstring for why `request_exit`'s
        # OWN internal `broker.place_order` call can't be pointed at
        # `_submit_order` directly, and the one disclosed approximation this
        # implies (`available` standing in for `request_exit`'s own,
        # possibly narrower, internal `ReductionPlan.requested_quantity`,
        # which this method has no way to read back).
        #
        # REJECTED/ERROR (whether from one of `request_exit`'s own
        # pre-submission checks -- "no active lifecycle", "halted", a prior
        # unresolved exit, a failed stop cancel/reservation -- or a real
        # broker-confirmed/ambiguous outcome from `_submit_exit_order`
        # inside it): this method can't tell those apart from `result`
        # alone, so it uses the one classification that's honest for EITHER
        # cause -- nothing confirmed applied/acknowledged, nothing
        # outstanding left to poll for THIS call -- exactly `_submit_order`'s
        # own fallthrough for these two statuses.
        applied_quantity: float | None = None
        confirmed_cumulative_fill: float | None = None
        outstanding_possible_fill = 0.0
        if result.status == OrderStatus.FILLED:
            # `request_exit`'s own synchronous FILLED branch already called
            # `_apply_exit_fill` -> `record_fill` with exactly
            # `actual_filled` (`result.filled_quantity`, defaulting to 0.0
            # when the broker didn't report one -- NOT the full `available`,
            # unlike `_submit_order`'s own FILLED convention; see this
            # method's docstring: `request_exit`'s real internal default
            # differs and this reports what actually happened, never a
            # hypothetical). `_apply_exit_fill` itself only calls
            # `record_fill` when that quantity is `> 0`, so `applied_quantity`
            # mirrors that same gate here rather than assume it happened.
            actual_filled = result.filled_quantity if result.filled_quantity is not None else 0.0
            if actual_filled > 0:
                applied_quantity = actual_filled
            confirmed_cumulative_fill = result.filled_quantity
        elif result.status == OrderStatus.PENDING:
            # `request_exit`'s own PENDING branch returns straight through
            # without ever calling `_apply_exit_fill` -- unlike
            # `_handle_managed_entry`'s extra synchronous-partial-fill
            # branch, there is no equivalent immediate application here, so
            # `applied_quantity` stays None; only `resolve_pending_exit`
            # (app/reconciliation.py's polling) can ever apply this one.
            confirmed_cumulative_fill = result.filled_quantity
            outstanding_possible_fill = exit_quantity - (confirmed_cumulative_fill or 0.0)
        applied_execution_delta = applied_quantity if applied_quantity is not None else 0.0
        acknowledged_quantity = quantity_module.acknowledged_quantity_for(result, exit_quantity)
        return _ManagedOrderOutcome(
            result,
            None,
            None,
            applied_quantity=applied_quantity,
            confirmed_cumulative_fill=confirmed_cumulative_fill,
            applied_execution_delta=applied_execution_delta,
            outstanding_possible_fill=outstanding_possible_fill,
            acknowledged_quantity=acknowledged_quantity,
        )

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

        # D-07/F-01: determine the exit path by lifecycle existence, not by
        # the managed_lifecycle flag. If a lifecycle exists, it must be
        # closed through the managed path (to handle the protective stop).
        # Otherwise, use the plain path.
        lifecycle_before_close = self.lifecycle_manager.get_lifecycle(account.account_id, symbol)
        use_managed_path = lifecycle_before_close is not None

        if use_managed_path:
            # E06: the resolved opposing side (BUY/SELL), not Side.CLOSE --
            # save_order_result's own docstring says "for a resolved close,
            # side is the opposing buy/sell, not Side.CLOSE" (the plain-
            # account path below already honors this), but this branch
            # used to save Side.CLOSE regardless, making it impossible to
            # tell a managed close's actual trade direction from the
            # `orders` table alone -- exactly what a P&L/execution-journal
            # report needs to reconstruct realized gains correctly.
            resolved_side = lifecycle_before_close.exit_side if lifecycle_before_close is not None else Side.CLOSE

            # Track 18: manual flatten is explicitly NOT provider-scoped
            # (see this method's own docstring) -- `close_signal.source`
            # is a synthetic reason string (e.g. "manual_exit"), never a
            # real provider, so ownership-gating it would incorrectly
            # reject the one action meant to flatten the WHOLE pooled
            # position regardless of which provider(s) built it.
            managed_outcome = await self._handle_managed_close(
                close_signal, account, symbol, source=reason, enforce_provider_ownership=False
            )
            result = managed_outcome.result
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
            # TRK-23: same audit gap as `_handle_signal`'s managed branch
            # (see its own comment) -- a manual "Exit now"/"Flatten" fill
            # never exported an EXECUTION_APPLIED envelope either.
            # `resolved_side` above is already the real BUY/SELL for every
            # case that can reach FILLED (it only falls back to the
            # sentinel `Side.CLOSE` when `lifecycle_before_close` is None,
            # i.e. "no open position to close," which never fills) --
            # guarded here anyway rather than trusted blindly, since
            # `build_execution_applied_envelope` itself raises for
            # `Side.CLOSE` rather than silently mis-exporting it.
            export_envelope = (
                self._build_export_envelope(
                    result,
                    account=account,
                    symbol=symbol,
                    side=resolved_side,
                    asset_class=(
                        lifecycle_before_close.plan.asset_class
                        if lifecycle_before_close is not None
                        else close_signal.asset_class
                    ),
                    originating_source_event_id=close_signal.id,
                    originating_analyst_id=None,
                )
                if resolved_side != Side.CLOSE
                else None
            )
            # TRK-22: same fix as `_handle_signal`'s managed branch -- see
            # its own comment for exactly what was missing before.
            self.store.save_order_result(
                result,
                broker=account.broker,
                symbol=symbol,
                side=resolved_side,
                applied_quantity=managed_outcome.applied_quantity,
                confirmed_cumulative_fill=managed_outcome.confirmed_cumulative_fill,
                applied_execution_delta=managed_outcome.applied_execution_delta,
                outstanding_possible_fill=managed_outcome.outstanding_possible_fill,
                acknowledged_quantity=managed_outcome.acknowledged_quantity,
                export_envelope=export_envelope,
                purpose="close",
                family_id=family_id,
            )
        else:
            # Goes through the same (account_id, symbol) lock as a provider-driven
            # CLOSE signal (see _resolve_and_submit_plain_close) -- a dashboard
            # "Exit now"/"Flatten" click can't race a concurrent provider EXIT
            # signal, or a second click, into a double-sell. This also persists
            # its own order-result row, so no separate save here.
            # Track 18: same reasoning as the managed branch above -- manual
            # flatten bypasses provider-ownership gating entirely.
            result = await self._resolve_and_submit_plain_close(
                close_signal, account, symbol, broker, enforce_provider_ownership=False
            )
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
