# Database Schema

Source of truth: `app/db.py`'s `SCHEMA` string (the canonical, always
up-to-date DDL every `SignalStore` bootstraps against) and
`_COLUMN_MIGRATIONS` (additive columns bolted onto already-existing
tables — see `docs/database/MIGRATIONS.md` for the migration policy
these two mechanisms sit inside). Every table below is real and
present in `app/db.py`'s `SCHEMA` string as of this document. Column
lists are complete; prose is a compressed version of `app/db.py`'s own
inline comments — read that file directly for the full reasoning.

For field-level semantics of the trickiest columns, see
`docs/database/DATA_DICTIONARY.md` (this document intentionally does
not repeat that depth here).

---

## `signals`

The normalized, source-agnostic trade instruction every adapter
produces (`app/models.py`'s `Signal`).

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | uuid4 |
| `source` | TEXT NOT NULL | which provider/channel this came from |
| `symbol` | TEXT NOT NULL | |
| `side` | TEXT NOT NULL | `buy` / `sell` / `close` |
| `asset_class` | TEXT NOT NULL | crypto/forex/equity/option/future |
| `quantity` | REAL | |
| `price` | REAL | |
| `stop_loss` | REAL | added by `_COLUMN_MIGRATIONS` |
| `take_profit` | REAL | added by `_COLUMN_MIGRATIONS` |
| `analyst` | TEXT | added by `_COLUMN_MIGRATIONS`; who within `source` posted this |
| `received_at` | TEXT NOT NULL | ISO timestamp |
| `raw` | TEXT NOT NULL | JSON of the original payload |
| `import_batch` | TEXT | `NULL` for every live-received signal; a batch label only for one created by the owner-gated history-import workflow (E02) |

## `orders`

One row per broker order attempt (or a rejection with no broker
submission at all).

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `account_id` | TEXT NOT NULL | |
| `broker` | TEXT | |
| `symbol` | TEXT | |
| `side` | TEXT | the side actually sent (a resolved close's opposing buy/sell, not `Side.CLOSE`) |
| `requested_quantity` | REAL | what was asked for |
| `signal_id` | TEXT NOT NULL | FK -> `signals.id` (enforced — `PRAGMA foreign_keys=ON`) |
| `status` | TEXT NOT NULL | pending/filled/rejected/error |
| `broker_order_id` | TEXT | |
| `filled_quantity` | REAL | |
| `filled_price` | REAL | |
| `message` | TEXT | |
| `executed_at` | TEXT NOT NULL | |
| `submitted_at` | TEXT | PU-A2 multi-stage execution-latency timestamp; `NULL` = never reached that stage |
| `protection_confirmed_at` | TEXT | PU-A2; `NULL` for a non-managed account or an unconfirmed stop |
| `reserved_notional` | REAL | E03; set only while `status='pending'` with a real `broker_order_id` to poll — see `DATA_DICTIONARY.md` |
| `purpose` | TEXT | `entry` / `close` (only values a call site populates today) — see `DATA_DICTIONARY.md` |
| `family_id` | TEXT | groups every order in one position episode — see `DATA_DICTIONARY.md` |
| `confirmed_cumulative_fill` | REAL | AUD-01 distinct-quantity model — see `DATA_DICTIONARY.md` |
| `applied_execution_delta` | REAL | AUD-01 — see `DATA_DICTIONARY.md` |
| `outstanding_possible_fill` | REAL | AUD-01 — see `DATA_DICTIONARY.md` |

Indexes: `idx_orders_executed_at`, `idx_orders_account_id`.

## `positions`

Net position per (account, symbol), maintained by the engine so a
`close` signal knows what to close. Positive = net long, negative =
net short, zero = flat. **This service's own record of what it has
sent, not a live read of the broker's actual position.**

| column | type | notes |
|---|---|---|
| `account_id` | TEXT NOT NULL | PK (with `symbol`) |
| `symbol` | TEXT NOT NULL | PK (with `account_id`) |
| `net_quantity` | REAL NOT NULL DEFAULT 0 | AUD-01: `net_quantity` **is** `actual_remaining_ownership` by contract — updated only from a broker-confirmed fill, never from a PENDING order's merely-requested quantity |
| `updated_at` | TEXT NOT NULL | |

## `lifecycle_state`

Crash-resumable state for `PositionLifecycleManager`: one row per open
managed-lifecycle position, holding its whole `PositionLifecycle`
(plan, stop, pending_exit/pending_entry) plus its `CloseArbiter` ledger
snapshot as one JSON blob. Written after every state-changing
transition; deleted once the position closes.

| column | type | notes |
|---|---|---|
| `account_id` | TEXT NOT NULL | PK (with `symbol`) |
| `symbol` | TEXT NOT NULL | PK (with `account_id`) |
| `state` | TEXT NOT NULL | JSON snapshot |
| `updated_at` | TEXT NOT NULL | |

## `config_accounts` / `config_routing_rules` / `config_providers` / `config_analysts`

Live, GUI/API-editable configuration — the database-backed replacement
for hand-editing `config/accounts.yaml`, `config/routing.yaml`, and
`config/providers.yaml`. YAML files remain a one-time import path
(`app/config_admin.py`'s `seed_from_yaml_if_empty`), not the live
source of truth once any of these tables has a row.

**`config_accounts`**

| column | type | notes |
|---|---|---|
| `account_id` | TEXT PK | |
| `broker` | TEXT NOT NULL | |
| `multiplier` | REAL NOT NULL DEFAULT 1.0 | |
| `fixed_quantity` | REAL | |
| `symbol_map` | TEXT NOT NULL DEFAULT '{}' | JSON |
| `enabled` | INTEGER NOT NULL DEFAULT 1 | |
| `managed_lifecycle` | INTEGER NOT NULL DEFAULT 0 | |
| `max_notional_exposure` | REAL | E03 opt-in ceiling |
| `risk_percent_of_equity` | REAL | E03 opt-in risk-basis sizing |
| `management_recipe` | TEXT | P0-5; ADR-0008 |
| `qualification_level` | TEXT | P0-5; free-form label, `NULL` = not declared |
| `exclusive_writer_qualified` | INTEGER NOT NULL DEFAULT 0 | P0-5; ADR-0008 |

**`config_routing_rules`**: `id` PK, `source` NOT NULL, `destinations`
NOT NULL (JSON), `symbol_filter`.

**`config_providers`**: `provider_id` PK, `display_name` NOT NULL
DEFAULT '', `multiplier`, `fixed_quantity`, `managed_lifecycle`,
`enabled`.

**`config_analysts`**: `provider_id` + `analyst_id` composite PK,
`display_name` NOT NULL DEFAULT '', `multiplier`, `fixed_quantity`,
`managed_lifecycle`, `enabled`.

## `provider_subscriptions`

Subscription cost/billing tracking for a signal provider
(`app/provider_value.py`), keyed by `provider_id`.

| column | type | notes |
|---|---|---|
| `provider_id` | TEXT PK | |
| `display_name` | TEXT NOT NULL DEFAULT '' | |
| `cost_amount` | REAL NOT NULL DEFAULT 0.0 | cost of ONE `billing_cycle`, not a lifetime total |
| `currency` | TEXT NOT NULL DEFAULT 'USD' | |
| `billing_cycle` | TEXT NOT NULL DEFAULT 'monthly' | |
| `subscribed_since` | TEXT NOT NULL | anchors the cost-to-date estimate |
| `renewal_date` | TEXT | informational only, distinct from `subscribed_since` |
| `status` | TEXT NOT NULL DEFAULT 'active' | `candidate` = added by `app/provider_scout.py` (or manually) but not yet a real paid subscription |
| `notes` | TEXT NOT NULL DEFAULT '' | |
| `created_at` / `updated_at` | TEXT NOT NULL | |

## `provider_candidates`

`app/provider_scout.py`'s last-computed evaluation snapshot for a
(source, analyst, asset_class) that is **not** currently a
`provider_subscriptions` row — persisted so `GET /providers/candidates`
can show "last evaluated at X" without recomputing the full replay on
every dashboard load.

| column | type | notes |
|---|---|---|
| `source` | TEXT NOT NULL | PK part |
| `analyst` | TEXT NOT NULL DEFAULT '' | PK part; `''` (never NULL) so it can be part of the composite key |
| `asset_class` | TEXT NOT NULL | PK part |
| `closing_fills` | INTEGER NOT NULL | |
| `winning_closing_fills` | INTEGER NOT NULL | |
| `realized_pnl` | REAL NOT NULL | |
| `win_rate` | REAL | |
| `profit_factor` | REAL | |
| `recommendation` | TEXT NOT NULL | |
| `evaluated_at` | TEXT NOT NULL | |

## `sessions`

Server-side owner sessions (`app/auth.py`).

| column | type | notes |
|---|---|---|
| `session_id` | TEXT PK | opaque, carried in the session cookie |
| `csrf_token` | TEXT NOT NULL | returned once at login; echoed as `X-CSRF-Token` on every mutating request |
| `created_at` / `expires_at` | TEXT NOT NULL | |
| `credential_epoch` | TEXT | added by `_COLUMN_MIGRATIONS` |

Deleting a row revokes that session immediately (logout, or
owner-initiated "sign out everywhere"). Index:
`idx_sessions_expires_at`.

## `idempotency_records`

Recent financial-command results, keyed by a caller-supplied
idempotency key (`app/main.py`'s close/flatten endpoints): a retried or
duplicated request with the same key replays the stored result instead
of executing the command again.

| column | type | notes |
|---|---|---|
| `idempotency_key` | TEXT PK | |
| `response_json` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | |
| `fingerprint` | TEXT | added by `_COLUMN_MIGRATIONS` |

## `close_claims`

Cross-process/cross-instance mutual exclusion for a plain-account close
(EXE-12). `app/engine.py`'s in-memory `_plain_close_locks` only
serializes calls within one engine instance; two independent processes
sharing the same database have entirely separate lock objects. The
UNIQUE `(account_id, symbol)` primary key makes the claim atomic at the
database itself — only one process's INSERT can ever succeed at a time.

| column | type | notes |
|---|---|---|
| `account_id` | TEXT NOT NULL | PK part |
| `symbol` | TEXT NOT NULL | PK part |
| `claimed_at` | TEXT NOT NULL | |

## `writer_lease`

Cross-process/cross-host single-writer fencing (see
`docs/design/WRITER_FENCING.md`, ADR-0002, `docs/FAILOVER.md`). A
single row (`id=1`).

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK CHECK (id = 1) | singleton |
| `fencing_token` | INTEGER NOT NULL | monotonically increasing |
| `site_id` | TEXT NOT NULL | |
| `holder_id` | TEXT NOT NULL | |
| `acquired_at` / `expires_at` / `renewed_at` | TEXT NOT NULL | lease timing — never consulted by the per-command fencing check itself |

## `seed_state`

One-row marker (EXE-10) distinguishing "seeding has already happened
once" from "never seeded," so a later empty `config_accounts` from an
explicit owner deletion is never mistaken for a fresh install and
resurrected by `seed_from_yaml_if_empty`.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK CHECK (id = 1) | singleton |
| `seeded_at` | TEXT NOT NULL | |

## `export_events`

The transactional private export outbox (Signal Platform Integration
Correction Pack, S4.2/S6). A row here is written in the **same**
sqlite3 transaction as the authoritative local state change it
describes — never a best-effort HTTP POST after commit as the only
export mechanism (with the one documented exception below).

`EXECUTION_APPLIED` coverage (TRK-23): every real confirmed fill on a
`managed_lifecycle` account now builds and persists one of these, using
the same `build_execution_applied_envelope`/`_build_export_envelope`
machinery a plain account's fill already used — a synchronous managed
entry/close fill (`app/engine.py`'s `_handle_signal`/`close_position`,
same-transaction row-write as `save_order_result`), and a protective stop
filling on its own with no `orders`-row-writing caller of its own
(`PositionLifecycleManager.on_stop_filled` via
`_apply_exit_fill`/`_persist_self_initiated_exit`, also same-transaction).
An asynchronously-confirmed managed entry/CLOSE
(`OrderReconciler.reconcile_once`'s own lifecycle-owned-pending-order
branch) is the one documented exception: like the plain-account
reconciler's own `_export_reconciled_fill`, it appends its envelope in
its own separate transaction (`append_export_event`), since the
`orders` row it's confirming was already committed earlier at placement
time.

| column | type | notes |
|---|---|---|
| `event_id` | TEXT PK | producer's own idempotency key |
| `event_type` | TEXT NOT NULL | |
| `source_stream` | TEXT NOT NULL | |
| `export_sequence` | INTEGER NOT NULL | this producer's own monotonic position within `source_stream`; UNIQUE with `source_stream` |
| `envelope_json` | TEXT NOT NULL | full `signal_platform_contracts.EventEnvelope`, serialized once at append time — the relay only ever forwards these exact bytes |
| `payload_hash` | TEXT NOT NULL | |
| `appended_at` | TEXT NOT NULL | |
| `delivered_at` | TEXT | `NULL` = undelivered |

Indexes: `idx_export_events_undelivered` (partial, `WHERE delivered_at
IS NULL`).

## `position_excursions`

PU-A1: durable, per-closed-position record of real-price-driven
MAE/MFE tracking. One row per completed position episode, written
**once** a position fully closes (`app/lifecycle/manager.py`'s
`_persist_closed_excursion`), so it remains queryable for
analytics/charting after `lifecycle_state`'s own in-progress row is
deleted.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `account_id` / `symbol` / `side` | TEXT NOT NULL | |
| `entry_price` | REAL | nullable — `NULL` = genuinely unknown, never a fabricated 0 |
| `highest_price_since_entry` / `highest_price_at` | REAL / TEXT | |
| `lowest_price_since_entry` / `lowest_price_at` | REAL / TEXT | |
| `mae` / `mfe` | REAL | nullable |
| `has_price_data` | INTEGER NOT NULL DEFAULT 0 | distinguishes "no observation ever arrived" from "observed and it didn't move" |
| `closed_at` | TEXT NOT NULL | |

Indexes: `idx_position_excursions_account_symbol`,
`idx_position_excursions_closed_at`.

## `account_equity_snapshots`

PU-A3: a real, persisted equity/P&L snapshot per account, taken
periodically (`app/equity_history.py`'s `EquitySnapshotter`) — the
durable time series closing the "no persisted equity/balance history"
gap (`GET /accounts/{id}/economics`/`balance` only ever expose current
aggregate values).

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `account_id` | TEXT NOT NULL | |
| `captured_at` | TEXT NOT NULL | |
| `realized_pnl` | REAL NOT NULL | exactly `app/economics.py`'s `compute_account_economics`'s own `realized_pnl` at `captured_at` — never a second, independent calculation |
| `unrealized_pnl` | REAL NOT NULL | sums, per open position, `(last real observed price - average cost) * open quantity`, using only `PositionLifecycle.last_observed_price`; an unpriced symbol contributes `0.0` here and is listed in `unpriced_open_symbols` instead |
| `cumulative_pnl` | REAL NOT NULL | `realized_pnl + unrealized_pnl` — deliberately named "cumulative P&L," never "equity" (this codebase has no starting-balance baseline to report an absolute equity figure against) |
| `unpriced_open_symbols` | TEXT NOT NULL DEFAULT '[]' | JSON list |

Indexes: `idx_account_equity_snapshots_account_captured`.

## `stop_target_events`

PU-A4: a real, append-only event log of a managed position's
stop/target lifecycle. Rows are **never** updated or deleted anywhere
in this codebase — durable, ordered history, not a "current state" row
a later write could silently overwrite.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `account_id` / `symbol` | TEXT NOT NULL | |
| `event_type` | TEXT NOT NULL | `StopTargetEventType`: `stop_placed` / `stop_tightened` / `protection_failed` / `target_hit` |
| `at` | TEXT NOT NULL | |
| `price` / `previous_price` | REAL | meaning is event-type-dependent — see `DATA_DICTIONARY.md` |
| `source` | TEXT NOT NULL | |

Indexes: `idx_stop_target_events_account_symbol_at`.

## `backtest_runs`

TR-15: real, durable persistence for a completed `POST /backtest`
replay (`app/backtest/replay.py`'s `BacktestEngine`).

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `config_hash` | TEXT NOT NULL | SHA-256 over the exact replay inputs (source, symbol filter, period, max_hold_days, cost-stress params, and a content fingerprint of every CSV file actually used) — two runs sharing a hash really did replay identical inputs |
| `created_at` | TEXT NOT NULL | |
| `request_json` | TEXT NOT NULL | the exact `BacktestRequest` |
| `summary_json` | TEXT NOT NULL | the exact `BacktestReport.summary()` |
| `trades_json` | TEXT NOT NULL | the exact trades |
| `stressed_summary_json` | TEXT | |
| `cost_stress_note` | TEXT | |
| `capital_contention_json` | TEXT | added by `_COLUMN_MIGRATIONS` |

Indexes: `idx_backtest_runs_created_at`, `idx_backtest_runs_config_hash`.

## `saved_views`

TR-02: a real, persisted named filter set for a routed screen. This
engine is single-owner (no per-user accounts anywhere in this schema),
so `name` alone is the real identity a saved view is looked up/
overwritten by, enforced UNIQUE.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `name` | TEXT NOT NULL UNIQUE | |
| `screen` | TEXT NOT NULL | which routed view (e.g. `"positions"`); plain TEXT, not an enum/FK, so a future screen needs no schema change |
| `filters_json` | TEXT NOT NULL | the exact CLIENT-side filter-control state at save time — this codebase has no server-side query-param filtering for positions, so this is honestly a client-side blob applied after the normal full snapshot fetch |
| `created_at` | TEXT NOT NULL | |

Indexes: `idx_saved_views_screen`.

## `capital_reservations`

P0-4: a durable record of `app/capital_allocator.py`'s provisional
notional reservation, written **before** the broker call it's gating
even starts (see `docs/design/CAPITAL_ALLOCATION.md`, ADR-0007).

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | a reservation-local uuid, not (yet) a `command_ledger` id — see ADR-0004's follow-up note |
| `account_id` | TEXT NOT NULL | |
| `notional` | REAL NOT NULL | |
| `signal_id` | TEXT | best-effort context, never required for correctness |
| `created_at` | TEXT NOT NULL | |
| `resolved_at` | TEXT | `NULL` = still an uncertain external effect |

Indexes: `idx_capital_reservations_account_unresolved` (partial,
`WHERE resolved_at IS NULL`).

## `route_qualifications`

Live qualification state, per exact execution route — see
`app/qualification.py`'s module docstring and ADR-0006.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `adapter_type` | TEXT NOT NULL | the registered adapter's own `.name` (e.g. `"ccxt"`, `"alpaca"`) |
| `route_key` | TEXT NOT NULL | the exact account/venue variant (e.g. `"ccxt_binance_spot"` vs `"ccxt_binance_perp"`) |
| `asset_class` | TEXT NOT NULL | |
| `product_type` | TEXT NOT NULL | free-text refinement (spot vs perpetual, cash vs margin, etc.) |
| `state` | TEXT NOT NULL | one rung of `QUALIFICATION_STATE_ORDER` |
| `recorded_at` | TEXT NOT NULL | |
| `recorded_by` | TEXT NOT NULL | |
| `notes` | TEXT | |

Rows are append-only, one per (route, state) ever achieved — never
updated to a different state and never deleted; an operator
re-recording an already-achieved state upserts that same row via the
UNIQUE constraint, not a new logical fact. UNIQUE constraint:
`(adapter_type, route_key, asset_class, product_type, state)`. Indexes:
`idx_route_qualifications_route`.

## `command_ledger`

P0-2: the pre-effect durable ledger for every real financial command
this service submits to a broker — see `app/command_ledger.py`'s
module docstring, ADR-0004, `docs/design/WRITER_FENCING.md`'s
promotion integration.

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | |
| `intent_id` | TEXT NOT NULL | |
| `idempotency_key` | TEXT NOT NULL UNIQUE | |
| `command_type` | TEXT NOT NULL | `CommandType`: entry/close/stop_change/target_change(reserved, unused)/replace/cancel/flatten |
| `account_id` | TEXT NOT NULL | |
| `environment` | TEXT NOT NULL | same meaning as an export envelope's `environment` field (`RELAY_ENVIRONMENT`) |
| `expected_revision` | TEXT | optional optimistic-concurrency token; `NULL` where the command has no precondition |
| `request_fingerprint` | TEXT NOT NULL | deterministic SHA-256 over the request parameters — see `DATA_DICTIONARY.md` |
| `created_at` | TEXT NOT NULL | the pre-effect instant — this row is committed before the broker call |
| `remote_identifiers` | TEXT NOT NULL DEFAULT '{}' | JSON — see `DATA_DICTIONARY.md` |
| `uncertainty_state` | TEXT NOT NULL | `UncertaintyState`: pending_submission/submitted_unconfirmed/confirmed/rejected_confirmed/unknown_ambiguous |
| `terminal_evidence` | TEXT NOT NULL DEFAULT '{}' | JSON — see `DATA_DICTIONARY.md` |
| `resolved_at` | TEXT | `NULL` for every unresolved state; set exactly once when `uncertainty_state` becomes `confirmed` or `rejected_confirmed` |

Indexes: `idx_command_ledger_account_id`, `idx_command_ledger_created_at`,
`idx_command_ledger_unresolved` (partial, `WHERE resolved_at IS NULL`).

## `alembic_version`

Standard Alembic bookkeeping table (not created by `SCHEMA` — created
implicitly by `command.stamp`/Alembic's own migration machinery). Holds
the single revision id this database is stamped at. See
`docs/database/MIGRATIONS.md`.

---

## Cross-cutting indexes not tied to one table's own section

`idx_orders_executed_at`, `idx_orders_account_id`,
`idx_signals_received_at`, `idx_sessions_expires_at`,
`idx_export_events_undelivered` are declared at the end of the SCHEMA
string but documented inline above next to their owning table.
