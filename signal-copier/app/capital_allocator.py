"""E03 (extended): capital/risk admission gate closing the races and
fail-open gaps the adoption plan's E03 acceptance obligations call out --
"competing signals cannot independently spend the same capacity" -- PLUS
the specific gaps a later external release audit named:

1. Admission used to be silently SKIPPED whenever the admitting signal had
   no `price` at all, even when a real ceiling/risk gate was configured for
   the account. Skipping is a PASS by omission -- economically identical to
   admitting an unbounded, unsized order. Fixed: whenever ANY gate is
   configured for an account (a notional ceiling, owner-wide ceiling, or
   risk-basis sizing) and the signal carries no price this module has no
   other way to resolve one for, admission is now REJECTED, not skipped.
2. A position this replay could not resolve an `average_cost` for (see
   `AccountEconomics.incomplete_symbols` in app/economics.py) used to
   contribute exactly 0.0 to `confirmed_open_notional`'s total -- treating
   genuinely UNKNOWN exposure as economically equivalent to NO exposure.
   For a hard risk gate that is never safe: real notional could be sitting
   there, invisible to the ceiling. Fixed: `confirmed_open_notional` now
   reports which symbols it could not resolve (`ExposureReport.
   unresolved_symbols`), and every admission gate in this module treats a
   non-empty list as an automatic REJECT for that account -- new
   admissions are blocked until the position resolves (chosen over
   inventing a "last-known-good mark" estimate: this replay has no
   reliable historical mark to fall back to beyond the fill rows
   `app/economics.py` itself already declined to trust for this symbol --
   see that module's own "incomplete stays incomplete" rule -- so a
   fabricated number would be worse than refusing to size against it at
   all).

Scope, still deliberately narrower than a complete institutional risk
engine:

- NOTIONAL exposure ceiling, now enforced at two levels:
  - Per-account (`DestinationAccount.max_notional_exposure`, opt-in, None
    by default) -- unchanged from the original E03 slice.
  - Owner-wide (`app.config.MAX_OWNER_NOTIONAL_EXPOSURE`, opt-in, None by
    default) -- sums `confirmed_open_notional` + this process's own
    pending reservations across EVERY account this single-tenant
    deployment's `RoutingConfig` knows about (this service has exactly one
    owner/config; "every configured account" already IS "owner-wide" --
    see `owner_wide_exposure` below). This is a genuinely new dimension
    the original bounded slice explicitly disclosed as missing.
- RISK-BASIS sizing (`DestinationAccount.risk_percent_of_equity`, opt-in,
  None by default): reject an entry whose risk-to-stop (`|entry_price -
  stop_loss| * quantity`) would exceed that percentage of the account's
  real, freshly-fetched equity (`BrokerAdapter.get_account_balance`).
  Fails closed (rejects) whenever `stop_loss` is missing on the signal, or
  the broker can't report a real `equity` figure -- never a guessed or
  cached value (see `app/engine.py`'s `_check_risk_basis` for exactly what
  it requires and refuses).
- "Current exposure" is still the account's REAL, confirmed open notional
  -- `abs(open_quantity) * average_cost` per RESOLVED symbol, replayed
  from the same confirmed-fill journal app/economics.py already trusts
  (never a separately-maintained running total that could drift from what
  actually executed) -- now paired with the unresolved-symbol list above
  so "resolved total" and "known to be incomplete" are never conflated.
- What the original race-closing mechanism still closes: two signals for
  the same account, arriving concurrently in this process, both reading
  that same confirmed exposure before either one's order has been placed,
  and both being admitted even though only one fits under the ceiling.
  `admit()` holds a per-account asyncio.Lock across "read confirmed
  exposure, check, provisionally reserve" so the second concurrent caller
  sees the first's reservation before deciding. `owner_lock` extends this
  the same way across the owner-wide gate (see `CapitalAllocator.
  owner_lock`'s own docstring for the exact race it closes and the one it
  still doesn't). The provisional reservation is released as soon as that
  order call returns for REJECTED/ERROR (nothing happened, nothing to keep
  reserved) and for FILLED (the fill is immediately part of the confirmed
  exposure the next admission call will see).
- **PENDING reservation timing (closed for the pollable case):** a PENDING
  order is NOT yet part of confirmed exposure (confirmed exposure only
  counts a symbol once its `average_cost` is resolvable from an actual
  recorded fill -- see `confirmed_open_notional` below), so releasing its
  reservation immediately, the same as REJECTED/ERROR/FILLED, would
  briefly count it toward neither the reservation ledger nor confirmed
  exposure -- a second signal admitted in that window could push real
  total exposure past the configured ceiling. When the PENDING result
  carries a real `broker_order_id`, app/reconciliation.py's polling loop
  is guaranteed to eventually observe this exact order reach a terminal
  status (FILLED/REJECTED) and is the one place allowed to release the
  reservation then -- see app/engine.py's `_try_reserve_capital`
  docstring and both its callers, `app/db.py`'s
  `save_order_result(reserved_notional=...)` for a plain account, and
  `app/lifecycle/manager.py`'s `PendingEntry.reserved_notional` /
  `resolve_pending_entry` for a managed_lifecycle account.
  **Still not closed:** a PENDING result with no `broker_order_id` at
  all (nothing to poll -- the same ambiguous case as EXE-01/EXE-01b's
  raised-or-returned ERROR) still releases immediately, same as before
  this fix. Deferring release for an order nothing is guaranteed to ever
  revisit would risk a reservation that's never released, which would be
  worse (silently blocking all future admissions for that account) than
  the timing gap it would close -- this is a narrower, deliberately
  bounded slice of the original gap, not the whole thing.
- **P0-4: this provisional ledger is now durable across a restart.**
  Previously this docstring claimed "a restart has no in-flight
  admissions to lose (nothing survives a request that never returned)"
  -- an external release audit correctly called that out as unsound: a
  remote broker can accept an order (see e.g. `_try_reserve_capital`'s
  own PENDING-with-broker_order_id case) and this process can still die
  before anything else observes that acceptance. `admit()` and
  `reserve_locked()` now write a durable row to the `capital_reservations`
  table (see app/db.py's own SCHEMA comment on it) the INSTANT admission
  succeeds -- before the broker call it's gating even starts, not after
  the fact the way `orders.reserved_notional` already was.
  `CapitalAllocator.__init__` reloads every still-unresolved row, summed
  per account, into `_pending` when constructed with a `store` -- so a
  fresh instance built against the SAME database a crashed process was
  using resumes with exactly the reservations that process couldn't
  finish resolving, not a clean slate. Confirmed exposure is still always
  freshly recomputed from the store, never from this ledger -- only the
  PROVISIONAL half changed. `CapitalAllocator()` with no `store` (e.g.
  app/backtest/replay.py's synthetic allocator) behaves exactly as before
  this change -- no table writes, no restore, pure in-memory -- since a
  backtest has no real broker and nothing to survive a restart for.

  Not yet integrated with a `command_ledger`/command-id concept: at the
  time this was written, no sibling branch work introducing one had
  landed here yet (see `capital_reservations`' own SCHEMA comment for
  the specific follow-up this leaves for whoever lands it).

**Still genuinely, honestly unimplemented** (named here so no one mistakes
the above for a complete institutional risk engine):

- Multi-currency / basis-currency conversion. Every notional figure this
  module produces is summed in whatever units the signal's own `price`
  and the account's own fills are already denominated in -- an account
  trading EURUSD and one trading a USD equity are summed as if their
  numbers were directly comparable. A real implementation needs a genuine
  FX rate source (this codebase has none) and a declared basis currency
  per owner/account; inventing a rate would be worse than the current,
  disclosed non-conversion.
- Per-analyst overlap accounting. Two signals from different analysts (or
  different sources) both entering the same underlying symbol are not
  netted, correlated, or otherwise treated as a shared/overlapping risk
  bucket anywhere in this module -- each is sized and gated purely on the
  DESTINATION ACCOUNT's own notional/risk state.
- Stress-loss / scenario modeling (e.g. "what does this account's open
  book lose under a defined adverse move"). Nothing here estimates a
  loss distribution or worst-case drawdown beyond the literal
  risk-to-configured-stop figure risk-basis sizing computes per NEW entry
  -- there is no portfolio-level stress test.
- Cross-account netting for the SAME owner beyond simple summation
  (`owner_wide_exposure` below sums accounts; it does not net a long on
  one account against a short on another of the same underlying).
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.db import SignalStore
from app.economics import compute_account_economics


@dataclass
class ExposureReport:
    """`confirmed_open_notional`'s real result: the resolved notional total
    PLUS which symbols (if any) this replay could not resolve an
    `average_cost` for and therefore could not include in that total.

    `unresolved_symbols` non-empty means `notional` is a KNOWN
    UNDERSTATEMENT of this account's true open exposure, never a
    reliable "the rest is zero" figure -- every caller in this module
    treats a non-empty list as grounds to reject new admissions for the
    account (see this module's own docstring, point 2)."""

    notional: float
    unresolved_symbols: list[str] = field(default_factory=list)

    @property
    def has_unresolved(self) -> bool:
        return bool(self.unresolved_symbols)


def confirmed_open_notional(store: SignalStore, account_id: str) -> ExposureReport:
    """Sum of |open_quantity| * average_cost across every symbol this
    account currently holds a RESOLVED position in, per the confirmed-fill
    replay app/economics.py already performs, plus the list of symbols
    that replay flagged as unresolvable (`AccountEconomics.
    incomplete_symbols`) -- a fill this replay couldn't use (missing/
    invalid quantity or price, or an unresolved side). An unresolved
    symbol is NEVER folded into `notional` as if it contributed 0.0; it is
    surfaced separately so a caller enforcing a hard risk gate can refuse
    to admit against unknown exposure (see `ExposureReport`'s own
    docstring and this module's docstring, point 2) instead of silently
    treating "unknown" as "safe"."""
    economics = compute_account_economics(store, account_id)
    total = 0.0
    for symbol_economics in economics.per_symbol.values():
        if symbol_economics.average_cost is None:
            # Only reachable when open_quantity == 0 too (a fully closed
            # position -- see app/economics.py's `compute_account_economics`),
            # so contributing 0.0 here is exact, not a fallback: there is
            # truly no notional to add for a flat position.
            continue
        total += abs(symbol_economics.open_quantity) * symbol_economics.average_cost
    return ExposureReport(notional=total, unresolved_symbols=list(economics.incomplete_symbols))


def confirmed_strategy_notional(store: SignalStore, strategy_key: str) -> ExposureReport:
    """ALLOC-03: the strategy's confirmed open notional summed across EVERY
    account it has filled orders on -- each account's confirmed-fill replay
    filtered to this strategy's own signals (same replay as
    `confirmed_open_notional`, never a second derivation). Unresolved
    symbols are surfaced, not folded in as zero."""
    total = 0.0
    unresolved: list[str] = []
    for account_id in store.list_accounts_with_fills_for_source(strategy_key):
        economics = compute_account_economics(store, account_id, source=strategy_key)
        for symbol_economics in economics.per_symbol.values():
            if symbol_economics.average_cost is None:
                continue
            total += abs(symbol_economics.open_quantity) * symbol_economics.average_cost
        unresolved.extend(f"{account_id}:{symbol}" for symbol in economics.incomplete_symbols)
    return ExposureReport(notional=total, unresolved_symbols=unresolved)


def owner_wide_exposure(
    store: SignalStore, accounts: Iterable, allocator: "CapitalAllocator"
) -> ExposureReport:
    """Sums `confirmed_open_notional` + this process's own real pending
    reservation across EVERY account passed in -- in practice, every
    account this deployment's single `RoutingConfig` knows about, which
    for this single-tenant service already IS "every account under this
    owner" (there is no multi-owner/multi-tenant concept in this
    codebase's config model -- see app/routing.py's `RoutingConfig`).

    Does NOT convert currencies (see this module's docstring's "Still
    genuinely, honestly unimplemented" section) and does NOT net a long on
    one account against a short on another of the same underlying -- it is
    a plain sum, nothing more.

    `unresolved_symbols` is the union (prefixed `account_id:symbol` so two
    accounts' own unresolved lists can never be confused with each other)
    of every contributing account's own unresolved symbols -- non-empty
    means the owner-wide total, like any one account's own, is a known
    understatement, and callers must treat that as a reject the same way
    a single account's own unresolved exposure is treated."""
    total = 0.0
    unresolved: list[str] = []
    for account in accounts:
        report = confirmed_open_notional(store, account.account_id)
        total += report.notional + allocator.pending_reservation(account.account_id)
        unresolved.extend(f"{account.account_id}:{symbol}" for symbol in report.unresolved_symbols)
    return ExposureReport(notional=total, unresolved_symbols=unresolved)


class CapitalAllocator:
    """One instance shared by the engine for its whole lifetime (like
    PositionLifecycleManager) -- per-account locks and provisional
    reservations only mean anything shared across every call.

    `store` (P0-4, optional): when given, every reservation `admit()`
    grants is also durably recorded (see app/db.py's own
    `capital_reservations` SCHEMA comment), and `__init__` reloads any
    still-unresolved reservation left behind by a previous process
    against this SAME database -- see this module's own docstring.
    `None` (the default) keeps this exactly the in-memory-only ledger it
    always was, for a caller with no real database to survive a restart
    against (app/backtest/replay.py's synthetic allocator)."""

    def __init__(self, store: SignalStore | None = None) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pending: dict[str, float] = defaultdict(float)
        self.store = store
        if store is not None:
            # P0-4: rebuild in-memory state from whatever this database
            # still shows as unresolved -- NOT a clean slate. Any
            # reservation a previous process made but never resolved
            # (no confirming fill, no confirming rejection observed
            # before it died) stays reserved here until reconciliation
            # independently confirms its outcome, same as it would have
            # if this process had never restarted at all.
            for account_id, notional in store.sum_unresolved_capital_reservations().items():
                self._pending[account_id] = notional
        #: Serializes any admission decision that needs to read/compare
        #: pending reservations across MORE than one account -- today,
        #: only the owner-wide exposure gate (`owner_wide_exposure`).
        #: Acquired BEFORE the per-account lock (see `account_lock`) by
        #: every caller that needs it, so two concurrent owner-wide checks
        #: can never both read the same not-yet-reserved state and both
        #: admit past the owner-wide ceiling -- the same race `admit()`'s
        #: own per-account lock already closes for the single-account
        #: case, extended to a sum across accounts.
        #: **Still not closed:** an admission that only enforces a
        #: PER-ACCOUNT gate (no owner-wide ceiling configured at all, so
        #: it never touches this lock) can still run concurrently with an
        #: owner-wide-gated admission on a DIFFERENT account and mutate
        #: that other account's own `_pending` entry mid-computation --
        #: acceptable because the owner-wide gate is opt-in and off by
        #: default; closing this fully would mean funneling every
        #: admission through one global lock regardless of whether any
        #: account actually uses the owner-wide gate, which would
        #: needlessly serialize the common case for a feature most
        #: deployments won't enable.
        self.owner_lock = asyncio.Lock()

    def account_lock(self, account_id: str) -> asyncio.Lock:
        """The exact same per-account lock `admit()`/`release()` use
        internally -- exposed so a caller enforcing gates BEYOND the plain
        single-ceiling case `admit()` covers (unresolved-exposure checks,
        the owner-wide gate, risk-basis sizing) can hold it across its own
        read-check-reserve sequence too, instead of reaching into the
        "private" `_locks` dict directly. See `reserve_locked` for the
        matching lock-free reservation primitive meant to be called while
        already holding this."""
        return self._locks[account_id]

    async def admit(
        self,
        account_id: str,
        notional: float,
        *,
        confirmed_exposure: float,
        max_exposure: float,
        signal_id: str | None = None,
    ) -> bool:
        async with self._locks[account_id]:
            if confirmed_exposure + self._pending[account_id] + notional > max_exposure:
                return False
            if self.store is not None and notional:
                # P0-4: durably reserved BEFORE this coroutine returns to
                # its caller -- i.e. before the broker call `admit()` is
                # gating ever starts. See this class's own docstring for
                # why that ordering (not "record it after the broker
                # call returns", which is all `orders.reserved_notional`
                # already did) is the actual point.
                self.store.create_capital_reservation(
                    str(uuid.uuid4()), account_id, notional, signal_id=signal_id
                )
            self._pending[account_id] += notional
            return True

    def reserve_locked(
        self,
        account_id: str,
        notional: float,
        *,
        signal_id: str | None = None,
        strategy_key: str | None = None,
    ) -> None:
        """Reserve `notional` against `account_id` WITHOUT acquiring
        `account_lock(account_id)` -- the caller MUST already hold that
        lock (typically because it's enforcing more than one gate
        atomically in one locked section; `admit()` above is the
        single-gate, self-locking convenience wrapper most callers want
        instead). P0-4: durably reserved the same way `admit()` is (see
        its own docstring) whenever `self.store` is set -- a caller using
        this instead of `admit()` (this module's own multi-gate
        `_try_reserve_capital` orchestration in app/engine.py) must not
        lose that same restart-survival guarantee."""
        if self.store is not None and notional:
            self.store.create_capital_reservation(
                str(uuid.uuid4()), account_id, notional, signal_id=signal_id, strategy_key=strategy_key
            )
        self._pending[account_id] += notional

    def reserve_with_strategy_ceiling(
        self,
        account_id: str,
        notional: float,
        *,
        signal_id: str | None,
        strategy_key: str,
        ceiling: float,
    ) -> tuple[bool, float, float]:
        """ALLOC-03 joint admission step. Caller already holds
        `account_lock(account_id)` (account gates already passed). The
        strategy check + durable reservation insert is ONE atomic SQLite
        transaction, so the strategy ceiling holds across accounts AND
        across processes; the in-memory account ledger is only advanced
        once that transaction admitted. Requires a store."""
        assert self.store is not None, "strategy ceilings need a durable store"
        admitted, confirmed, pending = self.store.reserve_strategy_checked(
            str(uuid.uuid4()),
            account_id,
            notional,
            signal_id=signal_id,
            strategy_key=strategy_key,
            ceiling=ceiling,
            confirmed_notional=lambda: confirmed_strategy_notional(self.store, strategy_key).notional,  # type: ignore[arg-type]
        )
        if admitted:
            self._pending[account_id] += notional
        return admitted, confirmed, pending

    def release(self, account_id: str, notional: float, *, signal_id: str | None = None) -> None:
        self._pending[account_id] = max(0.0, self._pending[account_id] - notional)
        if self.store is not None:
            self.store.resolve_one_capital_reservation(account_id, None, notional, signal_id=signal_id)

    def pending_reservation(self, account_id: str) -> float:
        """Phase B7: this account's real, current in-memory provisional
        reservation (notional admitted via `admit()`/`reserve_locked()`
        and not yet released) -- the one figure this module tracks that
        `confirmed_open_notional` above does NOT already cover (that
        function only ever replays confirmed fills). Read-only: never
        mutates `_pending`. Exposed as a plain accessor (rather than
        reading `_pending` directly from outside this module) so a
        read-only GET endpoint (app/main.py's `/capital-allocation`) has a
        stable, narrow surface onto this otherwise process-internal
        ledger instead of reaching into a "private" attribute."""
        return self._pending[account_id]
