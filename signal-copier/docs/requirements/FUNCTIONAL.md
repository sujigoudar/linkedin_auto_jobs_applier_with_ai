# Functional Requirements

What signal-copier actually does, derived from what is implemented in
the codebase — not an aspirational feature list. Each item names the
real module/mechanism it maps to.

## 1. Signal ingestion

- Accepts trade signals from multiple source adapters (webhook, SMS/
  Twilio, WhatsApp, NinjaTrader, a free-text parser, and others under
  `app/sources/`), each normalizing its own wire format into a common
  `Signal` (`app/models.py`): `source`, `symbol`, `side`
  (buy/sell/close), `asset_class`, optional `analyst`, `quantity`,
  `price`, `stop_loss`, `take_profit`.
- Every live-received signal is persisted (`SignalStore.save_signal`,
  the `signals` table) before routing.
- A separate, owner-gated batch-import workflow
  (`POST /sources/{source}/import-signals`) can classify and import
  historical messages, tagged with `import_batch` so a backfilled
  signal is always distinguishable from a live-received one.

## 2. Routing

- `app/routing.py`'s `RoutingConfig` maps each signal's `source`
  (optionally narrowed by `symbol_filter`) to one or more destination
  accounts (`config_routing_rules`, or the YAML equivalent imported
  once at first boot).
- `app/engine.py`'s `SignalCopierEngine` is the sole place that wires a
  signal to every configured destination account, sized and
  symbol-mapped per account — sources and brokers know nothing about
  each other.
- Per-account, per-provider, and per-analyst settings overrides
  (`app/providers.py`'s `ProviderRegistry`) narrow an account's
  multiplier/fixed_quantity/managed_lifecycle/enabled based on
  `signal.source`/`signal.analyst`, on top of the account's own
  base configuration.

## 3. Risk sizing and admission

- Per-account position sizing via `multiplier`/`fixed_quantity` and a
  configurable `symbol_map` for broker-specific ticker naming.
- Capital admission gating (`app/capital_allocator.py`,
  `docs/design/CAPITAL_ALLOCATION.md`): an opt-in per-account notional
  ceiling, an opt-in owner-wide notional ceiling, and opt-in
  risk-basis (percent-of-equity) sizing, all fail-closed (ADR-0007).
- A `CLOSE` signal is resolved per destination account against that
  account's own tracked position (`positions.net_quantity`) — it is
  never a fixed size; a flat account rejects it without a broker call.

## 4. Order execution

- Broker adapters (`app/brokers/*.py`: Alpaca, CCXT, SignalStack,
  NinjaTrader, a paper/simulated adapter, and others) each implement a
  common `BrokerAdapter` interface (`place_order`,
  `get_account_balance`, capability introspection, and — for brokers
  that support it — `cancel_order`/`replace_stop_quantity`/
  `get_broker_position`).
- Every real broker-write call site opens a pre-effect
  `command_ledger` entry before calling the broker, and resolves it
  with the real outcome afterward (`app/command_ledger.py`,
  ADR-0004) — idempotent by `idempotency_key`, with a deterministic
  `request_fingerprint` guarding against a reused key describing a
  different command.
- Order results are recorded in `orders` (`SignalStore.
  save_order_result`) using the distinct-quantity model
  (`requested_quantity`/`confirmed_cumulative_fill`/
  `applied_execution_delta`/`outstanding_possible_fill` — ADR-0005),
  never optimistically applying an unconfirmed PENDING fill to
  `positions.net_quantity`.
- Cross-process/cross-instance mutual exclusion for a plain-account
  close (`close_claims`, a UNIQUE-constraint claim) prevents two
  processes racing the same close.

## 5. Position management — two distinct products (ADR-0008)

- **Plain/unmanaged** (`ManagementRecipe.PLAIN_UNMANAGED`): entries/
  exits are plain BUY/SELL/CLOSE orders against this service's own
  tracked position. A plain account's CLOSE additionally requires
  either a fresh broker position readback matching the tracked
  quantity, or the account's explicit
  `exclusive_writer_qualified` assertion — never a close that proceeds
  against a possibly-stale local projection.
- **Full managed lifecycle** (`ManagementRecipe.FULL_MANAGED_LIFECYCLE`,
  `app/lifecycle/manager.py`, `docs/design/POSITION_LIFECYCLE.md`):
  protect-first entry handling, logical profit targets and trailing
  stops evaluated in-process against a price feed, a serialized
  `CloseArbiter` coordinating every exit path (target fire, trailing
  ratchet, stop fill, provider EXIT signal, time exit, emergency exit)
  so no two exit paths can oversell the same shares.
- Crash-resumable managed-lifecycle state (`lifecycle_state`) survives
  a process restart for any position still open when it saved.

## 6. Reconciliation

- `app/reconciliation.py` polls PENDING orders with a real
  `broker_order_id` to a terminal status, releasing capital
  reservations and resolving `PendingEntry`/`PendingExit` states as
  real outcomes are observed — never guessed.
- Startup reconciliation against a broker's own live position/order
  state is a named, documented gap for managed-lifecycle positions
  (see `docs/design/POSITION_LIFECYCLE.md`'s "Documented gap" section)
  — not implemented as of this document.

## 7. Analytics and audit

- Real MAE/MFE excursion tracking per closed position
  (`position_excursions`), a append-only stop/target event history
  (`stop_target_events`), and periodic equity/P&L snapshots
  (`account_equity_snapshots`) — all populated only from genuine,
  broker-confirmed observations, never fabricated or estimated
  figures.
- Backtest replay (`app/backtest/replay.py`, persisted in
  `backtest_runs`) against historical CSV data, with a config hash
  guaranteeing two runs sharing a hash replayed identical inputs.
- Provider/subscription cost tracking (`provider_subscriptions`) and
  automated provider-candidate scoring (`provider_candidates`,
  `app/provider_scout.py`).

## 8. Operational safety

- Single-writer enforcement via a monotonic fencing token
  (`app/writer_lease.py`, `docs/design/WRITER_FENCING.md`, ADR-0002).
- Deliberately manual failover: promotion requires a human running
  `python -m app.promote_cli promote` with three explicit confirmation
  flags (`docs/FAILOVER.md`, ADR-0003).
- Per-route qualification ladder (`app/qualification.py`,
  ADR-0006) tracking whether a specific broker/venue/product
  combination has actually been verified end-to-end for live trading,
  separate from code-level capability introspection.
- Owner-only authentication (`sessions`, CSRF double-submit defense)
  and idempotent financial-command endpoints
  (`idempotency_records`).

## Explicitly out of scope (not aspirational gaps to be filled silently)

- Multi-tenant / multi-owner support — this is a single-owner engine
  throughout (`config_accounts`/`sessions` carry no per-user scoping).
- Multi-currency conversion in the capital allocator.
- Automatic failover of any kind.
- A complete institutional risk engine (stress-loss/scenario modeling,
  cross-account netting beyond plain summation, per-analyst overlap
  accounting) — see `app/capital_allocator.py`'s own disclosed scope
  boundaries.
