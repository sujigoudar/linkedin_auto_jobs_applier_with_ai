"""Minimal SQLite persistence for signal/order history.

Kept deliberately simple (stdlib sqlite3, no ORM) since this is a scaffold —
swap for SQLAlchemy + Postgres when volume/concurrency needs it.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from alembic import command  # type: ignore[attr-defined]  # real, working import; alembic's __init__.py doesn't re-export it in a way mypy can see
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.models import (
    TERMINAL_UNCERTAINTY_STATES,
    CommandLedgerEntry,
    CommandType,
    OrderResult,
    Side,
    Signal,
    UncertaintyState,
)
from app.writer_lease import LeaseStillValidError, WriterLeaseHeldByAnotherSiteError, WriterLeaseRecord
from signal_platform_contracts import EventEnvelope

_ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"

#: AUD-01: private sentinel distinguishing "this call has no new value for
#: this quantity column, leave whatever's stored" from an explicit `None`
#: ("this call knows the real value is genuinely unknown") -- see
#: `SignalStore._update_order_status_locked`'s docstring.
_UNSET = object()


def _alembic_config(db_path: Path) -> AlembicConfig:
    """A fresh, in-process Alembic Config per call, pointed at whichever
    SQLite file this SignalStore instance actually uses -- one process
    (every test run, in particular) opens many SignalStore instances
    against different files, so a single static alembic.ini-derived URL
    would be wrong for all but one of them. alembic.ini on disk still
    exists for a human running the `alembic` CLI by hand."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def alembic_code_head() -> str | None:
    """TR-16: the migration revision this DEPLOYED CODE (alembic/versions/
    on disk, not any particular database file) expects to be at head --
    read straight from the same `alembic/` scripts `_alembic_config`
    points every real `SignalStore` at. Compared against a live
    `SignalStore.schema_version()` this is the real "is this database's
    schema reproducible from this exact release" check E01 calls for --
    never a hardcoded version string that could silently drift from the
    real migration scripts."""
    script_dir = ScriptDirectory(str(_ALEMBIC_DIR))
    return script_dir.get_current_head()

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    quantity REAL,
    price REAL,
    stop_loss REAL,
    take_profit REAL,
    analyst TEXT,
    received_at TEXT NOT NULL,
    raw TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    broker TEXT,
    symbol TEXT,
    side TEXT,
    requested_quantity REAL,
    signal_id TEXT NOT NULL,
    status TEXT NOT NULL,
    broker_order_id TEXT,
    filled_quantity REAL,
    filled_price REAL,
    message TEXT,
    executed_at TEXT NOT NULL,
    -- PU-A2: real multi-stage execution-latency timestamps -- see
    -- app/execution_quality.py's module docstring for exactly which of the
    -- 7 conceptual TCA stages these two (plus `executed_at` above) cover,
    -- and which honestly collapse together for structural reasons instead.
    -- Both nullable: NULL means this order never reached that stage (a
    -- rejection before submission ever happened, or -- for
    -- protection_confirmed_at -- a non-managed_lifecycle account, or a
    -- managed entry whose stop was never confirmed resting), never a
    -- fabricated value.
    submitted_at TEXT,
    protection_confirmed_at TEXT,
    -- E03 (bounded): the capital_allocator.py notional this specific order
    -- reserved, set only while its status is 'pending' and there's a real
    -- broker_order_id to poll -- see app/capital_allocator.py's "Known gap"
    -- section and app/reconciliation.py's _correct_position, the one place
    -- that releases it once this row's status is confirmed terminal. NULL
    -- for every other order (nothing to release).
    reserved_notional REAL,
    -- DB-0X (order purpose/family): WHY this order was placed, set at the
    -- exact call site that decided to place it (never inferred later from
    -- side/status, which can't distinguish e.g. an entry from a close on
    -- the same symbol/side) -- see app/engine.py's own call sites into
    -- SignalStore.save_order_result. One of a small, real set: 'entry' (a
    -- fresh position-opening order, from a BUY/SELL signal), 'close' (a
    -- position-reducing order, from a CLOSE signal or a manual
    -- flatten/exit), or -- TR-EPISODE-01 -- 'stop_exit'/'target_exit'/
    -- 'time_exit' for a managed-lifecycle position's protective-stop
    -- (including a trailing-stop ratchet -- see `signals.raw.reason`,
    -- `"trailing_stop"` vs `"stop"`, for which), logical-target, or
    -- automatic time-exit fill, respectively -- see
    -- app/lifecycle/manager.py's `PositionLifecycleManager._apply_exit_fill`/
    -- `_persist_self_initiated_exit`, the single place that persists these
    -- (closing the accounting-ledger review's "managed lifecycle exits ...
    -- are not necessarily represented the same way [as an ordinary closing
    -- fill]" gap). A 'provider_exit'/'manual_exit' managed-lifecycle close
    -- is still persisted as 'close' by app/engine.py itself (its own
    -- existing call site), never duplicated here -- see
    -- `_apply_exit_fill`'s own `_SELF_PERSISTED_EXIT_KINDS` docstring for
    -- why only stop/target/time_exit are persisted at that call site.
    -- NULL for any order row saved before this column existed (a real,
    -- pre-existing deployment's history) -- never backfilled with a guess.
    purpose TEXT,
    -- DB-0X: an id shared by every order belonging to the SAME position
    -- episode, so a caller (or a later report) can group an entry with its
    -- eventual close without re-deriving that link from timing/quantity
    -- heuristics. For an 'entry' order this is simply that entry's own
    -- originating `signals.id` (== this row's own `signal_id` -- kept as a
    -- separate column anyway so a future family can span more than one
    -- signal without redefining `signal_id`'s own meaning). For a managed-
    -- lifecycle 'close' this is the SAME value as its position's entry
    -- order (see app/lifecycle/models.py's `PositionPlan.entry_signal_id`,
    -- persisted for exactly this) -- a real, durable link this codebase
    -- already had the data for. For a PLAIN (non-managed_lifecycle)
    -- account's close, NULL: a plain position has no tracked lifecycle
    -- object linking it back to whichever single or accumulated entry
    -- fill(s) produced it (see app/engine.py's `_resolve_and_submit_
    -- plain_close`), so there is no real family id to report -- an honest
    -- gap, not a fabricated one.
    family_id TEXT,
    -- AUD-01 (this pass): the distinct-field quantity model replacing the
    -- old "optimistically apply the requested quantity to positions while
    -- an async broker's order is still PENDING" behavior (see
    -- app/engine.py's module docstring history / git blame on this
    -- comment for exactly what that was). `requested_quantity` above is
    -- what was asked for; these three, together with `positions
    -- .net_quantity` (== `actual_remaining_ownership` by contract from
    -- this pass on -- see that table's own comment), are the rest of the
    -- model. All three are nullable: NULL means "this row predates this
    -- migration" (a real, pre-existing deployment's history — never
    -- backfilled with a guess) or "this call site hasn't been updated to
    -- populate it" (e.g. a REJECTED/ERROR result with nothing to report),
    -- never a fabricated 0.0 standing in for genuinely unknown.
    --
    -- `confirmed_cumulative_fill`: the broker's own reported cumulative
    -- filled quantity for this order, exactly as given (`result
    -- .filled_quantity`) -- never guessed, never defaulted to the
    -- requested quantity. NULL means the broker has not confirmed
    -- anything yet for this specific PENDING order.
    --
    -- `applied_execution_delta`: the actual signed-by-side quantity this
    -- exact save applied to `positions.net_quantity` (via `record_fill`),
    -- if anything. 0.0 (not NULL) is a genuine, known fact -- "this save
    -- confirmed nothing new and touched the position not at all" (e.g. a
    -- still-PENDING order with no confirmed fill yet) -- distinct from
    -- NULL ("this row predates the field / never applicable").
    --
    -- `outstanding_possible_fill`: `requested_quantity -
    -- confirmed_cumulative_fill` at the moment this row was written --
    -- the quantity that could STILL be confirmed by the broker and must
    -- be treated as uncertain exposure, not zero, while this order
    -- remains PENDING. 0.0 once the order reaches a terminal status
    -- (FILLED/REJECTED/ERROR -- nothing more can possibly fill). See
    -- `SignalStore.get_outstanding_possible_fill`'s docstring for the
    -- live, queryable aggregate other code (the capital allocator, the
    -- position-detail UI) should call instead of reading this column
    -- directly off one row.
    confirmed_cumulative_fill REAL,
    applied_execution_delta REAL,
    outstanding_possible_fill REAL,
    -- TRK-Q1: the two remaining named fields of app/models.py's
    -- `QuantityBreakdown` (see that dataclass's own docstring) that had no
    -- persisted column of their own before this pass -- `requested_quantity`,
    -- `confirmed_cumulative_fill`/`applied_execution_delta`/
    -- `outstanding_possible_fill` above (AUD-01) and `positions.net_quantity`
    -- already covered the other five. Both nullable, same "never
    -- fabricated" rule as every other column here: NULL means this row
    -- predates the column, or genuinely has nothing to report for it (e.g.
    -- a close, which reserves no capital and so has no reserved_quantity).
    --
    -- `reserved_quantity`: the QUANTITY (units, not notional) capital
    -- admission set aside for this specific order, set under the exact same
    -- rule as `reserved_notional` above (non-NULL only while status is
    -- 'pending' with a real broker_order_id to poll) -- see
    -- app/capital_allocator.py's own reservation-timing docstring, which
    -- this column mirrors in units rather than notional.
    --
    -- `acknowledged_quantity`: how much of `requested_quantity` the
    -- broker/venue has actually ACCEPTED an order for -- `requested_quantity`
    -- the instant a real broker_order_id exists (FILLED, or PENDING with an
    -- id to poll), `0.0` for a definite non-acceptance (REJECTED, or PENDING
    -- with no broker_order_id at all). See app/quantity.py's
    -- `acknowledged_quantity_for` for the exact rule -- deliberately the
    -- same FILLED/PENDING-with-id vs. everything-else split
    -- app/command_ledger.py's `classify_order_result` already uses to
    -- distinguish CONFIRMED/SUBMITTED_UNCONFIRMED from
    -- REJECTED_CONFIRMED/UNKNOWN_AMBIGUOUS.
    reserved_quantity REAL,
    acknowledged_quantity REAL,
    FOREIGN KEY (signal_id) REFERENCES signals (id)
);

-- Net position per (account, symbol), maintained by the engine so a
-- 'close' signal knows what to close. Positive = net long, negative = net
-- short, zero = flat. This is this service's own record of what it has
-- sent, not a live read of the broker's actual position.
--
-- AUD-01 (this pass): `net_quantity` IS `actual_remaining_ownership` by
-- contract from this pass on -- it is updated ONLY from a broker-confirmed
-- fill (`record_fill` called with a real `confirmed_cumulative_fill`-
-- derived delta, never from a PENDING order's merely-requested quantity).
-- A broker that reports PENDING rather than a synchronous confirmed fill
-- (SignalStack, Alpaca, IBKR, NinjaTrader, Rithmic) leaves this column
-- UNCHANGED until app/reconciliation.py (or a synchronous partial-fill
-- report alongside PENDING) confirms a real quantity -- see
-- app/engine.py's module docstring and `orders.outstanding_possible_fill`/
-- `SignalStore.get_outstanding_possible_fill` for where that unconfirmed,
-- still-possible exposure is tracked and surfaced instead.
CREATE TABLE IF NOT EXISTS positions (
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    net_quantity REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol)
);

-- Crash-resumable state for app/lifecycle/manager.py's PositionLifecycleManager:
-- one row per open managed-lifecycle position, holding its whole
-- PositionLifecycle (plan, stop, pending_exit) plus its CloseArbiter ledger
-- snapshot as one JSON blob. Written after every state-changing transition;
-- deleted once the position closes. See PositionLifecycleManager.restore_from_store.
CREATE TABLE IF NOT EXISTS lifecycle_state (
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol)
);

-- Live, GUI/API-editable configuration — the database-backed replacement
-- for hand-editing config/accounts.yaml, config/routing.yaml, and
-- config/providers.yaml. See app/main.py's account/routing-rule/provider
-- CRUD endpoints and app/routing.py's/app/providers.py's `*_from_store`
-- loaders. YAML files remain a one-time import path (see
-- app/config_admin.py's `seed_from_yaml_if_empty`), not the live source
-- of truth once any of these tables has a row.
CREATE TABLE IF NOT EXISTS config_accounts (
    account_id TEXT PRIMARY KEY,
    broker TEXT NOT NULL,
    multiplier REAL NOT NULL DEFAULT 1.0,
    fixed_quantity REAL,
    symbol_map TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    managed_lifecycle INTEGER NOT NULL DEFAULT 0,
    max_notional_exposure REAL,
    risk_percent_of_equity REAL,
    -- P0-5: explicit, persisted management-recipe declaration (see
    -- app/models.py's ManagementRecipe) and a simple free-form
    -- qualification label -- both additive, both NULLable so an
    -- existing row is honestly "not yet declared" until read back
    -- through app/routing.py's *_from_store loader (which fills
    -- management_recipe from managed_lifecycle the same way
    -- DestinationAccount.__post_init__ does for a fresh construction).
    management_recipe TEXT,
    qualification_level TEXT,
    -- P0-5: off-by-default exclusive-writer-qualified assertion -- see
    -- DestinationAccount.exclusive_writer_qualified's own docstring.
    exclusive_writer_qualified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config_routing_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    destinations TEXT NOT NULL,
    symbol_filter TEXT
);

CREATE TABLE IF NOT EXISTS config_providers (
    provider_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    multiplier REAL,
    fixed_quantity REAL,
    managed_lifecycle INTEGER,
    enabled INTEGER
);

CREATE TABLE IF NOT EXISTS config_analysts (
    provider_id TEXT NOT NULL,
    analyst_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    multiplier REAL,
    fixed_quantity REAL,
    managed_lifecycle INTEGER,
    enabled INTEGER,
    PRIMARY KEY (provider_id, analyst_id)
);

-- Subscription cost/billing tracking for a signal provider (see
-- app/provider_value.py) -- keyed by provider_id (Signal.source /
-- ProviderConfig.provider_id), since a subscription is normally billed
-- per channel/provider, not per individual analyst within a shared one.
-- `cost_amount` is the cost of ONE `billing_cycle`, not a lifetime total.
-- `subscribed_since` anchors the "how many billing cycles have elapsed"
-- estimate app/provider_value.py's cost-to-date calculation uses; it is
-- NOT the same thing as `renewal_date` (the next upcoming renewal,
-- informational only). status='candidate' marks a provider
-- app/provider_scout.py (or the owner, manually) added here without yet
-- treating it as a real, paid subscription -- see that module.
CREATE TABLE IF NOT EXISTS provider_subscriptions (
    provider_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    cost_amount REAL NOT NULL DEFAULT 0.0,
    currency TEXT NOT NULL DEFAULT 'USD',
    billing_cycle TEXT NOT NULL DEFAULT 'monthly',
    subscribed_since TEXT NOT NULL,
    renewal_date TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- app/provider_scout.py's last-computed snapshot for a (source, analyst,
-- asset_class) that is NOT currently a provider_subscriptions row --
-- persisted so GET /providers/candidates can show "last evaluated at X"
-- without recomputing the full replay on every dashboard load, and so the
-- scheduled scan (not a live request) is what actually produces this.
-- `analyst` is '' (never NULL) for "no analyst on the signal" so it can be
-- part of this table's primary key.
CREATE TABLE IF NOT EXISTS provider_candidates (
    source TEXT NOT NULL,
    analyst TEXT NOT NULL DEFAULT '',
    asset_class TEXT NOT NULL,
    closing_fills INTEGER NOT NULL,
    winning_closing_fills INTEGER NOT NULL,
    realized_pnl REAL NOT NULL,
    win_rate REAL,
    profit_factor REAL,
    recommendation TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY (source, analyst, asset_class)
);

-- Server-side owner sessions (see app/auth.py). `session_id` is the opaque
-- value carried in the session cookie; `csrf_token` is returned once at
-- login and must be echoed back as the X-CSRF-Token header on every
-- mutating request (double-submit defense). Deleting a row revokes that
-- session immediately (logout, or an owner-initiated "sign out everywhere").
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    credential_epoch TEXT
);

-- Recent financial-command results, keyed by an idempotency key the caller
-- supplies (see app/main.py's close/flatten endpoints): a retried or
-- duplicated request with the same key replays the stored result instead
-- of executing the command again.
CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    fingerprint TEXT
);

-- Cross-process/cross-instance mutual exclusion for a plain-account close
-- (EXE-12): app/engine.py's `_plain_close_locks` is an in-memory
-- asyncio.Lock, which only serializes calls within ONE engine instance --
-- two independent processes (or two engine instances) sharing this same
-- database have entirely separate lock objects and no real exclusion
-- between them. The UNIQUE constraint here makes the claim atomic at the
-- database itself: only one process's INSERT can ever succeed for a given
-- (account_id, symbol) at a time, everywhere this file is the shared store.
CREATE TABLE IF NOT EXISTS close_claims (
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol)
);

-- Cross-process/cross-host single-writer fencing (see app/writer_lease.py
-- and docs/FAILOVER.md). A single row (id=1): whichever site/process last
-- acquired or was explicitly promoted holds the CURRENT `fencing_token`.
-- The token only ever increases (see `promote_writer_lease` /
-- `acquire_or_reacquire_writer_lease`) -- a process whose in-memory token
-- no longer matches this row's is fenced out immediately, on its very
-- next command-execution check, regardless of whether `expires_at` would
-- otherwise still look unexpired to it. This is an additional, automatic
-- guard on top of (never a replacement for) deploy/RUNBOOK.md's manual
-- confirmation that a prior writer's host is actually stopped.
CREATE TABLE IF NOT EXISTS writer_lease (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fencing_token INTEGER NOT NULL,
    site_id TEXT NOT NULL,
    holder_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL
);

-- EXE-10: `seed_from_yaml_if_empty` (app/config_admin.py) used to treat an
-- EMPTY config_accounts table as "never seeded" regardless of why it's
-- empty -- a deliberate deletion of the only account left the table just
-- as empty as a genuinely fresh install, so the next restart's seed call
-- would resurrect the very account the owner just removed. This one-row
-- marker distinguishes "seeding has already happened once" from "never
-- seeded" so a later empty state from an explicit delete is never
-- mistaken for a fresh install.
CREATE TABLE IF NOT EXISTS seed_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    seeded_at TEXT NOT NULL
);

-- Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
-- S4.2/S6: the transactional private export outbox. A row here is
-- written in the SAME sqlite3 transaction as the authoritative local
-- state change it describes (see SignalStore.save_order_result's own
-- `export_envelope` parameter) -- never a best-effort HTTP POST after
-- commit as the only export mechanism. `event_id` is the producer's own
-- idempotency key (UNIQUE, so a caller that accidentally re-appends the
-- exact same event is a no-op, not a duplicate row) and
-- `export_sequence` is this producer's own monotonic position within
-- `source_stream` -- the relay (a later slice) delivers in that order
-- and the commercial inbox's own high-water mark advances by it.
-- `envelope_json` is the FULL signal_platform_contracts.EventEnvelope,
-- serialized once at append time -- the relay never reconstructs or
-- reinterprets it, only forwards these exact bytes.
CREATE TABLE IF NOT EXISTS export_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_stream TEXT NOT NULL,
    export_sequence INTEGER NOT NULL,
    envelope_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    appended_at TEXT NOT NULL,
    delivered_at TEXT,
    UNIQUE (source_stream, export_sequence)
);

-- PU-A1: durable, per-closed-position record of real-price-driven MAE/MFE
-- (maximum adverse/favorable excursion) tracking -- one row per completed
-- position episode (see app/lifecycle/manager.py's `_persist_closed_excursion`,
-- called from the single place every lifecycle-closing path converges on).
-- Written ONCE a position fully closes, so it remains queryable for
-- analytics/charting after `lifecycle_state`'s own in-progress row for the
-- same (account_id, symbol) is deleted. `entry_price`/the four price/time
-- columns/`mae`/`mfe` are all nullable: NULL means "genuinely unknown" (no
-- entry price, or no real price observation ever arrived — see
-- `has_price_data`), never a fabricated 0.
CREATE TABLE IF NOT EXISTS position_excursions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL,
    highest_price_since_entry REAL,
    highest_price_at TEXT,
    lowest_price_since_entry REAL,
    lowest_price_at TEXT,
    mae REAL,
    mfe REAL,
    has_price_data INTEGER NOT NULL DEFAULT 0,
    closed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_position_excursions_account_symbol
    ON position_excursions (account_id, symbol);
CREATE INDEX IF NOT EXISTS idx_position_excursions_closed_at ON position_excursions (closed_at);

-- PU-A3: a real, persisted equity/P&L snapshot per account, taken
-- periodically (see app/equity_history.py's EquitySnapshotter) -- the
-- durable time series that closes the "no persisted equity/balance
-- HISTORY exists" gap Phase B1 identified (`GET /accounts/{id}/economics`/
-- `balance` only ever expose CURRENT aggregate values).
--
-- `realized_pnl` is exactly app/economics.py's `compute_account_economics`
-- own `realized_pnl` at `captured_at` -- never a second, independent P&L
-- calculation (see that module's own load-bearing test).
--
-- `unrealized_pnl` sums, over this account's open positions, (last real
-- observed price - average cost) * open quantity, using ONLY a real price
-- from app/lifecycle/models.py's `PositionLifecycle.last_observed_price`
-- (itself fed solely by app/pricing.py's PriceMonitor or a real entry
-- fill). A symbol with an open position but no real price observed yet
-- contributes 0.0 here and is listed in `unpriced_open_symbols` instead --
-- an honest disclosure of incompleteness, never a fabricated mark.
--
-- This codebase's DestinationAccount/AccountRequest have no
-- starting_balance/starting_capital field, so there is no real,
-- broker-confirmed absolute equity baseline to report. `cumulative_pnl`
-- (== realized_pnl + unrealized_pnl at this snapshot) is deliberately
-- named for what it honestly is -- cumulative P&L since this account's
-- own execution history began -- rather than called "equity", which would
-- imply an absolute balance this service never configured or observed.
CREATE TABLE IF NOT EXISTS account_equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    cumulative_pnl REAL NOT NULL,
    unpriced_open_symbols TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_account_equity_snapshots_account_captured
    ON account_equity_snapshots (account_id, captured_at);

-- PU-A4: a real, append-only event log of a managed position's
-- stop/target lifecycle -- see app/lifecycle/models.py's
-- `StopTargetEventType` for exactly which event types exist and why (and
-- which catalog-requested ones, like a breakeven move or a trail
-- activation, are a documented gap rather than a fabricated event on this
-- branch). Written by app/lifecycle/manager.py's own call sites at the
-- exact moment each real state change happens -- never backfilled or
-- reconstructed after the fact. Rows are NEVER updated or deleted (this
-- table has no UPDATE/DELETE statement anywhere in this codebase): the
-- data prerequisite for stop/target analytics (stop-tightening frequency,
-- TP hit rates, etc.) must be a durable, ordered history, not a
-- "current state" row a later write could silently overwrite.
--
-- `price`/`previous_price` are both nullable and event-type-dependent:
-- for STOP_PLACED, `price` is the newly-confirmed stop price and
-- `previous_price` is whatever this same StopRecord's last
-- broker-confirmed price was before this call (NULL for a true initial
-- placement); for STOP_TIGHTENED, `price` is the new (tighter) price and
-- `previous_price` is the price it replaced; for PROTECTION_FAILED,
-- `price` is the price that was attempted and `previous_price` mirrors
-- STOP_PLACED's meaning; for TARGET_HIT, `price` is the target's own
-- trigger price and `previous_price` is always NULL (a target firing has
-- no "previous target price" to report).
CREATE TABLE IF NOT EXISTS stop_target_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    at TEXT NOT NULL,
    price REAL,
    previous_price REAL,
    source TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stop_target_events_account_symbol_at
    ON stop_target_events (account_id, symbol, at);

-- TR-15: real, durable persistence for a completed POST /backtest replay --
-- see app/backtest/replay.py's BacktestEngine / app/main.py's run_backtest
-- for what actually produces every field stored here. Brand-new table, so
-- CREATE TABLE IF NOT EXISTS alone is backfill-safe for a pre-existing
-- on-disk database that predates this table (no _COLUMN_MIGRATIONS entry
-- needed -- those are only for adding a column to an already-existing
-- table).
--
-- `config_hash` is a real SHA-256 over the exact replay inputs (source,
-- symbol filter, period, max_hold_days, cost-stress params, and a
-- content fingerprint of every CSV file actually used -- not just its
-- path, since the same path can hold different bars across runs) -- see
-- app/main.py's `compute_backtest_config_hash`. Two runs sharing a hash
-- really did replay the identical inputs; two runs that differ in ANY of
-- those real inputs get different hashes, which is what makes the
-- configuration-comparison view (Current policy vs candidate A vs
-- candidate B) trustworthy rather than coincidental.
--
-- `request_json`/`summary_json`/`trades_json` are the exact real
-- BacktestRequest and BacktestReport.summary()/trades this run actually
-- produced (the same shapes POST /backtest already returned inline before
-- this table existed) -- never re-derived or approximated after the fact.
CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    request_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    trades_json TEXT NOT NULL,
    stressed_summary_json TEXT,
    cost_stress_note TEXT,
    capital_contention_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_created_at ON backtest_runs (created_at);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_config_hash ON backtest_runs (config_hash);

-- TR-02 Saved views: a real, persisted named filter set for a routed
-- screen -- see app/static/views/tr02.js's module docstring for the exact
-- gap this closes (that screen's "Saved views" panel previously disclosed
-- "not implemented in this build").
--
-- This engine is single-owner (one OWNER_PASSWORD, no per-user accounts
-- anywhere in this schema -- see config_accounts/sessions), so there is no
-- real per-user scoping column to add; `name` alone is the real identity
-- a saved view is looked up/overwritten by, enforced UNIQUE so two saves
-- under the same name can never silently coexist as ambiguous rows.
--
-- `screen` identifies which routed view (`Router.register`'s own route
-- key, e.g. "positions" for TR-02's `#/trade/positions`) this view's
-- filters apply to -- a plain TEXT column (not an enum/FK) so a future
-- screen can start writing its own rows here without a schema change,
-- while `idx_saved_views_screen` keeps a per-screen listing real-time
-- cheap.
--
-- `filters_json` is the exact, real client-side filter-control state TR-02
-- (or a future screen) actually had selected at save time -- this
-- codebase has no server-side query-param filtering for positions yet
-- (see tr02.js's own scope-controls note), so this is honestly a
-- CLIENT-side filter-state blob, applied by the view after fetching its
-- normal full snapshot -- never a server-side query it silently implies
-- exists.
CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    screen TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saved_views_screen ON saved_views (screen);

-- P0-4: a durable record of app/capital_allocator.py's provisional
-- notional reservation, written BEFORE the broker call it's gating even
-- starts (see CapitalAllocator.admit) -- not after, the way
-- `orders.reserved_notional` already is (that column is only ever set by
-- `save_order_result`, which only runs once the broker call has already
-- returned). The gap this closes: a remote broker can accept an order
-- and this process can still die before `save_order_result` ever
-- commits, and `orders.reserved_notional`'s own release accounting (see
-- app/reconciliation.py's `_correct_position`) has no row to work with
-- at all in that case. A row here is written the instant admission
-- succeeds, independent of whether the order row that follows ever gets
-- written. `resolved_at IS NULL` means "still an uncertain external
-- effect" -- released only once this exact admission's outcome is
-- confirmed terminal (REJECTED/ERROR/FILLED, or a PENDING with nothing
-- left to ever poll) at the same call sites that already call
-- `CapitalAllocator.release` (see `resolve_one_capital_reservation`).
-- `CapitalAllocator.__init__` sums every still-unresolved row here, per
-- account, to rebuild its in-memory ledger on startup -- a restart must
-- not start from "no in-flight admissions to lose": an order the broker
-- already accepted before the crash stays reserved (uncertain) until
-- reconciliation independently confirms its outcome, never silently
-- forgotten.
--
-- NOTE for whoever lands P0-2's `command_ledger` table: this table's
-- `id` is a reservation-local uuid, not that ledger's own command/intent
-- id, because `command_ledger` wasn't on this branch yet when this was
-- written. Once it lands, consider having `CapitalAllocator.admit` take
-- and store the real command/intent id here instead of minting its own,
-- so a reservation and its originating command share one identifier
-- end-to-end -- this table's `signal_id` column is a good anchor for
-- that reconciliation (both should already agree on the same signal).
CREATE TABLE IF NOT EXISTS capital_reservations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    notional REAL NOT NULL,
    signal_id TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_capital_reservations_account_unresolved
    ON capital_reservations (account_id) WHERE resolved_at IS NULL;

-- Live qualification state, per EXACT execution route -- see
-- app/qualification.py's module docstring for what this is and why it is
-- deliberately separate from app/brokers/base.py's implementation-derived
-- capability introspection. A route is the tuple (adapter_type, route_key,
-- asset_class, product_type): `adapter_type` is the registered adapter's
-- own `.name` (e.g. "ccxt", "alpaca", "signalstack" -- shared by every
-- instance of that adapter class), `route_key` is the exact account/venue
-- variant this row is about (e.g. "ccxt_binance_spot" vs
-- "ccxt_binance_perp" -- these are DIFFERENT routes even though both run
-- through the identical CCXTBroker class), `asset_class` is one of
-- app/models.py's AssetClass values, and `product_type` is a free-text
-- refinement distinguishing routes that share the same asset_class but are
-- genuinely different products (spot vs perpetual on the same crypto
-- exchange, cash vs margin equities, etc).
--
-- Rows are APPEND-ONLY, one per (route, state) ever achieved -- never
-- updated to a different state and never deleted by any code path in this
-- build (an operator re-recording an already-achieved state updates that
-- SAME row's recorded_at/recorded_by/notes via the UNIQUE constraint's
-- upsert, not a new logical fact). This durable history is what lets the
-- ladder-prerequisite check in `SignalStore.record_route_qualification`
-- work at all (it recomputes "every state already achieved for this exact
-- route" from these rows every time) and is what TR-07/TR-08 render
-- alongside the engineering capability matrix, never replacing it.
CREATE TABLE IF NOT EXISTS route_qualifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adapter_type TEXT NOT NULL,
    route_key TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    product_type TEXT NOT NULL,
    state TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    notes TEXT,
    UNIQUE(adapter_type, route_key, asset_class, product_type, state)
);

CREATE INDEX IF NOT EXISTS idx_route_qualifications_route
    ON route_qualifications (adapter_type, route_key, asset_class, product_type);

-- P0-2 (external release audit, "one durable command ledger"): the
-- pre-effect durable ledger for EVERY real financial command this service
-- submits to a broker -- entry, close, stop_change (initial/re-placed
-- protective stop), replace (an in-place broker amend of a resting stop),
-- cancel, and flatten. See app/command_ledger.py's module docstring for
-- the full contract and app/models.py's `CommandType`/`UncertaintyState`
-- for the real, closed sets of values `command_type`/`uncertainty_state`
-- take. `target_change` is a reserved value in the enum with no writer on
-- this branch -- see `CommandType`'s own docstring for why.
--
-- The whole point of this table is ORDERING: a row here is written and
-- COMMITTED before the broker call it describes is ever made (see every
-- real call site in app/engine.py / app/lifecycle/manager.py) -- never
-- after. `created_at` is that pre-effect instant. A process killed between
-- this commit and the broker call still leaves a real, durable
-- `pending_submission` row for a restart to find (see
-- SignalStore.list_unresolved_command_ledger_entries) -- the exact
-- "PENDING result without a broker order ID is an acknowledged exposure
-- gap" scenario the audit names, now visible instead of silently lost.
--
-- `idempotency_key` is UNIQUE: a retried/duplicated call with the SAME key
-- never submits a second broker order (see
-- SignalStore.open_command_ledger_entry) -- it either replays the
-- existing row's tracked state (matching `request_fingerprint`) or is
-- rejected outright (a different fingerprint under the same key is a
-- caller bug, never silently allowed through).
--
-- `request_fingerprint` is a deterministic hash over the exact request
-- parameters (see app/command_ledger.py's `compute_fingerprint`) -- what
-- distinguishes "this is really the same command, retried" from "this
-- reused an old key for a genuinely different command."
--
-- `expected_revision` is an optional optimistic-concurrency token (e.g. a
-- broker-reported order/position revision a `replace`/`cancel` was issued
-- against) -- NULL wherever the command has no such precondition (e.g. a
-- fresh `entry`, which has nothing to be optimistic-concurrent against).
--
-- `remote_identifiers` and `terminal_evidence` are both JSON objects
-- (never NULL -- '{}' when nothing is known yet): the former holds
-- whatever broker order id(s) become known once the broker responds
-- (never fabricated before then); the latter holds whatever REAL evidence
-- resolved this row to a terminal state -- a broker fill confirmation, a
-- confirmed rejection, a reconciliation match, or (for the ambiguous case)
-- the exception/response that made the outcome genuinely unknown. Both are
-- read with `json.loads`, so a row's on-disk value is always valid JSON --
-- see SignalStore's own read/write helpers, the only code that ever
-- touches this column.
--
-- `resolved_at` is NULL for every unresolved row (`pending_submission`,
-- `submitted_unconfirmed`, `unknown_ambiguous`) and set exactly once, at
-- the moment `uncertainty_state` is written as `confirmed` or
-- `rejected_confirmed` -- see app/models.py's `TERMINAL_UNCERTAINTY_STATES`.
CREATE TABLE IF NOT EXISTS command_ledger (
    id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    command_type TEXT NOT NULL,
    account_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    expected_revision TEXT,
    request_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    remote_identifiers TEXT NOT NULL DEFAULT '{}',
    uncertainty_state TEXT NOT NULL,
    terminal_evidence TEXT NOT NULL DEFAULT '{}',
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_command_ledger_account_id ON command_ledger (account_id);
CREATE INDEX IF NOT EXISTS idx_command_ledger_created_at ON command_ledger (created_at);
CREATE INDEX IF NOT EXISTS idx_command_ledger_unresolved ON command_ledger (resolved_at) WHERE resolved_at IS NULL;

-- Track 5: the persistent Telegram collector registry -- see
-- app/telegram_collectors.py's module docstring for the full contract
-- and each enum's own docstring for the real, closed set of values
-- connection_mode/health_state take. One row per collector (a bot
-- membership, or an authenticated Telethon user-account session) this
-- deployment runs against one chat/topic.
--
-- `credential_env_var` is a REFERENCE ONLY -- the name of an environment
-- variable this collector's real credential (bot token, or Telethon
-- session file path) is read from at process startup. Never a secret
-- value itself -- see docs/security/SECRETS.md and
-- docs/security/TELEGRAM_USER_LOGIN.md.
--
-- `allowed_uses` is a JSON array of app/telegram_collectors.py's
-- `AllowedUse` values, defaulting to `["private_trading"]` only (point
-- 10: never automatically `commercial_redistribution` -- see that
-- module's own docstring for the isolation this is asserting).
--
-- `checkpoint_message_id` is the per-collector restart-recovery
-- checkpoint (point 7): the last message id this collector has admitted
-- to LIVE routing. NULL for a collector that has never processed a live
-- message. A historical import never advances this column -- see
-- app/sources/telegram_user.py's `import_history`.
--
-- `qualification_evidence` is a JSON object recording WHEN/how real,
-- authorized message receipt was last confirmed for this collector (see
-- SignalStore.record_telegram_collector_qualification_evidence) --
-- distinct from, and orthogonal to, app/qualification.py's live-ROUTING
-- release ladder: a Telegram collector reaching `healthy_qualified` here
-- means "this ingests real messages," never "this route may submit a
-- live order" (that's still gated entirely by
-- SignalStore.is_route_release_approved, untouched by this table).
CREATE TABLE IF NOT EXISTS telegram_collectors (
    id TEXT PRIMARY KEY,
    connection_mode TEXT NOT NULL,
    identity_ref TEXT NOT NULL,
    credential_env_var TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    topic_id TEXT,
    provider_name TEXT NOT NULL,
    allowed_uses TEXT NOT NULL DEFAULT '["private_trading"]',
    noforwards INTEGER,
    last_qualified_at TEXT,
    qualification_evidence TEXT NOT NULL DEFAULT '{}',
    checkpoint_message_id INTEGER,
    checkpoint_updated_at TEXT,
    health_state TEXT NOT NULL DEFAULT 'unqualified',
    health_detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telegram_collectors_provider ON telegram_collectors (provider_name);
CREATE INDEX IF NOT EXISTS idx_telegram_collectors_chat ON telegram_collectors (chat_id);

-- Track 6: the persistent Slack/Twitter USER-CONTEXT collector registry --
-- see app/collector_registry.py's module docstring for the full contract.
-- One shared table for both providers (a `provider` column distinguishes
-- rows) -- unlike telegram_collectors this has no `topic_id`/`noforwards`
-- (Telegram-specific concepts with no verified Slack/Twitter equivalent).
--
-- `credential_env_var` is a REFERENCE ONLY, never a secret value -- see
-- docs/security/SECRETS.md, docs/security/SLACK_USER_TOKEN.md,
-- docs/security/TWITTER_USER_CONTEXT.md.
--
-- `checkpoint` is TEXT (not INTEGER, unlike telegram_collectors'
-- `checkpoint_message_id`) -- Slack's own message id (`ts`) is not an
-- integer. Ordering/monotonicity is enforced by each adapter itself
-- (app/sources/slack_user.py, app/sources/twitter_user.py) before calling
-- advance_pull_collector_checkpoint, not by this table.
CREATE TABLE IF NOT EXISTS pull_collectors (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    identity_ref TEXT NOT NULL,
    credential_env_var TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_label TEXT,
    provider_name TEXT NOT NULL,
    allowed_uses TEXT NOT NULL DEFAULT '["private_trading"]',
    last_qualified_at TEXT,
    qualification_evidence TEXT NOT NULL DEFAULT '{}',
    checkpoint TEXT,
    checkpoint_updated_at TEXT,
    health_state TEXT NOT NULL DEFAULT 'unqualified',
    health_detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Track 9: the persistent website/article collector registry -- see
-- app/website_collectors.py's module docstring for the full contract.
-- Mirrors telegram_collectors's shape: one row per site/section this
-- deployment polls, either via a configured RSS/Atom feed_url or a
-- configured article_list_url (diffed against a seen-URL checkpoint --
-- see checkpoint_seen_urls below). Exactly one of feed_url/
-- article_list_url is expected to be set, per site_format -- enforced at
-- the application layer by app.website_collectors.validate_registration,
-- never guessed/scanned by this table or any adapter.
--
-- auth_state_env_var is a REFERENCE ONLY (same convention as Telegram's
-- credential_env_var) -- the name of an environment variable pointing to
-- a stored auth-state FILE PATH for paywalled content, never a
-- session/cookie value itself. NULL (the default) means this collector
-- fetches with no stored auth at all.
--
-- checkpoint_seen_urls is a JSON array of article URLs this collector
-- has already admitted to live routing (the ARTICLE_LIST mode's own
-- "seen URL set" diff checkpoint; FEED mode instead uses
-- checkpoint_article_url, the single most-recent admitted URL, since a
-- feed's own ordering makes a full seen-set unnecessary there).
CREATE TABLE IF NOT EXISTS website_collectors (
    id TEXT PRIMARY KEY,
    site_format TEXT NOT NULL,
    site_id TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    feed_url TEXT,
    article_list_url TEXT,
    analyst TEXT,
    auth_state_env_var TEXT,
    allowed_uses TEXT NOT NULL DEFAULT '["private_trading"]',
    last_qualified_at TEXT,
    qualification_evidence TEXT NOT NULL DEFAULT '{}',
    checkpoint_article_url TEXT,
    checkpoint_seen_urls TEXT NOT NULL DEFAULT '[]',
    checkpoint_updated_at TEXT,
    health_state TEXT NOT NULL DEFAULT 'unqualified',
    health_detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pull_collectors_provider ON pull_collectors (provider);
CREATE INDEX IF NOT EXISTS idx_pull_collectors_target ON pull_collectors (target_id);
CREATE INDEX IF NOT EXISTS idx_website_collectors_provider ON website_collectors (provider_name);
CREATE INDEX IF NOT EXISTS idx_website_collectors_site ON website_collectors (site_id);

-- Track 9: extracted/classified article trade candidates -- see
-- app/sources/article_classifier.py's TradeCandidate docstring. Keyed by
-- (channel_id, message_id) == (site_id, canonical article URL), the same
-- provider-identity dedup convention as `signals.channel_id`/
-- `message_id` (find_signal_id_by_provider_identity). A later revision
-- of the SAME URL (a newer modified_at) UPDATES this row in place --
-- never a duplicate insert -- see upsert_website_candidate.
CREATE TABLE IF NOT EXISTS website_article_candidates (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    classification TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    signal_id TEXT,
    published_at TEXT,
    modified_at TEXT,
    candidate_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (channel_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_website_article_candidates_channel ON website_article_candidates (channel_id);

CREATE INDEX IF NOT EXISTS idx_orders_executed_at ON orders (executed_at);
CREATE INDEX IF NOT EXISTS idx_orders_account_id ON orders (account_id);
CREATE INDEX IF NOT EXISTS idx_orders_signal_id ON orders (signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS idx_signals_received_at ON signals (received_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_export_events_undelivered ON export_events (source_stream, export_sequence) WHERE delivered_at IS NULL;
"""


#: Additive migrations for columns added after a table already existed —
#: `CREATE TABLE IF NOT EXISTS` above only helps a brand-new database.
#: Each entry is applied with ALTER TABLE, ignoring the "duplicate column"
#: error SQLite raises when it's already there (no IF NOT EXISTS support
#: for columns before SQLite 3.35, and this stays compatible with older
#: builds rather than assuming a version).
_COLUMN_MIGRATIONS = [
    ("signals", "stop_loss", "REAL"),
    ("signals", "take_profit", "REAL"),
    ("signals", "analyst", "TEXT"),
    ("sessions", "credential_epoch", "TEXT"),
    ("idempotency_records", "fingerprint", "TEXT"),
    ("config_accounts", "max_notional_exposure", "REAL"),
    ("config_accounts", "risk_percent_of_equity", "REAL"),
    ("orders", "submitted_at", "TEXT"),
    ("orders", "protection_confirmed_at", "TEXT"),
    ("orders", "purpose", "TEXT"),
    ("orders", "family_id", "TEXT"),
    # E02 (bounded, history-import workflow): NULL for every live-received
    # signal (this codebase's only other signal-creation path); a batch
    # label for one created by the owner-gated batch-classify-and-import
    # review workflow -- see Signal.import_batch's docstring in
    # app/models.py for why this is the one field added for it.
    ("signals", "import_batch", "TEXT"),
    ("backtest_runs", "capital_contention_json", "TEXT"),
    # AUD-01 (this pass): the distinct-field quantity model -- see the
    # `orders` table's own SCHEMA comment above for exactly what each
    # column means and why all three are nullable.
    ("orders", "confirmed_cumulative_fill", "REAL"),
    ("orders", "applied_execution_delta", "REAL"),
    ("orders", "outstanding_possible_fill", "REAL"),
    # P0-5: additive for a pre-existing config_accounts table -- see this
    # table's own CREATE TABLE comment above.
    ("config_accounts", "management_recipe", "TEXT"),
    ("config_accounts", "qualification_level", "TEXT"),
    ("config_accounts", "exclusive_writer_qualified", "INTEGER NOT NULL DEFAULT 0"),
    # TRK-Q1: the two remaining named `QuantityBreakdown` fields with no
    # existing column -- see the `orders` table's own SCHEMA comment above.
    ("orders", "reserved_quantity", "REAL"),
    ("orders", "acknowledged_quantity", "REAL"),
    # Track 5 (point 6, cross-collector/cross-transport dedup): NULL for
    # every adapter that hasn't been wired to report real provider message
    # identity yet -- see Signal.channel_id/message_id/revision_id's own
    # docstrings in app/models.py, and
    # SignalStore.find_signal_id_by_provider_identity for the ONE place
    # these three columns are read back, before app/engine.py's own
    # SIG-01 per-signal-id replay-lookup runs.
    ("signals", "channel_id", "TEXT"),
    ("signals", "message_id", "TEXT"),
    ("signals", "revision_id", "TEXT"),
]


class SignalStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            for table, column, coltype in _COLUMN_MIGRATIONS:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc):
                        raise
            # Must run AFTER the _COLUMN_MIGRATIONS loop above (a brand new
            # database gets these three columns from that loop, not from
            # SCHEMA's own CREATE TABLE) -- see
            # find_signal_id_by_provider_identity's docstring for what this
            # index serves.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_signals_provider_identity "
                "ON signals (channel_id, message_id, revision_id)"
            )
        self._stamp_alembic_head_if_needed()

    def _stamp_alembic_head_if_needed(self) -> None:
        """C03 (bounded, adoption plan E01): the bootstrap above has
        already brought this database's schema to exactly what
        alembic/versions/0001_initial_schema.py's upgrade() would produce
        -- whether this file is brand new or a pre-existing deployment
        the old ad-hoc _COLUMN_MIGRATIONS mechanism already upgraded. Mark
        it as being at that Alembic revision without RE-running it (that
        would try to CREATE a table that's already there); this is purely
        so `alembic history`/`alembic upgrade head` are meaningful for
        every real database from here on, for whatever the NEXT schema
        change adds as a proper revision. Never re-stamps a database
        that's already stamped (or that a real `alembic upgrade` has
        already brought under version control) -- see this method's own
        `alembic_version` check.
        """
        with self._connect() as conn:
            already_tracked = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            ).fetchone()
        if already_tracked:
            return
        command.stamp(_alembic_config(self.db_path), "head")

    def schema_version(self) -> str | None:
        """TR-16 (E01 bounded, deployment-reproducibility slice): the
        `alembic_version` row this exact database file is actually
        stamped at right now -- real, live evidence read straight off
        disk, not a cached/assumed value. `None` only for a database this
        process hasn't opened/stamped yet (shouldn't happen once
        `__init__` has run, but never guessed at)."""
        with self._connect() as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        # DB-01: SQLite disables foreign key enforcement by default on
        # every new connection regardless of a schema's own FOREIGN KEY
        # declarations -- `orders.signal_id -> signals.id` was declared
        # but never actually enforced, so an order row pointing at a
        # signal id that was never persisted (or already deleted) was
        # silently accepted rather than rejected.
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get_signal_raw(self, signal_id: str) -> dict:
        """TR-EPISODE-01: the parsed `raw` JSON for one signal, or `{}` if
        the id doesn't resolve to a stored signal -- used by
        app/trade_episode.py to tell a plain stop exit from a trailing-
        stop exit (both share `orders.purpose == 'stop_exit'`; only the
        originating signal's `raw.reason` disambiguates them -- see
        `PositionLifecycleManager._apply_exit_fill`'s `was_trailing`
        check). Never raises for an unresolved id -- an honestly empty
        dict, not a fabricated guess."""
        with self._connect() as conn:
            row = conn.execute("SELECT raw FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if row is None or row[0] is None:
            return {}
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return {}

    def save_signal(self, signal: Signal) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO signals
                   (id, source, symbol, side, asset_class, quantity, price, stop_loss, take_profit,
                    analyst, received_at, raw, import_batch, channel_id, message_id, revision_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.id,
                    signal.source,
                    signal.symbol,
                    signal.side.value,
                    signal.asset_class.value,
                    signal.quantity,
                    signal.price,
                    signal.stop_loss,
                    signal.take_profit,
                    signal.analyst,
                    signal.received_at.isoformat(),
                    json.dumps(signal.raw),
                    signal.import_batch,
                    signal.channel_id,
                    signal.message_id,
                    signal.revision_id,
                ),
            )

    def find_signal_id_by_provider_identity(
        self, *, channel_id: str | None, message_id: str | None, revision_id: str | None
    ) -> str | None:
        """Track 5, point 6 (cross-collector/cross-transport redelivery
        dedup): the provider's own (channel_id, message_id, revision_id)
        identity is the real dedup boundary for "is this the SAME
        underlying provider event a different collector -- e.g. a bot AND
        a user-account collector both configured against the same channel
        -- (or the same collector's own reconnect) already observed" --
        independent of whichever locally-generated `Signal.id` (a fresh
        uuid4 minted by whichever adapter parsed it this time) happens to
        be attached.

        Returns the EARLIEST-persisted signal id sharing this exact
        identity, or `None` the first time this identity is seen (or
        whenever `channel_id`/`message_id` is `None`, e.g. every adapter
        this task didn't touch, which never has real provider identity to
        dedup on at all).

        See `app/engine.py`'s `_handle_signal`: it canonicalizes a
        freshly-parsed `Signal.id` onto whatever this returns BEFORE the
        existing SIG-01 per-signal-id replay-lookup (`list_orders_for_
        signal`) runs, so two collectors observing the same message never
        each independently submit their own live order for it."""
        if channel_id is None or message_id is None:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id FROM signals WHERE channel_id = ? AND message_id = ?
                   AND ((revision_id IS NULL AND ? IS NULL) OR revision_id = ?)
                   ORDER BY received_at ASC LIMIT 1""",
                (channel_id, message_id, revision_id, revision_id),
            ).fetchone()
        return row[0] if row else None

    def save_order_result(
        self,
        result: OrderResult,
        *,
        broker: str | None = None,
        symbol: str | None = None,
        side: Side | None = None,
        requested_quantity: float | None = None,
        applied_quantity: float | None = None,
        confirmed_cumulative_fill: float | None = None,
        applied_execution_delta: float | None = None,
        outstanding_possible_fill: float | None = None,
        reserved_notional: float | None = None,
        reserved_quantity: float | None = None,
        acknowledged_quantity: float | None = None,
        export_envelope: EventEnvelope | None = None,
        submitted_at: datetime | None = None,
        protection_confirmed_at: datetime | None = None,
        purpose: str | None = None,
        family_id: str | None = None,
    ) -> int:
        """Persist an order result and return its row id.

        `export_envelope` (Signal Platform Integration Correction Pack's
        own INTEGRATION_DECISION.md S6 "Commit and delivery"): when given,
        its outbox row is inserted in the SAME sqlite3 transaction as this
        order -- both commit together, or (a write failure between the two
        statements) neither does. Pass this whenever the caller has
        already built a real EXECUTION_APPLIED envelope for this fill;
        omit it for a result that isn't exportable yet (this parameter
        doesn't build the envelope itself -- see app/engine.py's own
        wiring, a later slice, for that).

        `reserved_notional` (E03, bounded): pass this order's
        app/capital_allocator.py reservation ONLY when `result.status` is
        PENDING and there's a real `result.broker_order_id` to poll --
        i.e. only when app/reconciliation.py's per-pending-order loop is
        guaranteed to eventually observe this order's terminal status and
        release it via `_correct_position`. Every other outcome (REJECTED/
        ERROR/FILLED, or a PENDING with no broker_order_id to ever poll)
        must release its reservation immediately at the call site instead
        and pass None here -- deferring release for an order nothing will
        ever revisit would leak the reservation forever, which
        app/capital_allocator.py's own docstring calls out as worse than
        the gap this closes.

        `broker`/`symbol`/`side`/`requested_quantity` are what was actually
        sent to the broker for this order (not just the original signal —
        for a resolved close, `side` is the opposing buy/sell, not
        Side.CLOSE).

        `applied_quantity` is what the caller actually applied to the
        tracked position (`SignalStore.positions.net_quantity`, i.e.
        `actual_remaining_ownership` — see that table's own SCHEMA comment)
        for this order, if anything — pass it whenever `record_fill` was
        called alongside this save, so this row's stored `filled_quantity`
        matches what the position ledger actually did. It is NEVER an
        optimistic guess from this pass on (AUD-01): a PENDING order with
        no confirmed fill yet (`result.filled_quantity is None`) must pass
        `applied_quantity=None` (or omit it) here too, matching the fact
        that `record_fill` was correctly NOT called for it — see
        app/engine.py's call sites. Omit this for calls that never touched
        the tracked position (REJECTED/ERROR results, or a save with no
        `symbol`/`side` at all).

        `confirmed_cumulative_fill` / `applied_execution_delta` /
        `outstanding_possible_fill` (AUD-01, the distinct-field quantity
        model this pass introduces to replace the old optimistic-PENDING
        behavior — see `orders`'s own SCHEMA comment in this module for
        the full contract each column implies):

        - `confirmed_cumulative_fill`: pass exactly `result.filled_quantity`
          as reported by the broker for this specific save (never a
          fallback to `requested_quantity`). `None` when the broker hasn't
          confirmed anything yet for this order.
        - `applied_execution_delta`: pass exactly what this save applied to
          `positions.net_quantity` (i.e. exactly `applied_quantity`,
          signed the same way `record_fill`'s own `side` argument implies).
          `0.0` (not `None`) when this save genuinely applied nothing —
          e.g. a still-PENDING order with nothing confirmed yet. `None`
          only for a caller that hasn't been updated to pass it.
        - `outstanding_possible_fill`: pass `requested_quantity -
          (confirmed_cumulative_fill or 0.0)` while `result.status` is
          PENDING (the quantity that could still be confirmed later, and
          must be treated as uncertain exposure — see
          `get_outstanding_possible_fill`), or `0.0` once the order is
          terminal (FILLED/REJECTED/ERROR — nothing more can fill). `None`
          for a caller that hasn't been updated to pass it.

        `submitted_at`/`protection_confirmed_at` (PU-A2): real multi-stage
        execution-latency timestamps -- see app/execution_quality.py's
        module docstring for what each one is and isn't. Both `None` (never
        a fabricated fallback) when the caller never reached that stage --
        see app/engine.py's own call sites for exactly when each is set.

        `purpose`/`family_id` (DB-0X, order purpose/family): see this
        table's own SCHEMA comment in this module for exactly what each
        value means and the real, disclosed gap for a plain account's
        close. Both `None` for a caller that hasn't been updated to pass
        them (or a genuinely unclassifiable row) -- never guessed from
        `side`/`status` after the fact.

        `reserved_quantity`/`acknowledged_quantity` (TRK-Q1, the two
        `QuantityBreakdown` fields with no pre-existing column): see the
        `orders` table's own SCHEMA comment for exactly what each means and
        the rule for when each is non-NULL. `app/quantity.py`'s
        `build_quantity_breakdown`/`acknowledged_quantity_for` are the
        preferred way for a caller to compute these consistently rather
        than re-deriving the rule inline.
        """
        stored_filled_quantity = applied_quantity if applied_quantity is not None else result.filled_quantity
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO orders
                   (account_id, broker, symbol, side, requested_quantity, signal_id, status,
                    broker_order_id, filled_quantity, filled_price, message, executed_at, reserved_notional,
                    submitted_at, protection_confirmed_at, purpose, family_id,
                    confirmed_cumulative_fill, applied_execution_delta, outstanding_possible_fill,
                    reserved_quantity, acknowledged_quantity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.account_id,
                    broker,
                    symbol,
                    side.value if side else None,
                    requested_quantity,
                    result.signal_id,
                    result.status.value,
                    result.broker_order_id,
                    stored_filled_quantity,
                    result.filled_price,
                    result.message,
                    result.executed_at.isoformat(),
                    reserved_notional,
                    submitted_at.isoformat() if submitted_at else None,
                    protection_confirmed_at.isoformat() if protection_confirmed_at else None,
                    purpose,
                    family_id,
                    confirmed_cumulative_fill,
                    applied_execution_delta,
                    outstanding_possible_fill,
                    reserved_quantity,
                    acknowledged_quantity,
                ),
            )
            if export_envelope is not None:
                self._insert_export_event(conn, export_envelope)
            # lastrowid is None only for a statement that isn't a rowid-table
            # INSERT -- never true for this one; asserted so this stays true
            # if the schema or query ever changes, rather than silently
            # returning None where every caller expects a real id.
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def _insert_export_event(self, conn: sqlite3.Connection, envelope: EventEnvelope) -> None:
        """The actual outbox INSERT, taking an ALREADY-OPEN connection so a
        caller (like `save_order_result` above) can include it in its own
        transaction. `INSERT OR IGNORE` on `event_id`: a caller that
        builds and appends the identical envelope twice (e.g. a retried
        in-process call, not a network redelivery -- that dedup happens at
        the relay/inbox, a later slice) is a harmless no-op here, not a
        duplicate outbox row."""
        conn.execute(
            """INSERT OR IGNORE INTO export_events
               (event_id, event_type, source_stream, export_sequence, envelope_json, payload_hash, appended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                envelope.event_id,
                envelope.event_type.value,
                envelope.source_stream,
                envelope.export_sequence,
                envelope.model_dump_json(),
                envelope.payload_hash,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def append_export_event(self, envelope: EventEnvelope) -> None:
        """Append a standalone outbox event in its own transaction -- for
        an event with no accompanying `orders` row to commit alongside
        (e.g. a SOURCE_RECEIPT, which records a recommendation, never an
        execution). See `save_order_result`'s own `export_envelope`
        parameter for the same-transaction case."""
        with self._connect() as conn:
            self._insert_export_event(conn, envelope)

    def next_export_sequence(self, source_stream: str) -> int:
        """The next `export_sequence` a producer should use for
        `source_stream` -- 0 for a stream with no events yet, otherwise
        one past the highest sequence already appended. Callers build
        their envelope with this value BEFORE appending; it is not itself
        transactional with the append (two concurrent producers on the
        SAME stream could race), which is fine for this slice's own single-
        process signal-copier engine -- see this method's own docstring in
        a later slice if a second concurrent producer is ever introduced."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(export_sequence) FROM export_events WHERE source_stream = ?", (source_stream,)
            ).fetchone()
        highest = row[0]
        return 0 if highest is None else highest + 1

    def list_undelivered_export_events(self, *, limit: int = 100) -> list[EventEnvelope]:
        """Every export event not yet marked delivered, oldest first by
        (source_stream, export_sequence) -- what a relay worker (a later
        slice) would poll and forward. Reconstructs the exact
        `EventEnvelope` that was appended (S6: "the relay never
        reconstructs or reinterprets it, only forwards these exact
        bytes"), never a freshly-built one from the row's own columns."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT envelope_json FROM export_events WHERE delivered_at IS NULL
                   ORDER BY source_stream, export_sequence LIMIT ?""",
                (limit,),
            ).fetchall()
        return [EventEnvelope.model_validate_json(row[0]) for row in rows]

    def mark_export_events_delivered(self, event_ids: list[str]) -> None:
        """Mark each of `event_ids` as delivered -- idempotent: an
        already-delivered event_id (or one that doesn't exist) is simply
        not matched by the UPDATE, never an error."""
        if not event_ids:
            return
        with self._connect() as conn:
            conn.executemany(
                "UPDATE export_events SET delivered_at = ? WHERE event_id = ? AND delivered_at IS NULL",
                [(datetime.now(timezone.utc).isoformat(), event_id) for event_id in event_ids],
            )

    def export_outbox_backlog(self) -> tuple[int, int]:
        """INT-040: a real, live measurement of the undelivered export
        outbox backlog -- the storage-ceiling/alerting policy this
        supports needs a genuine number, never a fabricated or estimated
        one. Returns `(row_count, total_bytes)`:

        - `row_count` is a real `COUNT(*)` over rows not yet marked
          delivered.
        - `total_bytes` is the real `SUM(LENGTH(envelope_json))` already
          stored for those exact rows -- the precise serialized size of
          every envelope this producer has appended but has not yet had
          the commercial platform acknowledge as delivered.

        Deliberately NOT this whole database file's own on-disk size
        (`os.path.getsize(DATABASE_PATH)`): that file also holds every
        other table this module defines (`orders`, `position_excursions`,
        `stop_target_events`, `backtest_runs`, `account_equity_snapshots`,
        ...), so its size conflates their own, unrelated growth with
        outbox pressure -- a large `backtest_runs` history could trip a
        file-size ceiling with zero undelivered financial evidence at
        risk, or a genuinely large outbox backlog could stay hidden
        inside an otherwise-small file right after a fresh VACUUM. Summing
        the real serialized length of exactly the rows a storage-pressure
        incident would be tempted to prune is the actionable number: it
        is exactly what would be discarded if a disk-pressure response
        ever (wrongly) truncated this table.

        `(0, 0)` for an empty backlog -- always a real, completed query,
        never `None`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(envelope_json)), 0) "
                "FROM export_events WHERE delivered_at IS NULL"
            ).fetchone()
        return (row[0], row[1])

    def create_capital_reservation(
        self, reservation_id: str, account_id: str, notional: float, signal_id: str | None = None
    ) -> None:
        """P0-4: durably record a app/capital_allocator.py provisional
        reservation the INSTANT it's admitted -- called from inside
        `CapitalAllocator.admit`, before the broker call it's gating ever
        starts (see this table's own SCHEMA comment for exactly why that
        ordering is the point). `reservation_id` is minted by the caller
        (a uuid4) so it can later resolve this exact row without a
        round-trip; `signal_id` is best-effort context for a human
        reading the table, never required for correctness."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO capital_reservations (id, account_id, notional, signal_id, created_at, resolved_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (reservation_id, account_id, notional, signal_id, datetime.now(timezone.utc).isoformat()),
            )

    def resolve_one_capital_reservation(self, account_id: str, reservation_id: str | None, notional: float) -> None:
        """Mark one durable reservation resolved -- called everywhere
        `CapitalAllocator.release` already is, so a row here goes
        unresolved for exactly as long as `_pending`'s own in-memory
        figure would have carried it. Prefers `reservation_id` (an exact,
        unambiguous match) when the caller has one; falls back to
        matching any one still-unresolved row for this account with this
        exact notional when it doesn't (a caller that only ever had the
        notional value, e.g. `CapitalAllocator.release`'s own pre-existing
        signature, which every call site already uses without a
        reservation id) -- which specific row of several identical-amount
        duplicates gets marked resolved doesn't matter, since the
        invariant this supports is only ever a per-account SUM. A
        `notional` of 0.0 never had a row to begin with (`admit` only
        inserts one for a real, non-zero reservation) so this is a safe
        no-op for it."""
        if not notional:
            return
        with self._connect() as conn:
            if reservation_id is not None:
                conn.execute(
                    "UPDATE capital_reservations SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
                    (datetime.now(timezone.utc).isoformat(), reservation_id),
                )
                return
            row = conn.execute(
                "SELECT id FROM capital_reservations WHERE account_id = ? AND notional = ? AND resolved_at IS NULL "
                "LIMIT 1",
                (account_id, notional),
            ).fetchone()
            if row is None:
                return
            conn.execute(
                "UPDATE capital_reservations SET resolved_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row[0]),
            )

    def sum_unresolved_capital_reservations(self) -> dict[str, float]:
        """Every account's real, currently-outstanding durable reservation
        total (SUM of `capital_reservations.notional` where
        `resolved_at IS NULL`) -- what `CapitalAllocator.__init__` reloads
        at startup to reconstruct its in-memory `_pending` ledger, so a
        restart resumes with exactly the reservations a crash could have
        left uncertain, never a clean slate. An account with no
        unresolved rows is simply absent from the returned dict (the
        caller's own `defaultdict(float)` already treats that as 0.0)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT account_id, SUM(notional) FROM capital_reservations "
                "WHERE resolved_at IS NULL GROUP BY account_id"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def list_pending_orders(self) -> list[dict]:
        """Orders still PENDING with a broker_order_id to re-check (see
        app/reconciliation.py). Joined to this order's originating signal
        (B5: real fills from brokers like Alpaca/IBKR that report PENDING
        at placement time and only confirm FILLED later, via this exact
        poll, were never exported to the commercial platform -- only a
        synchronous FILLED at placement time built an export envelope,
        see app/engine.py's `_build_export_envelope`) so the reconciler
        can build one too: `signal_id`/`asset_class`/`analyst` are exactly
        what `build_execution_applied_envelope` needs beyond what this
        table already carries. `orders.signal_id` is `NOT NULL REFERENCES
        signals(id)`, so this JOIN never drops a row."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT o.id, o.account_id, o.broker, o.symbol, o.side, o.requested_quantity,
                          o.filled_quantity, o.broker_order_id, o.reserved_notional,
                          o.signal_id, s.asset_class, s.analyst
                   FROM orders o JOIN signals s ON o.signal_id = s.id
                   WHERE o.status = 'pending' AND o.broker_order_id IS NOT NULL"""
            ).fetchall()
        return [
            {
                "id": r[0],
                "account_id": r[1],
                "broker": r[2],
                "symbol": r[3],
                "side": r[4],
                "requested_quantity": r[5],
                "filled_quantity": r[6],
                "broker_order_id": r[7],
                "reserved_notional": r[8],
                "signal_id": r[9],
                "asset_class": r[10],
                "analyst": r[11],
            }
            for r in rows
        ]

    def update_order_status(
        self,
        order_row_id: int,
        result: OrderResult,
        *,
        confirmed_cumulative_fill: float | None = _UNSET,  # type: ignore[assignment]
        applied_execution_delta: float | None = _UNSET,  # type: ignore[assignment]
        outstanding_possible_fill: float | None = _UNSET,  # type: ignore[assignment]
    ) -> None:
        with self._connect() as conn:
            self._update_order_status_locked(
                conn,
                order_row_id,
                result,
                confirmed_cumulative_fill=confirmed_cumulative_fill,
                applied_execution_delta=applied_execution_delta,
                outstanding_possible_fill=outstanding_possible_fill,
            )

    def _update_order_status_locked(
        self,
        conn: sqlite3.Connection,
        order_row_id: int,
        result: OrderResult,
        *,
        confirmed_cumulative_fill: float | None = _UNSET,  # type: ignore[assignment]
        applied_execution_delta: float | None = _UNSET,  # type: ignore[assignment]
        outstanding_possible_fill: float | None = _UNSET,  # type: ignore[assignment]
    ) -> None:
        """`confirmed_cumulative_fill`/`applied_execution_delta`/
        `outstanding_possible_fill` (AUD-01) each default to the private
        `_UNSET` sentinel, meaning "this call has nothing new to report for
        this column -- leave whatever was already stored." Passing an
        explicit value (including `None`, e.g. a genuinely-unknown
        confirmed fill) overwrites it. This is deliberately NOT the same as
        defaulting to `None`: an ordinary status/price/message update (the
        original, pre-AUD-01 shape of this method) must never silently
        blank out a quantity column a previous, more-informative call
        already set."""
        columns = ["status = ?", "filled_quantity = ?", "filled_price = ?", "message = ?", "executed_at = ?"]
        params: list[object] = [
            result.status.value,
            result.filled_quantity,
            result.filled_price,
            result.message,
            result.executed_at.isoformat(),
        ]
        for column_name, value in (
            ("confirmed_cumulative_fill", confirmed_cumulative_fill),
            ("applied_execution_delta", applied_execution_delta),
            ("outstanding_possible_fill", outstanding_possible_fill),
        ):
            if value is not _UNSET:
                columns.append(f"{column_name} = ?")
                params.append(value)
        params.append(order_row_id)
        conn.execute(f"UPDATE orders SET {', '.join(columns)} WHERE id = ?", params)

    def correct_position_and_update_order_status(
        self,
        order_row_id: int,
        account_id: str,
        symbol: str,
        signed_delta: float,
        result: OrderResult,
        *,
        confirmed_cumulative_fill: float | None = _UNSET,  # type: ignore[assignment]
        applied_execution_delta: float | None = _UNSET,  # type: ignore[assignment]
        outstanding_possible_fill: float | None = _UNSET,  # type: ignore[assignment]
    ) -> float:
        """Apply a reconciliation correction to `positions` and mark this
        order row's terminal status in ONE local transaction (EXE-03: these
        were two separate commits -- an interruption between them left the
        order row still `status='pending'`, so the next reconciliation pass
        re-fetched the same broker answer, recomputed the same correction
        from the same stale `orders.filled_quantity` baseline, and applied
        it a SECOND time on top of the first). `signed_delta` may be 0.0
        (no position change, e.g. a straight terminal-status confirmation)
        -- the order row is still updated in the same call so it's never
        re-processed.

        `confirmed_cumulative_fill`/`applied_execution_delta`/
        `outstanding_possible_fill` (AUD-01): see `_update_order_status_locked`'s
        docstring for the `_UNSET`-sentinel contract shared with
        `update_order_status`. `signed_delta` IS this call's
        `applied_execution_delta` by definition (the exact quantity just
        applied to `positions.net_quantity`, i.e.
        `actual_remaining_ownership`) — a caller (`OrderReconciler
        ._correct_position`) that already computed `signed_delta` should
        normally also pass it as `applied_execution_delta` for the stored
        row to agree with what this method actually did to the position."""
        with self._connect() as conn:
            current = conn.execute(
                "SELECT net_quantity FROM positions WHERE account_id = ? AND symbol = ?",
                (account_id, symbol),
            ).fetchone()
            new_quantity = (current[0] if current else 0.0) + signed_delta
            if signed_delta:
                conn.execute(
                    """INSERT INTO positions (account_id, symbol, net_quantity, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (account_id, symbol)
                       DO UPDATE SET net_quantity = excluded.net_quantity, updated_at = excluded.updated_at""",
                    (account_id, symbol, new_quantity, datetime.now(timezone.utc).isoformat()),
                )
            self._update_order_status_locked(
                conn,
                order_row_id,
                result,
                confirmed_cumulative_fill=confirmed_cumulative_fill,
                applied_execution_delta=applied_execution_delta,
                outstanding_possible_fill=outstanding_possible_fill,
            )
        return new_quantity

    def get_position(self, account_id: str, symbol: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT net_quantity FROM positions WHERE account_id = ? AND symbol = ?",
                (account_id, symbol),
            ).fetchone()
        return row[0] if row else 0.0

    def get_outstanding_possible_fill(self, account_id: str) -> dict[str, float]:
        """AUD-01: the plain-account (non-managed_lifecycle) counterpart of
        `PositionLifecycleManager.get_outstanding_possible_fill` — the
        contract every downstream reader (in particular
        app/capital_allocator.py's capital allocator, and any position-
        detail UI) should call for "how much MORE could this account's
        tracked position still move, from orders the broker hasn't finished
        confirming."

        Returns `{symbol: net_signed_outstanding_possible_fill}` for every
        symbol with at least one currently-PENDING order on this account —
        a symbol with nothing pending is simply absent (never a fabricated
        0.0 entry); callers should treat a missing key as zero, exactly
        like `PositionLifecycleManager.get_outstanding_possible_fill`.

        For each pending order, its own contribution is `requested_quantity
        - COALESCE(confirmed_cumulative_fill, 0)` — the quantity that could
        still be confirmed — signed by that order's own `side` (BUY
        positive, SELL negative: what it would do to net exposure if it
        lands), then summed per symbol across every pending order. This is
        DISTINCT from `actual_remaining_ownership` (`positions.net_quantity`,
        read via `get_position`): that column only ever reflects a
        CONFIRMED fill (see this table's own SCHEMA comment and
        app/engine.py's PENDING-order handling) and this method's result is
        never folded into it — the two are meant to be read together, not
        merged into one number, so a caller can see both "what's actually
        owned" and "what could still change" without one masking the
        other.

        Only orders with a real `broker_order_id` are included (mirrors
        `list_pending_orders`'s own filter): a PENDING order with no
        broker_order_id at all (a lost/ambiguous response — see
        app/engine.py's exception-handling branches) is never polled to a
        terminal state by app/reconciliation.py either, so it has no
        `confirmed_cumulative_fill` this method could use here — excluding
        it here is the same "don't guess, disclose the gap elsewhere"
        choice as everywhere else in this pass, not a silent undercount of
        a case this method could otherwise resolve."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT symbol, side, requested_quantity, confirmed_cumulative_fill
                   FROM orders
                   WHERE account_id = ? AND status = 'pending' AND broker_order_id IS NOT NULL
                         AND symbol IS NOT NULL AND side IS NOT NULL AND requested_quantity IS NOT NULL""",
                (account_id,),
            ).fetchall()
        outstanding: dict[str, float] = {}
        for symbol, side, requested_quantity, confirmed_cumulative_fill in rows:
            remainder = requested_quantity - (confirmed_cumulative_fill or 0.0)
            if remainder <= 0:
                continue
            signed = remainder if side == Side.BUY.value else -remainder
            outstanding[symbol] = outstanding.get(symbol, 0.0) + signed
        return outstanding

    def record_fill(
        self, account_id: str, symbol: str, side: Side, quantity: float, *, lifecycle_state: dict | None = None
    ) -> float:
        """Update the tracked position after a buy/sell and return the new net quantity.

        A `side` of BUY adds `quantity`, SELL subtracts it. Never called with
        CLOSE — the engine resolves a close into the opposing BUY/SELL before
        this is reached (see app/engine.py).

        `lifecycle_state`, if given, is written to `lifecycle_state` (see
        `save_lifecycle_state`) in the SAME local transaction as the position
        update — one commit, not two. `PositionLifecycleManager.resolve_pending_entry`/
        `_apply_exit_fill` use this so a confirmed execution delta and the
        lifecycle checkpoint that already reflects it can't be split by a
        crash landing between "position committed" and "checkpoint
        committed" (see those methods' docstrings — this is deliberately
        NOT held open across any broker I/O; it's a short, local-only
        transaction over two SQLite tables)."""
        delta = quantity if side == Side.BUY else -quantity
        return self._apply_position_delta(account_id, symbol, delta, lifecycle_state=lifecycle_state)

    def adjust_position(self, account_id: str, symbol: str, delta: float) -> float:
        """Apply a raw signed adjustment to a tracked position and return the new net
        quantity. Used directly by app/reconciliation.py to correct an optimistic fill
        (e.g. reverse it if the order actually got rejected, or true it up to the real
        filled quantity)."""
        return self._apply_position_delta(account_id, symbol, delta)

    def _apply_position_delta(
        self, account_id: str, symbol: str, delta: float, *, lifecycle_state: dict | None = None
    ) -> float:
        with self._connect() as conn:
            current = conn.execute(
                "SELECT net_quantity FROM positions WHERE account_id = ? AND symbol = ?",
                (account_id, symbol),
            ).fetchone()
            new_quantity = (current[0] if current else 0.0) + delta
            conn.execute(
                """INSERT INTO positions (account_id, symbol, net_quantity, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (account_id, symbol)
                   DO UPDATE SET net_quantity = excluded.net_quantity, updated_at = excluded.updated_at""",
                (account_id, symbol, new_quantity, datetime.now(timezone.utc).isoformat()),
            )
            if lifecycle_state is not None:
                conn.execute(
                    """INSERT INTO lifecycle_state (account_id, symbol, state, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (account_id, symbol)
                       DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at""",
                    (account_id, symbol, json.dumps(lifecycle_state), datetime.now(timezone.utc).isoformat()),
                )
        return new_quantity

    def list_open_positions(self) -> list[dict]:
        """All non-flat tracked positions, across every account/symbol."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT account_id, symbol, net_quantity, updated_at FROM positions
                   WHERE net_quantity != 0 ORDER BY updated_at DESC"""
            ).fetchall()
        return [
            {"account_id": r[0], "symbol": r[1], "net_quantity": r[2], "updated_at": r[3]} for r in rows
        ]

    def list_recent_signals(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, source, symbol, side, asset_class, quantity, price, received_at, analyst,
                          stop_loss, take_profit, raw, import_batch
                   FROM signals ORDER BY received_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "source": r[1],
                "symbol": r[2],
                "side": r[3],
                "asset_class": r[4],
                "quantity": r[5],
                "price": r[6],
                "received_at": r[7],
                # TR-04 (incoming signal stream): already-stored per-signal
                # attribution (see app/db.py's `_COLUMN_MIGRATIONS` --
                # `signals.analyst` predates this projection; it just wasn't
                # previously selected here) -- '' for "no analyst on the
                # signal" (same convention `save_signal` already writes),
                # never fabricated.
                "analyst": r[8] or None,
                # TR-05 (signal evidence and plan preview): `stop_loss` and
                # `take_profit` were always persisted per-signal (see the
                # `signals` table above and `save_signal` below) but never
                # previously projected out of this method -- TR-05's
                # "Risk/stop/horizon plan" panel needs the actual resolved
                # values, not just quantity/price. `raw` is the exact,
                # unmodified payload/text this signal was parsed from --
                # TR-05's "Original/revisions" panel's only real evidence
                # (this schema has no revision history; only the single
                # received version is ever stored, which that panel says
                # honestly rather than inventing a revision list).
                "stop_loss": r[9],
                "take_profit": r[10],
                "raw": json.loads(r[11]) if r[11] else {},
                # E02 (bounded, history-import workflow): None for a
                # live-received signal, a batch label for one created by
                # POST /sources/{source}/import-signals -- see
                # Signal.import_batch's docstring in app/models.py.
                "import_batch": r[12],
            }
            for r in rows
        ]

    def save_lifecycle_state(self, account_id: str, symbol: str, state: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO lifecycle_state (account_id, symbol, state, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (account_id, symbol)
                   DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at""",
                (account_id, symbol, json.dumps(state), datetime.now(timezone.utc).isoformat()),
            )

    def load_lifecycle_states(self) -> list[dict]:
        """Every persisted managed-lifecycle state, for
        PositionLifecycleManager.restore_from_store to resume after a
        restart. Each dict is `{"account_id": ..., "symbol": ..., **state}`."""
        with self._connect() as conn:
            rows = conn.execute("SELECT account_id, symbol, state FROM lifecycle_state").fetchall()
        return [{"account_id": r[0], "symbol": r[1], **json.loads(r[2])} for r in rows]

    def delete_lifecycle_state(self, account_id: str, symbol: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM lifecycle_state WHERE account_id = ? AND symbol = ?", (account_id, symbol)
            )

    def record_position_excursion(
        self,
        account_id: str,
        symbol: str,
        *,
        side: str,
        entry_price: float | None,
        highest_price_since_entry: float | None,
        highest_price_at: datetime | None,
        lowest_price_since_entry: float | None,
        lowest_price_at: datetime | None,
        mae: float | None,
        mfe: float | None,
        has_price_data: bool,
        closed_at: datetime,
    ) -> None:
        """PU-A1: append this now-closed position's final MAE/MFE as a new,
        durable row -- see `position_excursions`'s schema comment. Appends
        rather than upserts: the same (account_id, symbol) can legitimately
        close and later reopen as an independent episode (see
        PositionLifecycleManager.validate_plan's EXE-09 guard on a SECOND
        concurrent one), and each episode's own final excursion must remain
        its own historical row rather than overwrite the previous one."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO position_excursions
                   (account_id, symbol, side, entry_price, highest_price_since_entry, highest_price_at,
                    lowest_price_since_entry, lowest_price_at, mae, mfe, has_price_data, closed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    account_id,
                    symbol,
                    side,
                    entry_price,
                    highest_price_since_entry,
                    highest_price_at.isoformat() if highest_price_at else None,
                    lowest_price_since_entry,
                    lowest_price_at.isoformat() if lowest_price_at else None,
                    mae,
                    mfe,
                    1 if has_price_data else 0,
                    closed_at.isoformat(),
                ),
            )

    def list_position_excursions(
        self, account_id: str | None = None, symbol: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Closed positions' final MAE/MFE, newest-closed first -- the
        historical/queryable counterpart to the in-progress figures
        `GET /positions` reports for still-open managed lifecycles (see
        app/main.py's `_managed_lifecycle_snapshot`). Optionally narrowed to
        one account and/or symbol."""
        query = (
            "SELECT id, account_id, symbol, side, entry_price, highest_price_since_entry, highest_price_at, "
            "lowest_price_since_entry, lowest_price_at, mae, mfe, has_price_data, closed_at "
            "FROM position_excursions"
        )
        clauses = []
        params: list = []
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY closed_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "account_id": r[1],
                "symbol": r[2],
                "side": r[3],
                "entry_price": r[4],
                "highest_price_since_entry": r[5],
                "highest_price_at": r[6],
                "lowest_price_since_entry": r[7],
                "lowest_price_at": r[8],
                "mae": r[9],
                "mfe": r[10],
                "has_price_data": bool(r[11]),
                "closed_at": r[12],
            }
            for r in rows
        ]

    def record_equity_snapshot(
        self,
        account_id: str,
        *,
        captured_at: datetime,
        realized_pnl: float,
        unrealized_pnl: float,
        cumulative_pnl: float,
        unpriced_open_symbols: list[str] | None = None,
    ) -> None:
        """PU-A3: append one real, honestly-labeled equity/P&L snapshot for
        this account -- see `account_equity_snapshots`'s schema comment.
        Always an append (never an upsert): each snapshot is one real point
        on this account's real cumulative-P&L time series, not a
        replaceable "current state" row."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO account_equity_snapshots
                   (account_id, captured_at, realized_pnl, unrealized_pnl, cumulative_pnl, unpriced_open_symbols)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    account_id,
                    captured_at.isoformat(),
                    realized_pnl,
                    unrealized_pnl,
                    cumulative_pnl,
                    json.dumps(unpriced_open_symbols or []),
                ),
            )

    def list_equity_snapshots(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """This account's real, persisted equity/P&L snapshot series,
        oldest first (the natural order for charting a curve) -- optionally
        bounded to `[since, until]` (each inclusive)."""
        query = (
            "SELECT id, account_id, captured_at, realized_pnl, unrealized_pnl, cumulative_pnl, "
            "unpriced_open_symbols FROM account_equity_snapshots WHERE account_id = ?"
        )
        params: list = [account_id]
        if since is not None:
            query += " AND captured_at >= ?"
            params.append(since.isoformat())
        if until is not None:
            query += " AND captured_at <= ?"
            params.append(until.isoformat())
        query += " ORDER BY captured_at ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "account_id": r[1],
                "captured_at": r[2],
                "realized_pnl": r[3],
                "unrealized_pnl": r[4],
                "cumulative_pnl": r[5],
                "unpriced_open_symbols": json.loads(r[6]) if r[6] else [],
            }
            for r in rows
        ]

    def record_stop_target_event(
        self,
        account_id: str,
        symbol: str,
        *,
        event_type: str,
        at: datetime,
        price: float | None,
        previous_price: float | None,
        source: str,
    ) -> None:
        """PU-A4: append one real stop/target lifecycle event -- see
        `stop_target_events`'s schema comment. Always an append: this is a
        durable history, and the same (account_id, symbol) can legitimately
        accumulate many rows over one position's life (an initial
        placement, zero or more tightenings, a target hit) or across
        multiple independent episodes over time."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO stop_target_events
                   (account_id, symbol, event_type, at, price, previous_price, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (account_id, symbol, event_type, at.isoformat(), price, previous_price, source),
            )

    def list_stop_target_events(
        self, account_id: str, symbol: str, *, limit: int = 500
    ) -> list[dict]:
        """This position's real stop/target lifecycle event history, oldest
        first (the natural order for a later charting pass) -- the query
        surface app/main.py's `GET /positions/{account_id}/{symbol}/stop-events`
        exposes."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, account_id, symbol, event_type, at, price, previous_price, source
                   FROM stop_target_events
                   WHERE account_id = ? AND symbol = ?
                   ORDER BY at ASC, id ASC
                   LIMIT ?""",
                (account_id, symbol, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "account_id": r[1],
                "symbol": r[2],
                "event_type": r[3],
                "at": r[4],
                "price": r[5],
                "previous_price": r[6],
                "source": r[7],
            }
            for r in rows
        ]

    def claim_close(self, account_id: str, symbol: str, *, stale_after_seconds: float = 60.0) -> bool:
        """Atomically claim the exclusive right to resolve-and-submit a
        plain-account close for (account_id, symbol) -- see `close_claims`'
        schema comment for why an in-memory asyncio.Lock alone (EXE-12)
        doesn't protect against two independent processes/engine instances
        sharing this same database. Returns False if another
        process/instance already holds an unexpired claim. A claim older
        than `stale_after_seconds` is treated as abandoned (its holder
        presumably crashed) and may be re-claimed -- a safety valve against
        permanent deadlock, not a substitute for the caller actually
        releasing its own claim via `release_close` when done."""
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT claimed_at FROM close_claims WHERE account_id = ? AND symbol = ?",
                (account_id, symbol),
            ).fetchone()
            if existing is not None:
                claimed_at = datetime.fromisoformat(existing[0])
                if (now - claimed_at).total_seconds() < stale_after_seconds:
                    return False
                conn.execute(
                    "DELETE FROM close_claims WHERE account_id = ? AND symbol = ?", (account_id, symbol)
                )
            try:
                conn.execute(
                    "INSERT INTO close_claims (account_id, symbol, claimed_at) VALUES (?, ?, ?)",
                    (account_id, symbol, now.isoformat()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def release_close(self, account_id: str, symbol: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM close_claims WHERE account_id = ? AND symbol = ?", (account_id, symbol)
            )

    # --- P0-2: command_ledger -- the pre-effect durable command ledger.
    # See app/db.py's own `command_ledger` SCHEMA comment for the table's
    # full contract and app/command_ledger.py for the real call sites that
    # use these three methods.

    def open_command_ledger_entry(
        self,
        *,
        idempotency_key: str,
        command_type: "CommandType",
        account_id: str,
        environment: str,
        request_fingerprint: str,
        expected_revision: str | None = None,
        intent_id: str | None = None,
    ) -> "CommandLedgerEntry":
        """Write the PRE-EFFECT durable intent row and COMMIT it -- the
        caller must call this, and see it return, BEFORE making the actual
        broker call it describes. Never call this after the broker call;
        that would defeat the entire point (see the `command_ledger`
        table's own schema comment on why ordering is load-bearing here).

        Idempotency (this is the real dedup boundary, not `orders`' own
        best-effort in-memory locks):
          - A brand-new `idempotency_key` inserts a fresh row with
            `uncertainty_state=PENDING_SUBMISSION` and returns it. The
            caller proceeds to call the broker.
          - The SAME `idempotency_key` arriving again BEFORE the first
            call resolved (or after it resolved -- either way) with a
            MATCHING `request_fingerprint` returns the EXISTING row
            unchanged, never inserting a second one. The caller must NOT
            call the broker again -- it already has this row's tracked
            `uncertainty_state`/`remote_identifiers` to act on (replay,
            not resubmit).
          - The same `idempotency_key` with a DIFFERENT
            `request_fingerprint` is a caller bug (a genuinely different
            command reusing an old key) -- raises `CommandFingerprintMismatch`
            rather than silently allowing it through (fail closed on
            ambiguity, same posture as EXE-11's HTTP-level idempotency
            check in app/main.py).
        """
        from app.command_ledger import CommandFingerprintMismatch

        row_id = str(uuid.uuid4())
        resolved_intent_id = intent_id or row_id
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO command_ledger
                       (id, intent_id, idempotency_key, command_type, account_id, environment,
                        expected_revision, request_fingerprint, created_at, remote_identifiers,
                        uncertainty_state, terminal_evidence, resolved_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, '{}', NULL)""",
                    (
                        row_id,
                        resolved_intent_id,
                        idempotency_key,
                        command_type.value,
                        account_id,
                        environment,
                        expected_revision,
                        request_fingerprint,
                        now.isoformat(),
                        UncertaintyState.PENDING_SUBMISSION.value,
                    ),
                )
            except sqlite3.IntegrityError:
                pass  # idempotency_key already exists -- fall through to read it back below.
            else:
                return self._command_ledger_row_to_entry(
                    (
                        row_id,
                        resolved_intent_id,
                        idempotency_key,
                        command_type.value,
                        account_id,
                        environment,
                        expected_revision,
                        request_fingerprint,
                        now.isoformat(),
                        "{}",
                        UncertaintyState.PENDING_SUBMISSION.value,
                        "{}",
                        None,
                    )
                )
            existing_row = conn.execute(
                """SELECT id, intent_id, idempotency_key, command_type, account_id, environment,
                          expected_revision, request_fingerprint, created_at, remote_identifiers,
                          uncertainty_state, terminal_evidence, resolved_at
                   FROM command_ledger WHERE idempotency_key = ?""",
                (idempotency_key,),
            ).fetchone()
        assert existing_row is not None  # the IntegrityError above guarantees this row now exists
        existing = self._command_ledger_row_to_entry(existing_row)
        if existing.request_fingerprint != request_fingerprint:
            raise CommandFingerprintMismatch(
                f"idempotency_key '{idempotency_key}' was already used for a different command "
                f"(fingerprint {existing.request_fingerprint!r} != {request_fingerprint!r}) -- "
                "refusing to reuse it for a different request rather than silently allowing it through"
            )
        return existing

    def mark_command_ledger_outcome(
        self,
        idempotency_key: str,
        *,
        uncertainty_state: "UncertaintyState",
        remote_identifiers: dict | None = None,
        terminal_evidence: dict | None = None,
    ) -> None:
        """Update a command_ledger row's tracked outcome after the broker
        call this row's own pre-effect intent describes has returned (or
        raised). `resolved_at` is set (once, now) iff `uncertainty_state`
        is one of `app.models.TERMINAL_UNCERTAINTY_STATES` -- every other
        state (including `unknown_ambiguous`) leaves it NULL, so this row
        keeps showing up in `list_unresolved_command_ledger_entries` until
        something with real evidence (a reconciliation match, a later
        broker poll) resolves it. `remote_identifiers`/`terminal_evidence`
        are merged into the existing JSON dict (never replace wholesale),
        so an earlier partial write (e.g. `submitted_unconfirmed` recording
        just a broker_order_id) isn't lost when a later call adds
        `terminal_evidence` on confirmation."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT remote_identifiers, terminal_evidence FROM command_ledger WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                # Never happens on a real call site (every caller marks an
                # outcome only for a key it just opened), but a row that
                # vanished between open and mark is worth failing loudly on
                # rather than silently no-opping over.
                raise KeyError(f"no command_ledger row for idempotency_key '{idempotency_key}'")
            merged_remote = json.loads(row[0])
            merged_remote.update(remote_identifiers or {})
            merged_evidence = json.loads(row[1])
            merged_evidence.update(terminal_evidence or {})
            resolved_at = (
                datetime.now(timezone.utc).isoformat() if uncertainty_state in TERMINAL_UNCERTAINTY_STATES else None
            )
            conn.execute(
                """UPDATE command_ledger
                   SET uncertainty_state = ?, remote_identifiers = ?, terminal_evidence = ?, resolved_at = ?
                   WHERE idempotency_key = ?""",
                (
                    uncertainty_state.value,
                    json.dumps(merged_remote),
                    json.dumps(merged_evidence),
                    resolved_at,
                    idempotency_key,
                ),
            )

    def get_command_ledger_entry(self, idempotency_key: str) -> "CommandLedgerEntry | None":
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, intent_id, idempotency_key, command_type, account_id, environment,
                          expected_revision, request_fingerprint, created_at, remote_identifiers,
                          uncertainty_state, terminal_evidence, resolved_at
                   FROM command_ledger WHERE idempotency_key = ?""",
                (idempotency_key,),
            ).fetchone()
        return self._command_ledger_row_to_entry(row) if row is not None else None

    def list_unresolved_command_ledger_entries(self, account_id: str | None = None) -> list["CommandLedgerEntry"]:
        """Contract depended on by a sibling agent (P0-4, restart-survivable
        capital reservations): the durable source of truth for
        reconstructing in-flight broker commands after a restart.

        Returns every `command_ledger` row whose `uncertainty_state` is
        NOT in `app.models.TERMINAL_UNCERTAINTY_STATES` -- i.e.
        `pending_submission`, `submitted_unconfirmed`, or
        `unknown_ambiguous` -- ordered by `created_at` ascending (oldest
        first, the order those commands were actually issued in, so a
        recovery replay processes them in the same order they happened).
        Optionally filtered to one `account_id`; omit (or pass `None`) for
        every account.

        Each `CommandLedgerEntry` in the result carries: `id`, `intent_id`,
        `idempotency_key`, `command_type`, `account_id`, `environment`,
        `expected_revision` (`None` if not applicable to that command),
        `request_fingerprint`, `created_at` (the real PRE-EFFECT instant --
        this row was committed before the broker was ever called),
        `remote_identifiers` (a `dict`, `{}` if no broker order id is known
        yet -- this IS the "acknowledged exposure gap" case the audit
        names when `command_type` implies risk was taken but this dict is
        still empty), `uncertainty_state`, `terminal_evidence` (a `dict`,
        `{}` for every unresolved row by construction), and `resolved_at`
        (always `None` for a row in this result set).

        A caller reconstructing in-flight state after a restart MUST treat
        every returned row as "broker outcome unknown as of process
        death/last update" and reconcile it against the broker's own
        order/position state (or wait for app/reconciliation.py's own
        pending-order polling to resolve it) rather than assuming either
        success or failure. This method itself does no reconciliation --
        it only exposes what's durably known.
        """
        query = """SELECT id, intent_id, idempotency_key, command_type, account_id, environment,
                          expected_revision, request_fingerprint, created_at, remote_identifiers,
                          uncertainty_state, terminal_evidence, resolved_at
                   FROM command_ledger WHERE resolved_at IS NULL"""
        params: tuple = ()
        if account_id is not None:
            query += " AND account_id = ?"
            params = (account_id,)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._command_ledger_row_to_entry(r) for r in rows]

    @staticmethod
    def _command_ledger_row_to_entry(row: tuple) -> "CommandLedgerEntry":
        return CommandLedgerEntry(
            id=row[0],
            intent_id=row[1],
            idempotency_key=row[2],
            command_type=CommandType(row[3]),
            account_id=row[4],
            environment=row[5],
            expected_revision=row[6],
            request_fingerprint=row[7],
            created_at=datetime.fromisoformat(row[8]),
            remote_identifiers=json.loads(row[9]),
            uncertainty_state=UncertaintyState(row[10]),
            terminal_evidence=json.loads(row[11]),
            resolved_at=datetime.fromisoformat(row[12]) if row[12] else None,
        )

    # --- Writer lease / fencing (app/writer_lease.py, docs/FAILOVER.md) ---

    @staticmethod
    def _writer_lease_row_to_record(row) -> WriterLeaseRecord:
        return WriterLeaseRecord(
            fencing_token=row[0],
            site_id=row[1],
            holder_id=row[2],
            acquired_at=datetime.fromisoformat(row[3]),
            expires_at=datetime.fromisoformat(row[4]),
            renewed_at=datetime.fromisoformat(row[5]),
        )

    def get_writer_lease(self) -> WriterLeaseRecord | None:
        """A read-only snapshot of the current lease row -- used both by
        `WriterLeaseGuard.require_active()` (the per-command fencing
        check) and by anything reporting status (`GET /health`,
        `app/promote_cli.py`'s own preview)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fencing_token, site_id, holder_id, acquired_at, expires_at, renewed_at "
                "FROM writer_lease WHERE id = 1"
            ).fetchone()
        return self._writer_lease_row_to_record(row) if row is not None else None

    def acquire_or_reacquire_writer_lease(
        self, site_id: str, holder_id: str, lease_seconds: float, *, now: datetime | None = None
    ) -> WriterLeaseRecord:
        """Called once at startup by the ACTIVE (non-`STANDBY_MODE`)
        process only (see `app/main.py`'s `lifespan`) -- never by a
        standby. Three cases:

        - No lease row exists yet (true first boot of this database):
          claims fencing_token=1 for this site.
        - A lease row exists for THIS SAME `site_id`: this is the one
          configured active site restarting (a crash, a deploy, systemd
          restarting the unit) -- not a failover. Reacquired
          automatically, still bumping the fencing token so any zombie
          instance of the previous process (e.g. a hung request that
          never noticed the restart) is fenced too.
        - A lease row exists for a DIFFERENT `site_id`: raises
          `WriterLeaseHeldByAnotherSiteError`, unconditionally --
          including when that lease already looks expired. Automatic
          cross-site takeover is exactly the "second host trades merely
          because the first heartbeat disappeared" failure mode this
          module exists to close; the only way a different site ever
          becomes the writer is the explicit `app/promote_cli.py`
          action.
        """
        now = now or datetime.now(timezone.utc)
        expires = now + timedelta(seconds=lease_seconds)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fencing_token, site_id, holder_id, acquired_at, expires_at, renewed_at "
                "FROM writer_lease WHERE id = 1"
            ).fetchone()
            if row is None:
                new_token = 1
            else:
                existing = self._writer_lease_row_to_record(row)
                if existing.site_id != site_id:
                    raise WriterLeaseHeldByAnotherSiteError(
                        f"writer lease is held by site={existing.site_id!r} (token={existing.fencing_token}, "
                        f"expires_at={existing.expires_at.isoformat()}) -- site={site_id!r} may not acquire it "
                        "automatically; use app/promote_cli.py for a deliberate takeover"
                    )
                new_token = existing.fencing_token + 1
            conn.execute(
                "INSERT OR REPLACE INTO writer_lease "
                "(id, fencing_token, site_id, holder_id, acquired_at, expires_at, renewed_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (new_token, site_id, holder_id, now.isoformat(), expires.isoformat(), now.isoformat()),
            )
        return WriterLeaseRecord(new_token, site_id, holder_id, now, expires, now)

    def renew_writer_lease(self, holder_id: str, fencing_token: int, lease_seconds: float, *, now: datetime | None = None) -> bool:
        """Heartbeat: extends `expires_at` for the CURRENT holder/token
        only. Returns False (never raises) when this holder/token is no
        longer current -- `WriterLeaseGuard.renew()` turns that into a
        `FencedOutError`."""
        now = now or datetime.now(timezone.utc)
        expires = now + timedelta(seconds=lease_seconds)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE writer_lease SET expires_at = ?, renewed_at = ? "
                "WHERE id = 1 AND holder_id = ? AND fencing_token = ?",
                (expires.isoformat(), now.isoformat(), holder_id, fencing_token),
            )
        return cur.rowcount > 0

    def promote_writer_lease(
        self, new_site_id: str, new_holder_id: str, lease_seconds: float, *, now: datetime | None = None
    ) -> WriterLeaseRecord:
        """The ONLY way a different site ever becomes the writer (or the
        way the very first lease is created, if none exists yet) --
        called exclusively from `app/promote_cli.py`'s deliberate,
        human-run promotion command, never automatically. Verifies the
        existing lease (if any) is genuinely expired -- `expires_at` in
        the past as of `now` -- and raises `LeaseStillValidError`
        otherwise, refusing to issue a new token over a possibly-live
        writer. This one automated check (an expiry timestamp) is
        explicitly NOT a substitute for deploy/RUNBOOK.md's manual
        confirmation that the prior writer's host is actually stopped or
        its brokerage credentials revoked -- see docs/FAILOVER.md; the
        caller (`app/promote_cli.py`) is responsible for gating this call
        behind that confirmation."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fencing_token, site_id, holder_id, acquired_at, expires_at, renewed_at "
                "FROM writer_lease WHERE id = 1"
            ).fetchone()
            if row is None:
                new_token = 1
            else:
                existing = self._writer_lease_row_to_record(row)
                if not existing.is_expired(now=now):
                    raise LeaseStillValidError(
                        f"current writer lease (site={existing.site_id!r}, token={existing.fencing_token}) "
                        f"does not expire until {existing.expires_at.isoformat()} -- refusing to promote over "
                        "a possibly-live writer"
                    )
                new_token = existing.fencing_token + 1
            expires = now + timedelta(seconds=lease_seconds)
            conn.execute(
                "INSERT OR REPLACE INTO writer_lease "
                "(id, fencing_token, site_id, holder_id, acquired_at, expires_at, renewed_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (new_token, new_site_id, new_holder_id, now.isoformat(), expires.isoformat(), now.isoformat()),
            )
        return WriterLeaseRecord(new_token, new_site_id, new_holder_id, now, expires, now)

    # --- Live-editable config: accounts, routing rules, providers/analysts ---
    # See app/main.py's CRUD endpoints and app/routing.py's/app/providers.py's
    # `*_from_store` loaders — this is what makes account/routing/provider
    # changes take effect immediately, no YAML edit or restart required.

    def list_config_accounts(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT account_id, broker, multiplier, fixed_quantity, symbol_map, enabled,
                          managed_lifecycle, max_notional_exposure, risk_percent_of_equity,
                          management_recipe, qualification_level, exclusive_writer_qualified
                   FROM config_accounts ORDER BY account_id"""
            ).fetchall()
        return [
            {
                "account_id": r[0],
                "broker": r[1],
                "multiplier": r[2],
                "fixed_quantity": r[3],
                "symbol_map": json.loads(r[4]),
                "enabled": bool(r[5]),
                "managed_lifecycle": bool(r[6]),
                "max_notional_exposure": r[7],
                "risk_percent_of_equity": r[8],
                # P0-5: a row written before this migration (or one whose
                # caller never passed management_recipe explicitly) has
                # NULL here -- fall back to the same managed_lifecycle
                # -> recipe mapping DestinationAccount.__post_init__ uses,
                # rather than surface a raw NULL to a caller that expects
                # a real declared value.
                "management_recipe": r[9] or ("full_managed_lifecycle" if r[6] else "plain_unmanaged"),
                "qualification_level": r[10],
                "exclusive_writer_qualified": bool(r[11]),
            }
            for r in rows
        ]

    def upsert_config_account(
        self,
        account_id: str,
        broker: str,
        multiplier: float = 1.0,
        fixed_quantity: float | None = None,
        symbol_map: dict | None = None,
        enabled: bool = True,
        managed_lifecycle: bool = False,
        max_notional_exposure: float | None = None,
        risk_percent_of_equity: float | None = None,
        management_recipe: str | None = None,
        qualification_level: str | None = None,
        exclusive_writer_qualified: bool = False,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO config_accounts
                   (account_id, broker, multiplier, fixed_quantity, symbol_map, enabled, managed_lifecycle,
                    max_notional_exposure, risk_percent_of_equity, management_recipe, qualification_level,
                    exclusive_writer_qualified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (account_id) DO UPDATE SET
                     broker = excluded.broker, multiplier = excluded.multiplier,
                     fixed_quantity = excluded.fixed_quantity, symbol_map = excluded.symbol_map,
                     enabled = excluded.enabled, managed_lifecycle = excluded.managed_lifecycle,
                     max_notional_exposure = excluded.max_notional_exposure,
                     risk_percent_of_equity = excluded.risk_percent_of_equity,
                     management_recipe = excluded.management_recipe,
                     qualification_level = excluded.qualification_level,
                     exclusive_writer_qualified = excluded.exclusive_writer_qualified""",
                (
                    account_id,
                    broker,
                    multiplier,
                    fixed_quantity,
                    json.dumps(symbol_map or {}),
                    int(enabled),
                    int(managed_lifecycle),
                    max_notional_exposure,
                    risk_percent_of_equity,
                    # P0-5: honor an explicit caller value; otherwise persist
                    # the same managed_lifecycle-derived default
                    # DestinationAccount.__post_init__ would -- this is what
                    # makes the field a real, non-NULL declaration on write,
                    # not just on read.
                    management_recipe or ("full_managed_lifecycle" if managed_lifecycle else "plain_unmanaged"),
                    qualification_level,
                    int(exclusive_writer_qualified),
                ),
            )

    def delete_config_account(self, account_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM config_accounts WHERE account_id = ?", (account_id,))

    def has_ever_seeded(self) -> bool:
        """EXE-10: True once `mark_seeded()` has ever been called -- see
        `seed_state`'s schema comment for why this must be checked
        instead of just "is config_accounts currently empty"."""
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM seed_state WHERE id = 1").fetchone() is not None

    def mark_seeded(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seed_state (id, seeded_at) VALUES (1, ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )

    def list_config_routing_rules(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, source, destinations, symbol_filter FROM config_routing_rules ORDER BY id"
            ).fetchall()
        return [
            {
                "id": r[0],
                "source": r[1],
                "destinations": json.loads(r[2]),
                "symbol_filter": json.loads(r[3]) if r[3] else None,
            }
            for r in rows
        ]

    def insert_config_routing_rule(
        self, source: str, destinations: list[str], symbol_filter: list[str] | None = None
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO config_routing_rules (source, destinations, symbol_filter) VALUES (?, ?, ?)",
                (source, json.dumps(destinations), json.dumps(symbol_filter) if symbol_filter else None),
            )
            assert cursor.lastrowid is not None  # see save_order_result's identical comment
            return cursor.lastrowid

    def update_config_routing_rule(
        self, rule_id: int, source: str, destinations: list[str], symbol_filter: list[str] | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE config_routing_rules SET source = ?, destinations = ?, symbol_filter = ? WHERE id = ?",
                (source, json.dumps(destinations), json.dumps(symbol_filter) if symbol_filter else None, rule_id),
            )

    def delete_config_routing_rule(self, rule_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM config_routing_rules WHERE id = ?", (rule_id,))

    def list_config_providers(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT provider_id, display_name, multiplier, fixed_quantity, managed_lifecycle, enabled
                   FROM config_providers ORDER BY provider_id"""
            ).fetchall()
        return [
            {
                "provider_id": r[0],
                "display_name": r[1],
                "multiplier": r[2],
                "fixed_quantity": r[3],
                "managed_lifecycle": None if r[4] is None else bool(r[4]),
                "enabled": None if r[5] is None else bool(r[5]),
            }
            for r in rows
        ]

    def upsert_config_provider(
        self,
        provider_id: str,
        display_name: str = "",
        multiplier: float | None = None,
        fixed_quantity: float | None = None,
        managed_lifecycle: bool | None = None,
        enabled: bool | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO config_providers
                   (provider_id, display_name, multiplier, fixed_quantity, managed_lifecycle, enabled)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (provider_id) DO UPDATE SET
                     display_name = excluded.display_name, multiplier = excluded.multiplier,
                     fixed_quantity = excluded.fixed_quantity, managed_lifecycle = excluded.managed_lifecycle,
                     enabled = excluded.enabled""",
                (
                    provider_id,
                    display_name,
                    multiplier,
                    fixed_quantity,
                    None if managed_lifecycle is None else int(managed_lifecycle),
                    None if enabled is None else int(enabled),
                ),
            )

    def delete_config_provider(self, provider_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM config_providers WHERE provider_id = ?", (provider_id,))
            conn.execute("DELETE FROM config_analysts WHERE provider_id = ?", (provider_id,))

    def list_config_analysts(self, provider_id: str | None = None) -> list[dict]:
        query = """SELECT provider_id, analyst_id, display_name, multiplier, fixed_quantity,
                          managed_lifecycle, enabled FROM config_analysts"""
        params: list = []
        if provider_id:
            query += " WHERE provider_id = ?"
            params.append(provider_id)
        query += " ORDER BY provider_id, analyst_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "provider_id": r[0],
                "analyst_id": r[1],
                "display_name": r[2],
                "multiplier": r[3],
                "fixed_quantity": r[4],
                "managed_lifecycle": None if r[5] is None else bool(r[5]),
                "enabled": None if r[6] is None else bool(r[6]),
            }
            for r in rows
        ]

    def upsert_config_analyst(
        self,
        provider_id: str,
        analyst_id: str,
        display_name: str = "",
        multiplier: float | None = None,
        fixed_quantity: float | None = None,
        managed_lifecycle: bool | None = None,
        enabled: bool | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO config_analysts
                   (provider_id, analyst_id, display_name, multiplier, fixed_quantity, managed_lifecycle, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (provider_id, analyst_id) DO UPDATE SET
                     display_name = excluded.display_name, multiplier = excluded.multiplier,
                     fixed_quantity = excluded.fixed_quantity, managed_lifecycle = excluded.managed_lifecycle,
                     enabled = excluded.enabled""",
                (
                    provider_id,
                    analyst_id,
                    display_name,
                    multiplier,
                    fixed_quantity,
                    None if managed_lifecycle is None else int(managed_lifecycle),
                    None if enabled is None else int(enabled),
                ),
            )

    def delete_config_analyst(self, provider_id: str, analyst_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM config_analysts WHERE provider_id = ? AND analyst_id = ?", (provider_id, analyst_id)
            )

    # --- Signal-provider subscription cost/value tracking (app/provider_value.py) ---

    def list_provider_subscriptions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT provider_id, display_name, cost_amount, currency, billing_cycle,
                          subscribed_since, renewal_date, status, notes, created_at, updated_at
                   FROM provider_subscriptions ORDER BY provider_id"""
            ).fetchall()
        return [
            {
                "provider_id": r[0],
                "display_name": r[1],
                "cost_amount": r[2],
                "currency": r[3],
                "billing_cycle": r[4],
                "subscribed_since": r[5],
                "renewal_date": r[6],
                "status": r[7],
                "notes": r[8],
                "created_at": r[9],
                "updated_at": r[10],
            }
            for r in rows
        ]

    def get_provider_subscription(self, provider_id: str) -> dict | None:
        matches = [r for r in self.list_provider_subscriptions() if r["provider_id"] == provider_id]
        return matches[0] if matches else None

    def upsert_provider_subscription(
        self,
        provider_id: str,
        *,
        display_name: str = "",
        cost_amount: float = 0.0,
        currency: str = "USD",
        billing_cycle: str = "monthly",
        subscribed_since: str | None = None,
        renewal_date: str | None = None,
        status: str = "active",
        notes: str = "",
    ) -> None:
        """`subscribed_since` defaults to today (as an ISO date string) when
        creating a brand-new row -- never re-anchored on a later update to
        an EXISTING row (see the `COALESCE` below), so editing a
        subscription's cost/notes doesn't silently reset how long it's
        been tracked for the cost-to-date estimate."""
        now = datetime.now(timezone.utc).isoformat()
        anchor = subscribed_since or datetime.now(timezone.utc).date().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO provider_subscriptions
                   (provider_id, display_name, cost_amount, currency, billing_cycle, subscribed_since,
                    renewal_date, status, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (provider_id) DO UPDATE SET
                     display_name = excluded.display_name, cost_amount = excluded.cost_amount,
                     currency = excluded.currency, billing_cycle = excluded.billing_cycle,
                     subscribed_since = COALESCE(?, provider_subscriptions.subscribed_since),
                     renewal_date = excluded.renewal_date, status = excluded.status,
                     notes = excluded.notes, updated_at = excluded.updated_at""",
                (
                    provider_id, display_name, cost_amount, currency, billing_cycle, anchor,
                    renewal_date, status, notes, now, now,
                    subscribed_since,
                ),
            )

    def delete_provider_subscription(self, provider_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM provider_subscriptions WHERE provider_id = ?", (provider_id,))

    # --- Free-provider scouting snapshot (app/provider_scout.py) ---

    def list_provider_candidates(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT source, analyst, asset_class, closing_fills, winning_closing_fills, realized_pnl,
                          win_rate, profit_factor, recommendation, evaluated_at
                   FROM provider_candidates ORDER BY evaluated_at DESC"""
            ).fetchall()
        return [
            {
                "source": r[0],
                "analyst": r[1] or None,
                "asset_class": r[2],
                "closing_fills": r[3],
                "winning_closing_fills": r[4],
                "realized_pnl": r[5],
                "win_rate": r[6],
                "profit_factor": r[7],
                "recommendation": r[8],
                "evaluated_at": r[9],
            }
            for r in rows
        ]

    def upsert_provider_candidate(
        self,
        *,
        source: str,
        analyst: str | None,
        asset_class: str,
        closing_fills: int,
        winning_closing_fills: int,
        realized_pnl: float,
        win_rate: float | None,
        profit_factor: float | None,
        recommendation: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO provider_candidates
                   (source, analyst, asset_class, closing_fills, winning_closing_fills, realized_pnl,
                    win_rate, profit_factor, recommendation, evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (source, analyst, asset_class) DO UPDATE SET
                     closing_fills = excluded.closing_fills, winning_closing_fills = excluded.winning_closing_fills,
                     realized_pnl = excluded.realized_pnl, win_rate = excluded.win_rate,
                     profit_factor = excluded.profit_factor, recommendation = excluded.recommendation,
                     evaluated_at = excluded.evaluated_at""",
                (
                    source, analyst or "", asset_class, closing_fills, winning_closing_fills, realized_pnl,
                    win_rate, profit_factor, recommendation, now,
                ),
            )

    def delete_provider_candidate(self, source: str, analyst: str | None, asset_class: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM provider_candidates WHERE source = ? AND analyst = ? AND asset_class = ?",
                (source, analyst or "", asset_class),
            )

    def delete_provider_candidates_for_source(self, source: str) -> None:
        """Called once a source is promoted (a provider_subscriptions row
        now exists for it) -- every asset_class/analyst breakdown row
        app/provider_scout.py had accumulated for it stops being a
        "candidate" at once, not just the one row a caller happened to act
        on."""
        with self._connect() as conn:
            conn.execute("DELETE FROM provider_candidates WHERE source = ?", (source,))

    # --- Global (cross-account) fill replay for provider attribution (app/provider_value.py) ---

    def list_filled_orders_with_signal_chronological(self) -> list[dict]:
        """Every FILLED order across every account, oldest first, joined
        with its originating signal's source/analyst/asset_class -- the
        provider-attribution equivalent of `list_filled_orders_chronological`
        (which is scoped to one account and doesn't need signal identity at
        all). See app/provider_value.py for what this does and doesn't
        cover. TR-EPISODE-01: as of `PositionLifecycleManager._apply_exit_
        fill`'s own change, a managed-lifecycle stop/target/time_exit fill
        DOES now reach this table (`purpose` = 'stop_exit'/'target_exit'/
        'time_exit', `filled_price` NULL when genuinely unknown -- see that
        method's docstring) -- also includes `purpose`/`family_id` (NULL
        for any pre-existing row) so app/trade_episode.py can group every
        order belonging to the same position episode together."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT o.id, o.account_id, o.symbol, o.side, o.filled_quantity, o.filled_price, o.executed_at,
                          s.source, s.analyst, s.asset_class, o.purpose, o.family_id, o.signal_id
                   FROM orders o
                   JOIN signals s ON s.id = o.signal_id
                   WHERE o.status = 'filled'
                   ORDER BY o.executed_at ASC, o.id ASC"""
            ).fetchall()
        return [
            {
                "order_id": r[0],
                "account_id": r[1],
                "symbol": r[2],
                "side": r[3],
                "filled_quantity": r[4],
                "filled_price": r[5],
                "executed_at": r[6],
                "source": r[7],
                "analyst": r[8],
                "asset_class": r[9],
                "purpose": r[10],
                "family_id": r[11],
                "signal_id": r[12],
            }
            for r in rows
        ]

    def list_signals_in_range(
        self,
        source: str | None = None,
        symbol: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        """Every field needed to replay a historical signal (see
        app/backtest/replay.py) — including `stop_loss`/`take_profit`/
        `analyst`, which `list_recent_signals` above doesn't return. Only
        signals saved after this method's columns were added will have
        those three populated; older rows have them as `None` (see
        `_COLUMN_MIGRATIONS`) — the backtester surfaces that as a
        NO_PROTECTION_DATA case rather than assuming "no stop was ever
        requested" for a signal that predates this column existing."""
        query = """SELECT id, source, symbol, side, asset_class, quantity, price, stop_loss,
                          take_profit, analyst, received_at, raw
                   FROM signals WHERE 1=1"""
        params: list = []
        if source:
            query += " AND source = ?"
            params.append(source)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start:
            query += " AND received_at >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND received_at <= ?"
            params.append(end.isoformat())
        query += " ORDER BY received_at ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "source": r[1],
                "symbol": r[2],
                "side": r[3],
                "asset_class": r[4],
                "quantity": r[5],
                "price": r[6],
                "stop_loss": r[7],
                "take_profit": r[8],
                "analyst": r[9],
                "received_at": r[10],
                "raw": json.loads(r[11]) if r[11] else {},
            }
            for r in rows
        ]

    def save_backtest_run(
        self,
        *,
        config_hash: str,
        created_at: datetime,
        request: dict,
        summary: dict,
        trades: list[dict],
        stressed_summary: dict | None = None,
        cost_stress_note: str | None = None,
        capital_contention: dict | None = None,
    ) -> int:
        """TR-15: persist one completed POST /backtest replay -- the exact
        real request/summary/trades that endpoint already computes, never
        re-derived. Always an append (never an upsert): each run is its own
        real, reproducible record, even if a later run shares the same
        `config_hash` (a genuine repeat of the identical inputs) -- run
        history is a durable log, not a "latest result per config" cache.

        `capital_contention` is the real
        `app/backtest/replay.py`'s `CapitalContentionReport` this run
        computed (as a dict), covering B7's real cross-signal
        capital-sharing overlay -- `None` only for a run persisted before
        this field existed (a pre-existing on-disk database backfilled via
        `_COLUMN_MIGRATIONS`), never a fabricated placeholder."""
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO backtest_runs
                   (config_hash, created_at, request_json, summary_json, trades_json,
                    stressed_summary_json, cost_stress_note, capital_contention_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    config_hash,
                    created_at.isoformat(),
                    json.dumps(request),
                    json.dumps(summary),
                    json.dumps(trades),
                    json.dumps(stressed_summary) if stressed_summary is not None else None,
                    cost_stress_note,
                    json.dumps(capital_contention) if capital_contention is not None else None,
                ),
            )
            # lastrowid is None only for a statement that isn't a rowid-table
            # INSERT -- see save_order_result's identical comment.
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def list_backtest_runs(self, *, limit: int = 50) -> list[dict]:
        """Every persisted run's real identity/summary (id, config_hash,
        created_at, the real request that produced it, and its real
        summary metrics) -- most recent first. Trades are deliberately
        omitted here (that's a potentially large payload); fetch
        `get_backtest_run` for the full per-trade detail."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, config_hash, created_at, request_json, summary_json,
                          stressed_summary_json, cost_stress_note, capital_contention_json
                   FROM backtest_runs
                   ORDER BY created_at DESC, id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "config_hash": r[1],
                "created_at": r[2],
                "request": json.loads(r[3]),
                "summary": json.loads(r[4]),
                "stressed_summary": json.loads(r[5]) if r[5] else None,
                "cost_stress_note": r[6],
                "capital_contention": json.loads(r[7]) if r[7] else None,
            }
            for r in rows
        ]

    def get_backtest_run(self, run_id: int) -> dict | None:
        """This one persisted run's full real detail, including every
        replayed trade -- `None` (never a fabricated empty run) if no run
        with this id was ever persisted."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, config_hash, created_at, request_json, summary_json, trades_json,
                          stressed_summary_json, cost_stress_note, capital_contention_json
                   FROM backtest_runs WHERE id = ?""",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "config_hash": row[1],
            "created_at": row[2],
            "request": json.loads(row[3]),
            "summary": json.loads(row[4]),
            "trades": json.loads(row[5]),
            "stressed_summary": json.loads(row[6]) if row[6] else None,
            "cost_stress_note": row[7],
            "capital_contention": json.loads(row[8]) if row[8] else None,
        }

    def save_saved_view(self, *, name: str, screen: str, filters: dict, created_at: datetime) -> int:
        """Persist one real named filter set. `name` is UNIQUE at the
        schema level -- a caller trying to reuse an existing name gets a
        real `sqlite3.IntegrityError` (app/main.py turns that into a 409),
        never a silent overwrite."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO saved_views (name, screen, filters_json, created_at) VALUES (?, ?, ?, ?)",
                (name, screen, json.dumps(filters), created_at.isoformat()),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def list_saved_views(self, *, screen: str | None = None) -> list[dict]:
        """Every persisted saved view, optionally narrowed to one screen --
        most recently created first."""
        query = "SELECT id, name, screen, filters_json, created_at FROM saved_views"
        params: list = []
        if screen is not None:
            query += " WHERE screen = ?"
            params.append(screen)
        query += " ORDER BY created_at DESC, id DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "screen": r[2],
                "filters": json.loads(r[3]),
                "created_at": r[4],
            }
            for r in rows
        ]

    def delete_saved_view(self, view_id: int) -> bool:
        """Deletes one saved view by id. Returns whether a row actually
        existed to delete (app/main.py turns a `False` into a real 404
        rather than a silently-successful no-op)."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM saved_views WHERE id = ?", (view_id,))
            return cursor.rowcount > 0

    # --- Live qualification (app/qualification.py) ---------------------

    def _achieved_qualification_states(
        self, conn: sqlite3.Connection, *, adapter_type: str, route_key: str, asset_class: str, product_type: str
    ) -> "set":
        from app.qualification import QualificationState

        rows = conn.execute(
            "SELECT DISTINCT state FROM route_qualifications "
            "WHERE adapter_type = ? AND route_key = ? AND asset_class = ? AND product_type = ?",
            (adapter_type, route_key, asset_class, product_type),
        ).fetchall()
        achieved = set()
        for (state_value,) in rows:
            try:
                achieved.add(QualificationState(state_value))
            except ValueError:  # pragma: no cover - schema only ever stores valid values via this path
                continue
        return achieved

    def record_route_qualification(
        self,
        *,
        adapter_type: str,
        route_key: str,
        asset_class: str,
        product_type: str,
        state: str,
        supports_feedback: bool,
        recorded_by: str,
        notes: str | None = None,
        recorded_at: datetime | None = None,
    ) -> dict:
        """Record ONE state achieved for ONE exact route. Fails closed --
        raises `app.qualification.QualificationError` (never silently
        drops, downgrades, or reorders the request) when:

        1. `state` isn't a real `QualificationState` value.
        2. Any prerequisite rung below `state` on the ladder has not
           already been recorded for this SAME (adapter_type, route_key,
           asset_class, product_type) tuple -- see
           `app.qualification.missing_prerequisites`. A route that was
           never `authenticated` cannot jump straight to `venue_tested`.
        3. `state` is at or above `app.qualification.
           FEEDBACK_DEPENDENT_FLOOR` (account_entitled and everything
           above it) and the caller asserts `supports_feedback=False` --
           the caller (app/main.py) computes this from the REAL,
           currently-registered adapter's
           `has_account_order_position_feedback`, but this check is
           enforced here, at the persistence layer, so it can't be
           bypassed by any caller of this method (including a test or a
           future endpoint) that doesn't go through that resolution.
           `supports_feedback` defaults to nothing -- callers must pass it
           explicitly, so an unknown/unverified adapter fails closed
           rather than silently being allowed through.

        Re-recording a state that's already achieved for this route is an
        idempotent update (new recorded_at/recorded_by/notes on the same
        row, via the schema's UNIQUE constraint) -- it never re-runs the
        prerequisite check against itself.
        """
        from app.qualification import QualificationError, parse_state, missing_prerequisites, requires_feedback

        parsed_state = parse_state(state)
        if not adapter_type or not route_key or not asset_class or not product_type:
            raise QualificationError(
                "adapter_type, route_key, asset_class and product_type are all required to identify an exact route"
            )

        when = (recorded_at or datetime.now(timezone.utc)).isoformat()

        with self._connect() as conn:
            achieved = self._achieved_qualification_states(
                conn, adapter_type=adapter_type, route_key=route_key, asset_class=asset_class, product_type=product_type
            )
            if parsed_state not in achieved:
                missing = missing_prerequisites(parsed_state, achieved)
                if missing:
                    raise QualificationError(
                        f"cannot record '{parsed_state.value}' for route "
                        f"({adapter_type}/{route_key}/{asset_class}/{product_type}): "
                        f"missing prerequisite state(s) {[m.value for m in missing]} -- "
                        "the qualification ladder must be achieved in order"
                    )
                if requires_feedback(parsed_state) and not supports_feedback:
                    raise QualificationError(
                        f"cannot record '{parsed_state.value}' for route "
                        f"({adapter_type}/{route_key}/{asset_class}/{product_type}): "
                        f"adapter '{adapter_type}' has no real order-status, position-readback, or "
                        "balance-readback implementation (has_account_order_position_feedback is False) -- "
                        "there is no genuine feedback channel to verify this state with, so it structurally "
                        "cannot be claimed for any route on this adapter, regardless of operator intent"
                    )

            conn.execute(
                """
                INSERT INTO route_qualifications
                    (adapter_type, route_key, asset_class, product_type, state, recorded_at, recorded_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(adapter_type, route_key, asset_class, product_type, state)
                DO UPDATE SET recorded_at = excluded.recorded_at, recorded_by = excluded.recorded_by, notes = excluded.notes
                """,
                (adapter_type, route_key, asset_class, product_type, parsed_state.value, when, recorded_by, notes),
            )

        return {
            "adapter_type": adapter_type,
            "route_key": route_key,
            "asset_class": asset_class,
            "product_type": product_type,
            "state": parsed_state.value,
            "recorded_at": when,
            "recorded_by": recorded_by,
            "notes": notes,
        }

    def list_route_qualifications(
        self, *, adapter_type: str | None = None, route_key: str | None = None
    ) -> list[dict]:
        """Every route that has at least one recorded qualification event,
        grouped into one summary per route: its full achieved-state
        history (oldest first) plus `current_state`, the HIGHEST rung on
        the ladder actually achieved (never the most-recently-recorded row
        -- an operator recording `configured` again after `venue_tested`
        was already achieved must not regress what's shown as current)."""
        from app.qualification import QualificationState, state_index

        query = (
            "SELECT adapter_type, route_key, asset_class, product_type, state, recorded_at, recorded_by, notes "
            "FROM route_qualifications"
        )
        clauses = []
        params: list = []
        if adapter_type is not None:
            clauses.append("adapter_type = ?")
            params.append(adapter_type)
        if route_key is not None:
            clauses.append("route_key = ?")
            params.append(route_key)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY adapter_type, route_key, asset_class, product_type, recorded_at"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        routes: dict[tuple, dict] = {}
        for r in rows:
            key = (r[0], r[1], r[2], r[3])
            route = routes.setdefault(
                key,
                {
                    "adapter_type": r[0],
                    "route_key": r[1],
                    "asset_class": r[2],
                    "product_type": r[3],
                    "events": [],
                },
            )
            route["events"].append(
                {"state": r[4], "recorded_at": r[5], "recorded_by": r[6], "notes": r[7]}
            )

        result = []
        for route in routes.values():
            try:
                highest = max(
                    (QualificationState(e["state"]) for e in route["events"]),
                    key=state_index,
                )
                route["current_state"] = highest.value
            except ValueError:  # pragma: no cover - schema only ever stores valid values via this path
                route["current_state"] = None
            result.append(route)
        return result

    def is_route_release_approved(
        self, *, adapter_type: str, route_key: str, asset_class: str, product_type: str
    ) -> bool:
        """Live-routing gate read (app/engine.py's `_check_route_qualified`):
        has `QualificationState.RELEASE_APPROVED` -- the deliberate human
        sign-off, never auto-set (see app/qualification.py's own
        docstring) -- actually been recorded for this EXACT
        (adapter_type, route_key, asset_class, product_type) tuple.

        Reuses `_achieved_qualification_states` (the same read the write
        path's own prerequisite check uses), so this can never disagree
        with what `record_route_qualification`/`list_route_qualifications`
        report as achieved for the same route. Returns `False` for a route
        with zero recorded qualification events at all, and `False` for a
        route that has SOME recorded states but not `release_approved`
        itself -- there is no partial credit here; the ladder's own
        ordering already guarantees `release_approved` recorded means
        every rung below it was too."""
        from app.qualification import QualificationState

        with self._connect() as conn:
            achieved = self._achieved_qualification_states(
                conn, adapter_type=adapter_type, route_key=route_key, asset_class=asset_class, product_type=product_type
            )
        return QualificationState.RELEASE_APPROVED in achieved

    # -- Track 5: Telegram collector registry (app/telegram_collectors.py) --

    def register_telegram_collector(
        self,
        *,
        collector_id: str,
        connection_mode: str,
        identity_ref: str,
        credential_env_var: str,
        chat_id: str,
        provider_name: str,
        topic_id: str | None = None,
        allowed_uses: list[str] | None = None,
    ) -> dict:
        """Insert (or, idempotently, re-describe) one collector row.
        Validation (bad `connection_mode`/`allowed_uses`, a
        `credential_env_var` that looks like a secret value rather than a
        name) is enforced by `app.telegram_collectors.validate_registration`
        BEFORE anything is written -- this method never stores a row this
        registry's own vocabulary doesn't recognize.

        Re-registering the SAME `collector_id` replaces its identity/
        connection fields but preserves its qualification evidence,
        checkpoint, and health state (an operator re-describing which env
        var/chat a collector reads from -- e.g. after rotating a session
        file's path -- must not silently reset "this collector was
        already confirmed receiving real messages" or "here's how far
        we've already caught up live")."""
        from app.telegram_collectors import validate_registration

        mode, uses = validate_registration(
            collector_id=collector_id,
            connection_mode=connection_mode,
            identity_ref=identity_ref,
            credential_env_var=credential_env_var,
            chat_id=chat_id,
            provider_name=provider_name,
            allowed_uses=allowed_uses,
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM telegram_collectors WHERE id = ?", (collector_id,)
            ).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """INSERT INTO telegram_collectors
                       (id, connection_mode, identity_ref, credential_env_var, chat_id, topic_id,
                        provider_name, allowed_uses, qualification_evidence, health_state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 'unqualified', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       connection_mode = excluded.connection_mode,
                       identity_ref = excluded.identity_ref,
                       credential_env_var = excluded.credential_env_var,
                       chat_id = excluded.chat_id,
                       topic_id = excluded.topic_id,
                       provider_name = excluded.provider_name,
                       allowed_uses = excluded.allowed_uses,
                       updated_at = excluded.updated_at""",
                (
                    collector_id,
                    mode.value,
                    identity_ref,
                    credential_env_var,
                    str(chat_id),
                    str(topic_id) if topic_id is not None else None,
                    provider_name,
                    json.dumps(uses),
                    created_at,
                    now,
                ),
            )
        return self.get_telegram_collector(collector_id)  # type: ignore[return-value]

    def _telegram_collector_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "connection_mode": row[1],
            "identity_ref": row[2],
            "credential_env_var": row[3],
            "chat_id": row[4],
            "topic_id": row[5],
            "provider_name": row[6],
            "allowed_uses": json.loads(row[7]) if row[7] else [],
            "noforwards": bool(row[8]) if row[8] is not None else None,
            "last_qualified_at": row[9],
            "qualification_evidence": json.loads(row[10]) if row[10] else {},
            "checkpoint_message_id": row[11],
            "checkpoint_updated_at": row[12],
            "health_state": row[13],
            "health_detail": row[14],
            "created_at": row[15],
            "updated_at": row[16],
        }

    _TELEGRAM_COLLECTOR_COLUMNS = (
        "id, connection_mode, identity_ref, credential_env_var, chat_id, topic_id, provider_name, "
        "allowed_uses, noforwards, last_qualified_at, qualification_evidence, checkpoint_message_id, "
        "checkpoint_updated_at, health_state, health_detail, created_at, updated_at"
    )

    def get_telegram_collector(self, collector_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._TELEGRAM_COLLECTOR_COLUMNS} FROM telegram_collectors WHERE id = ?",
                (collector_id,),
            ).fetchone()
        return self._telegram_collector_row_to_dict(row) if row else None

    def list_telegram_collectors(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._TELEGRAM_COLLECTOR_COLUMNS} FROM telegram_collectors ORDER BY id"
            ).fetchall()
        return [self._telegram_collector_row_to_dict(r) for r in rows]

    def update_telegram_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None:
        """Point 8: the ONE place a collector's incident/health state is
        written. Validated against `app.telegram_collectors.
        CollectorHealth` so a caller can never persist a state string this
        registry doesn't recognize (which would otherwise silently render
        as neither clearly healthy nor clearly broken on a dashboard)."""
        from app.telegram_collectors import CollectorHealth

        state = CollectorHealth(health_state)  # raises ValueError for an unrecognized state
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE telegram_collectors SET health_state = ?, health_detail = ?, updated_at = ? WHERE id = ?",
                (state.value, detail, datetime.now(timezone.utc).isoformat(), collector_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no telegram collector registered with id={collector_id!r}")

    def record_telegram_collector_qualification_evidence(
        self,
        collector_id: str,
        *,
        evidence: dict,
        noforwards: bool | None = None,
        qualified_at: datetime | None = None,
    ) -> None:
        """Point 4/8: real evidence that authorized real-message receipt
        was confirmed for this collector -- e.g. `{"observed_message_id":
        "42", "observed_at": "...", "method": "live_event"}`. Setting this
        also advances `health_state` to `healthy_qualified` (the only
        state a dashboard may render as green -- see `CollectorHealth`'s
        own docstring) UNLESS `noforwards` is explicitly `True`, in which
        case `health_state` is instead set to
        `protected_content_restricted` -- ingestion itself is still real
        and recorded (this chat's protected-content flag governs
        downstream forwarding/redistribution, not raw receipt -- see
        `app/sources/telegram_user.py`'s module docstring), but a
        dashboard must not render this collector identically to one with
        no forwarding restriction at all."""
        from app.telegram_collectors import CollectorHealth

        when = (qualified_at or datetime.now(timezone.utc)).isoformat()
        health = CollectorHealth.PROTECTED_CONTENT_RESTRICTED if noforwards else CollectorHealth.HEALTHY_QUALIFIED
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE telegram_collectors
                   SET qualification_evidence = ?, last_qualified_at = ?, noforwards = ?,
                       health_state = ?, health_detail = NULL, updated_at = ?
                   WHERE id = ?""",
                (
                    json.dumps(evidence),
                    when,
                    None if noforwards is None else int(bool(noforwards)),
                    health.value,
                    when,
                    collector_id,
                ),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no telegram collector registered with id={collector_id!r}")

    def get_telegram_collector_checkpoint(self, collector_id: str) -> int | None:
        """Point 7: the last message id this collector has admitted to
        LIVE routing -- `None` for a collector that has never processed a
        live message (including one that has only ever gone through a
        historical import, which never touches this column -- see
        `advance_telegram_collector_checkpoint`)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT checkpoint_message_id FROM telegram_collectors WHERE id = ?", (collector_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no telegram collector registered with id={collector_id!r}")
        return row[0]

    def advance_telegram_collector_checkpoint(self, collector_id: str, message_id: int) -> None:
        """Point 7: called ONLY after a message has been genuinely
        admitted to live routing (never for a historical-import row, and
        never for a message this collector is merely re-observing at or
        below its current checkpoint). Monotonic -- never moves the
        checkpoint backward, so an out-of-order redelivery can't un-admit
        messages that were already caught up to."""
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE telegram_collectors
                   SET checkpoint_message_id = MAX(COALESCE(checkpoint_message_id, ?), ?),
                       checkpoint_updated_at = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    message_id,
                    message_id,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    collector_id,
                ),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no telegram collector registered with id={collector_id!r}")

    # -- Track 6: Slack/Twitter user-context collector registry -----------
    # (app/collector_registry.py) -- one shared `pull_collectors` table;
    # see that module's own docstring for why it's one table, not two.

    def register_pull_collector(
        self,
        *,
        collector_id: str,
        provider: str,
        auth_mode: str,
        identity_ref: str,
        credential_env_var: str,
        target_id: str,
        provider_name: str,
        target_label: str | None = None,
        allowed_uses: list[str] | None = None,
    ) -> dict:
        """Insert (or, idempotently, re-describe) one collector row.
        Mirrors `register_telegram_collector`'s own contract exactly:
        validation happens BEFORE anything is written
        (`app.collector_registry.validate_registration`), and
        re-registering the SAME `collector_id` replaces identity/
        connection fields but preserves qualification evidence, checkpoint,
        and health state."""
        from app.collector_registry import validate_registration

        provider_enum, uses = validate_registration(
            collector_id=collector_id,
            provider=provider,
            auth_mode=auth_mode,
            identity_ref=identity_ref,
            credential_env_var=credential_env_var,
            target_id=target_id,
            provider_name=provider_name,
            allowed_uses=allowed_uses,
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM pull_collectors WHERE id = ?", (collector_id,)
            ).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """INSERT INTO pull_collectors
                       (id, provider, auth_mode, identity_ref, credential_env_var, target_id, target_label,
                        provider_name, allowed_uses, qualification_evidence, health_state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', 'unqualified', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       provider = excluded.provider,
                       auth_mode = excluded.auth_mode,
                       identity_ref = excluded.identity_ref,
                       credential_env_var = excluded.credential_env_var,
                       target_id = excluded.target_id,
                       target_label = excluded.target_label,
                       provider_name = excluded.provider_name,
                       allowed_uses = excluded.allowed_uses,
                       updated_at = excluded.updated_at""",
                (
                    collector_id,
                    provider_enum.value,
                    auth_mode,
                    identity_ref,
                    credential_env_var,
                    str(target_id),
                    target_label,
                    provider_name,
                    json.dumps(uses),
                    created_at,
                    now,
                ),
            )
        return self.get_pull_collector(collector_id)  # type: ignore[return-value]

    def _pull_collector_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "provider": row[1],
            "auth_mode": row[2],
            "identity_ref": row[3],
            "credential_env_var": row[4],
            "target_id": row[5],
            "target_label": row[6],
            "provider_name": row[7],
            "allowed_uses": json.loads(row[8]) if row[8] else [],
            "last_qualified_at": row[9],
            "qualification_evidence": json.loads(row[10]) if row[10] else {},
            "checkpoint": row[11],
            "checkpoint_updated_at": row[12],
            "health_state": row[13],
            "health_detail": row[14],
            "created_at": row[15],
            "updated_at": row[16],
        }

    _PULL_COLLECTOR_COLUMNS = (
        "id, provider, auth_mode, identity_ref, credential_env_var, target_id, target_label, provider_name, "
        "allowed_uses, last_qualified_at, qualification_evidence, checkpoint, checkpoint_updated_at, "
        "health_state, health_detail, created_at, updated_at"
    )

    def get_pull_collector(self, collector_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._PULL_COLLECTOR_COLUMNS} FROM pull_collectors WHERE id = ?",
                (collector_id,),
            ).fetchone()
        return self._pull_collector_row_to_dict(row) if row else None

    def list_pull_collectors(self, provider: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if provider is not None:
                rows = conn.execute(
                    f"SELECT {self._PULL_COLLECTOR_COLUMNS} FROM pull_collectors WHERE provider = ? ORDER BY id",
                    (provider,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {self._PULL_COLLECTOR_COLUMNS} FROM pull_collectors ORDER BY id"
                ).fetchall()
        return [self._pull_collector_row_to_dict(r) for r in rows]

    def update_pull_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None:
        """The ONE place a Slack/Twitter collector's incident/health
        state is written. Validated against
        `app.collector_registry.CollectorHealth` so a caller can never
        persist a state string this registry doesn't recognize."""
        from app.collector_registry import CollectorHealth

        state = CollectorHealth(health_state)  # raises ValueError for an unrecognized state
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE pull_collectors SET health_state = ?, health_detail = ?, updated_at = ? WHERE id = ?",
                (state.value, detail, datetime.now(timezone.utc).isoformat(), collector_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no pull collector registered with id={collector_id!r}")

    def record_pull_collector_qualification_evidence(
        self,
        collector_id: str,
        *,
        evidence: dict,
        qualified_at: datetime | None = None,
    ) -> None:
        """Real evidence that authorized real-message receipt was
        confirmed for this collector. Always advances `health_state` to
        `healthy_qualified` (the only state a dashboard may render as
        green) -- unlike Telegram's equivalent, there is no verified
        Slack/Twitter `noforwards`-style downstream-restriction flag to
        branch on (see `app/collector_registry.py`'s module docstring)."""
        from app.collector_registry import CollectorHealth

        when = (qualified_at or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE pull_collectors
                   SET qualification_evidence = ?, last_qualified_at = ?,
                       health_state = ?, health_detail = NULL, updated_at = ?
                   WHERE id = ?""",
                (
                    json.dumps(evidence),
                    when,
                    CollectorHealth.HEALTHY_QUALIFIED.value,
                    when,
                    collector_id,
                ),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no pull collector registered with id={collector_id!r}")

    def get_pull_collector_checkpoint(self, collector_id: str) -> str | None:
        """The last message/tweet id this collector has admitted to LIVE
        routing -- `None` for a collector that has never processed one
        (including one that has only gone through a historical import,
        which never touches this column)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM pull_collectors WHERE id = ?", (collector_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no pull collector registered with id={collector_id!r}")
        return row[0]

    def advance_pull_collector_checkpoint(self, collector_id: str, checkpoint: str) -> None:
        """Called ONLY after a message/tweet has genuinely been admitted
        to live routing. Unlike `advance_telegram_collector_checkpoint`,
        this does not enforce monotonicity at the SQL level (`checkpoint`
        is an opaque TEXT value here -- see `app/collector_registry.py`'s
        module docstring) -- each adapter's own `_admits_live`-style check
        is the actual source of truth for "is this genuinely newer,"
        exactly like the value it's about to pass in was already
        compared before this is called."""
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE pull_collectors
                   SET checkpoint = ?, checkpoint_updated_at = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    str(checkpoint),
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    collector_id,
                ),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no pull collector registered with id={collector_id!r}")

    # -- Track 9: website collector registry (app/website_collectors.py) --

    def register_website_collector(
        self,
        *,
        collector_id: str,
        site_format: str,
        site_id: str,
        provider_name: str,
        feed_url: str | None = None,
        article_list_url: str | None = None,
        analyst: str | None = None,
        auth_state_env_var: str | None = None,
        allowed_uses: list[str] | None = None,
    ) -> dict:
        """Insert (or, idempotently, re-describe) one website collector
        row -- same reasoning as `register_telegram_collector`: validated
        BEFORE anything is written, and re-registering the SAME
        `collector_id` preserves qualification evidence, checkpoints, and
        health state."""
        from app.website_collectors import validate_registration

        fmt, uses = validate_registration(
            collector_id=collector_id,
            site_format=site_format,
            site_id=site_id,
            provider_name=provider_name,
            feed_url=feed_url,
            article_list_url=article_list_url,
            allowed_uses=allowed_uses,
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM website_collectors WHERE id = ?", (collector_id,)
            ).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """INSERT INTO website_collectors
                       (id, site_format, site_id, provider_name, feed_url, article_list_url, analyst,
                        auth_state_env_var, allowed_uses, qualification_evidence, health_state,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', 'unqualified', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       site_format = excluded.site_format,
                       site_id = excluded.site_id,
                       provider_name = excluded.provider_name,
                       feed_url = excluded.feed_url,
                       article_list_url = excluded.article_list_url,
                       analyst = excluded.analyst,
                       auth_state_env_var = excluded.auth_state_env_var,
                       allowed_uses = excluded.allowed_uses,
                       updated_at = excluded.updated_at""",
                (
                    collector_id,
                    fmt.value,
                    site_id,
                    provider_name,
                    feed_url,
                    article_list_url,
                    analyst,
                    auth_state_env_var,
                    json.dumps(uses),
                    created_at,
                    now,
                ),
            )
        return self.get_website_collector(collector_id)  # type: ignore[return-value]

    def _website_collector_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "site_format": row[1],
            "site_id": row[2],
            "provider_name": row[3],
            "feed_url": row[4],
            "article_list_url": row[5],
            "analyst": row[6],
            "auth_state_env_var": row[7],
            "allowed_uses": json.loads(row[8]) if row[8] else [],
            "last_qualified_at": row[9],
            "qualification_evidence": json.loads(row[10]) if row[10] else {},
            "checkpoint_article_url": row[11],
            "checkpoint_seen_urls": json.loads(row[12]) if row[12] else [],
            "checkpoint_updated_at": row[13],
            "health_state": row[14],
            "health_detail": row[15],
            "created_at": row[16],
            "updated_at": row[17],
        }

    _WEBSITE_COLLECTOR_COLUMNS = (
        "id, site_format, site_id, provider_name, feed_url, article_list_url, analyst, "
        "auth_state_env_var, allowed_uses, last_qualified_at, qualification_evidence, "
        "checkpoint_article_url, checkpoint_seen_urls, checkpoint_updated_at, health_state, "
        "health_detail, created_at, updated_at"
    )

    def get_website_collector(self, collector_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._WEBSITE_COLLECTOR_COLUMNS} FROM website_collectors WHERE id = ?",
                (collector_id,),
            ).fetchone()
        return self._website_collector_row_to_dict(row) if row else None

    def list_website_collectors(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._WEBSITE_COLLECTOR_COLUMNS} FROM website_collectors ORDER BY id"
            ).fetchall()
        return [self._website_collector_row_to_dict(r) for r in rows]

    def update_website_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None:
        """The ONE place a website collector's incident/health state is
        written -- validated against `app.website_collectors.
        CollectorHealth`, same as `update_telegram_collector_health`."""
        from app.website_collectors import CollectorHealth

        state = CollectorHealth(health_state)  # raises ValueError for an unrecognized state
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE website_collectors SET health_state = ?, health_detail = ?, updated_at = ? WHERE id = ?",
                (state.value, detail, datetime.now(timezone.utc).isoformat(), collector_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no website collector registered with id={collector_id!r}")

    def record_website_collector_qualification_evidence(
        self, collector_id: str, *, evidence: dict, qualified_at: datetime | None = None
    ) -> None:
        """Real evidence that a real article was genuinely fetched and
        processed for this collector -- advances health_state to
        `healthy_qualified` (the only state a dashboard may render as
        green)."""
        from app.website_collectors import CollectorHealth

        when = (qualified_at or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE website_collectors
                   SET qualification_evidence = ?, last_qualified_at = ?,
                       health_state = ?, health_detail = NULL, updated_at = ?
                   WHERE id = ?""",
                (json.dumps(evidence), when, CollectorHealth.HEALTHY_QUALIFIED.value, when, collector_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no website collector registered with id={collector_id!r}")

    def get_website_collector_seen_urls(self, collector_id: str) -> set[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT checkpoint_seen_urls FROM website_collectors WHERE id = ?", (collector_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no website collector registered with id={collector_id!r}")
        return set(json.loads(row[0]) if row[0] else [])

    def advance_website_collector_checkpoint(self, collector_id: str, *, article_url: str) -> None:
        """Called ONLY after an article has been genuinely admitted to
        live processing. Appends to the seen-URL set (ARTICLE_LIST mode's
        checkpoint) AND advances `checkpoint_article_url` to the latest
        one seen (FEED mode's checkpoint) -- a collector may switch
        `site_format` later, so both are kept current regardless of which
        mode is currently configured."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT checkpoint_seen_urls FROM website_collectors WHERE id = ?", (collector_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no website collector registered with id={collector_id!r}")
            seen = set(json.loads(row[0]) if row[0] else [])
            seen.add(article_url)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE website_collectors
                   SET checkpoint_seen_urls = ?, checkpoint_article_url = ?,
                       checkpoint_updated_at = ?, updated_at = ?
                   WHERE id = ?""",
                (json.dumps(sorted(seen)), article_url, now, now, collector_id),
            )

    # -- Track 9: website article trade candidates ------------------------

    def find_website_candidate_by_url(self, *, channel_id: str, message_id: str) -> dict | None:
        """The dedup lookup keyed on this source's own provider identity
        -- (site_id, canonical article URL) -- mirrors
        `find_signal_id_by_provider_identity`'s reasoning exactly, just
        for a candidate row rather than a `signals` row."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, channel_id, message_id, classification, resolved, signal_id,
                          published_at, modified_at, candidate_json, created_at, updated_at
                   FROM website_article_candidates WHERE channel_id = ? AND message_id = ?""",
                (channel_id, message_id),
            ).fetchone()
        return self._website_candidate_row_to_dict(row) if row else None

    def _website_candidate_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "channel_id": row[1],
            "message_id": row[2],
            "classification": row[3],
            "resolved": bool(row[4]),
            "signal_id": row[5],
            "published_at": row[6],
            "modified_at": row[7],
            "candidate": json.loads(row[8]),
            "created_at": row[9],
            "updated_at": row[10],
        }

    def upsert_website_candidate(
        self,
        *,
        channel_id: str,
        message_id: str,
        classification: str,
        resolved: bool,
        candidate_json: dict,
        signal_id: str | None = None,
        published_at: datetime | None = None,
        modified_at: datetime | None = None,
    ) -> dict:
        """Insert a new candidate, or -- for the SAME (channel_id,
        message_id) -- update the EXISTING row in place. Never creates a
        duplicate for the same canonical URL (the Track 9 brief's own
        explicit requirement: 'an article revision ... should update the
        EXISTING candidate record ... never create a duplicate').

        A revision only actually overwrites when the new `modified_at` is
        strictly newer than what's already stored (or the existing row
        has no `modified_at` at all) -- a redelivery of the SAME
        unmodified article is a no-op re-observe, not treated as a fresh
        revision."""
        import uuid as _uuid

        existing = self.find_website_candidate_by_url(channel_id=channel_id, message_id=message_id)
        now = datetime.now(timezone.utc).isoformat()
        if existing is not None:
            existing_modified = existing.get("modified_at")
            if modified_at is not None and existing_modified and modified_at.isoformat() <= existing_modified:
                return existing  # not a newer revision -- leave the stored row untouched
            with self._connect() as conn:
                conn.execute(
                    """UPDATE website_article_candidates
                       SET classification = ?, resolved = ?, signal_id = ?, published_at = ?,
                           modified_at = ?, candidate_json = ?, updated_at = ?
                       WHERE channel_id = ? AND message_id = ?""",
                    (
                        classification,
                        int(resolved),
                        signal_id,
                        published_at.isoformat() if published_at else None,
                        modified_at.isoformat() if modified_at else None,
                        json.dumps(candidate_json),
                        now,
                        channel_id,
                        message_id,
                    ),
                )
            return self.find_website_candidate_by_url(channel_id=channel_id, message_id=message_id)  # type: ignore[return-value]

        candidate_id = str(_uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO website_article_candidates
                       (id, channel_id, message_id, classification, resolved, signal_id,
                        published_at, modified_at, candidate_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate_id,
                    channel_id,
                    message_id,
                    classification,
                    int(resolved),
                    signal_id,
                    published_at.isoformat() if published_at else None,
                    modified_at.isoformat() if modified_at else None,
                    json.dumps(candidate_json),
                    now,
                    now,
                ),
            )
        return self.find_website_candidate_by_url(channel_id=channel_id, message_id=message_id)  # type: ignore[return-value]

    def list_orders_for_signal(self, signal_id: str) -> list[dict]:
        """Every order already recorded against this exact signal id — what
        SIG-01's engine-level dedup checks before routing/submitting a
        signal again: if this id already produced order results, those are
        replayed instead of re-submitting to every destination a second
        time."""
        query = """SELECT id, account_id, broker, symbol, side, requested_quantity, signal_id,
                          status, broker_order_id, filled_quantity, filled_price, message, executed_at
                   FROM orders WHERE signal_id = ? ORDER BY id ASC"""
        with self._connect() as conn:
            rows = conn.execute(query, (signal_id,)).fetchall()
        return [
            {
                "id": r[0],
                "account_id": r[1],
                "broker": r[2],
                "symbol": r[3],
                "side": r[4],
                "requested_quantity": r[5],
                "signal_id": r[6],
                "status": r[7],
                "broker_order_id": r[8],
                "filled_quantity": r[9],
                "filled_price": r[10],
                "message": r[11],
                "executed_at": r[12],
            }
            for r in rows
        ]

    def list_filled_orders_chronological(self, account_id: str) -> list[dict]:
        """Every FILLED order for this account, oldest first -- the replay
        order app/economics.py needs to reconstruct realized P&L via
        average-cost lot accounting. Unlike `list_recent_orders`, this has
        no LIMIT: a P&L computation that silently dropped older fills would
        misstate cost basis and realized gains, not just show fewer rows."""
        query = """SELECT id, account_id, broker, symbol, side, requested_quantity, signal_id,
                          status, broker_order_id, filled_quantity, filled_price, message, executed_at
                   FROM orders WHERE account_id = ? AND status = 'filled' ORDER BY executed_at ASC, id ASC"""
        with self._connect() as conn:
            rows = conn.execute(query, (account_id,)).fetchall()
        return [
            {
                "id": r[0],
                "account_id": r[1],
                "broker": r[2],
                "symbol": r[3],
                "side": r[4],
                "requested_quantity": r[5],
                "signal_id": r[6],
                "status": r[7],
                "broker_order_id": r[8],
                "filled_quantity": r[9],
                "filled_price": r[10],
                "message": r[11],
                "executed_at": r[12],
            }
            for r in rows
        ]

    def list_filled_orders_with_signal_timing(self, account_id: str) -> list[dict]:
        """Every FILLED order for this account joined to its originating
        signal's `received_at`, plus this order's own PU-A2 stage
        timestamps (`submitted_at`/`protection_confirmed_at`) --
        app/execution_quality.py's source for both the original
        signal-to-fill latency and the finer-grained stage breakdown built
        on top of it. Every one of `submitted_at`/`protection_confirmed_at`
        may be `None` for a given row (a rejection before submission, a
        non-managed_lifecycle account, or a managed entry whose stop was
        never confirmed) -- that module's own docstring says exactly which
        stages this schema does and doesn't separately track."""
        query = """SELECT o.symbol, o.executed_at, s.received_at, o.submitted_at, o.protection_confirmed_at
                   FROM orders o JOIN signals s ON o.signal_id = s.id
                   WHERE o.account_id = ? AND o.status = 'filled'
                   ORDER BY o.executed_at ASC"""
        with self._connect() as conn:
            rows = conn.execute(query, (account_id,)).fetchall()
        return [
            {
                "symbol": r[0],
                "executed_at": r[1],
                "received_at": r[2],
                "submitted_at": r[3],
                "protection_confirmed_at": r[4],
            }
            for r in rows
        ]

    def list_filled_orders_with_signal_reference_price(self, account_id: str) -> list[dict]:
        """TR-EPISODE-01 (P&L completeness): every FILLED order for this
        account joined to its originating signal's own `price` field --
        the provider's reference/intended price, when the signal carried
        one -- the real basis app/account_economics_v2.py uses to compute
        slippage/implementation shortfall. `signal_price` is `None` for a
        signal that never carried a price (most alert-only signals) --
        that order is honestly excluded from the slippage sample rather
        than compared against a fabricated reference."""
        query = """SELECT o.symbol, o.side, o.filled_quantity, o.filled_price, o.executed_at, s.price
                   FROM orders o JOIN signals s ON o.signal_id = s.id
                   WHERE o.account_id = ? AND o.status = 'filled'
                   ORDER BY o.executed_at ASC, o.id ASC"""
        with self._connect() as conn:
            rows = conn.execute(query, (account_id,)).fetchall()
        return [
            {
                "symbol": r[0],
                "side": r[1],
                "filled_quantity": r[2],
                "filled_price": r[3],
                "executed_at": r[4],
                "signal_price": r[5],
            }
            for r in rows
        ]

    def list_recent_orders(self, limit: int = 50, account_id: str | None = None) -> list[dict]:
        query = """SELECT id, account_id, broker, symbol, side, requested_quantity, signal_id,
                          status, broker_order_id, filled_quantity, filled_price, message, executed_at,
                          purpose, family_id
                   FROM orders"""
        params: list = []
        if account_id:
            query += " WHERE account_id = ?"
            params.append(account_id)
        query += " ORDER BY executed_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "account_id": r[1],
                "broker": r[2],
                "symbol": r[3],
                "side": r[4],
                "requested_quantity": r[5],
                "signal_id": r[6],
                "status": r[7],
                "broker_order_id": r[8],
                "filled_quantity": r[9],
                "filled_price": r[10],
                "message": r[11],
                "executed_at": r[12],
                # DB-0X: NULL (never fabricated) for any order row saved
                # before these two columns existed -- see this table's own
                # SCHEMA comment for exactly what each real value means.
                "purpose": r[13],
                "family_id": r[14],
            }
            for r in rows
        ]

    # --- owner sessions (app/auth.py) ---

    def create_session(
        self, session_id: str, csrf_token: str, expires_at: datetime, credential_epoch: str | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sessions (session_id, csrf_token, created_at, expires_at, credential_epoch)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    session_id,
                    csrf_token,
                    datetime.now(timezone.utc).isoformat(),
                    expires_at.isoformat(),
                    credential_epoch,
                ),
            )

    def get_session(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, csrf_token, expires_at, credential_epoch FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {"session_id": row[0], "csrf_token": row[1], "expires_at": row[2], "credential_epoch": row[3]}

    def delete_all_sessions(self) -> None:
        """Explicit sign-out-everywhere -- see app/auth.py's credential-epoch
        binding for why this is now rarely needed on its own (a credential
        change already invalidates every existing session automatically),
        but an owner may still want to revoke sessions without rotating
        either secret (e.g. a shared/public device)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions")

    def delete_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    def delete_expired_sessions(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now(timezone.utc).isoformat(),))

    # --- idempotent financial commands (app/main.py's close/flatten routes) ---

    def get_idempotent_response(self, idempotency_key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response_json FROM idempotency_records WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_idempotent_record(self, idempotency_key: str) -> dict | None:
        """Like `get_idempotent_response`, but also returns the fingerprint
        the caller stored alongside it (EXE-11): the same idempotency key
        reused for a DIFFERENT action/target must not silently replay the
        first action's response as if it were this one's -- the caller
        compares this record's `fingerprint` against its own before
        deciding whether to replay or refuse the reuse."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response_json, fingerprint FROM idempotency_records WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return {"response": json.loads(row[0]), "fingerprint": row[1]}

    def save_idempotent_response(self, idempotency_key: str, response: dict, fingerprint: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO idempotency_records (idempotency_key, response_json, created_at, fingerprint)
                   VALUES (?, ?, ?, ?)""",
                (idempotency_key, json.dumps(response), datetime.now(timezone.utc).isoformat(), fingerprint),
            )
