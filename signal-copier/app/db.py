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
from typing import Any, Iterator

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
from app.certification import (
    ALL_CHECKS,
    CHECK_KIND,
    CertificationError,
    CheckKind,
    is_live_eligible,
    parse_check_name,
    validate_check_record,
    validate_scope,
)
from app.connections import compute_connection_health, default_capabilities_for_connection_type, validate_connection_registration
from app.parser_tooling import (
    ParserProfileStatus,
    ParserToolingError,
    validate_profile_registration,
    validate_profile_transition,
    validate_supported_message_types,
)
from app.provider_catalog import validate_provider_registration, validate_source_registration
from app.unified_collectors import CollectorKind, UnifiedCollectorError, now_utc
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
    -- E04 (bounded): fee tracking for financial correctness
    fee DECIMAL(18, 8),
    fee_currency TEXT,
    slippage DECIMAL(18, 8),
    -- E-11/B-10: the currency in which filled_price is quoted for this order.
    -- ISO 4217 code (e.g., 'USD', 'JPY' for USDJPY, 'BTC' for BTC/USD pairs, etc.).
    -- NULL means this row predates the column or the price currency was not tracked.
    -- Never fabricated/guessed from symbol syntax; must come from broker or adapter.
    price_currency TEXT,
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
    exclusive_writer_qualified INTEGER NOT NULL DEFAULT 0,
    -- E04 (bounded): daily loss limit circuit breaker for risk control
    daily_loss_limit_percent DECIMAL(5, 2),
    min_equity_threshold DECIMAL(18, 8),
    -- E-11/B-10: account base currency for multi-currency support.
    -- ISO 4217 code (e.g., 'USD', 'EUR', 'JPY'). NULL means not declared;
    -- assume USD for backward compatibility only when reading existing
    -- configurations. Never fabricated/defaulted server-side for new accounts.
    currency TEXT,
    -- B-11: maximum gross leverage ceiling (e.g., 1.0 = no leverage,
    -- 1.25 = 25% leverage allowed). When set, exposure is refused if it
    -- would exceed max_gross_leverage × (equity − maintenance_margin).
    max_gross_leverage DECIMAL(5, 2),
    -- B-14: whether this account is allowed to open short positions
    allow_short INTEGER NOT NULL DEFAULT 0,
    -- WP-38 (G-C-13): the EvidenceClass value (e.g., "INTERNAL_PAPER",
    -- "OBSERVED_OWNER_LIVE") to export for this account's events.
    -- NULL means use the global config.RELAY_EVIDENCE_CLASS as fallback.
    evidence_class TEXT,
    -- WP-38 (G-C-24): monotonic counter for paper broker order IDs,
    -- persisted per account to remain unique across restarts.
    -- Only used when broker='paper'; NULL/unused for other brokers.
    paper_order_id_sequence INTEGER
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
    -- Track 42: set only for an event the relay reported back as a
    -- STRUCTURALLY parked status (app/relay_worker.py's own
    -- `classify_parked_reason` -- a `parked_reason` the relay side can
    -- never resolve by itself, e.g. `source_event_kind_not_ledger_
    -- representable`: the ledger model has no column for it at all, so
    -- no amount of waiting or redelivery ever changes the outcome
    -- without a code change on the commercial side). Deliberately a
    -- SEPARATE column from `delivered_at`, never reusing it: this event
    -- was never economically applied, so marking it "delivered" would
    -- be exactly the dishonest "delivered == applied" conflation this
    -- track exists to remove. Excluded from `list_undelivered_export_
    -- events` once set (see that query's own WHERE clause) so a
    -- structurally-unparkable event stops being resent forever, but it
    -- is never deleted/pruned -- same "never discard financial
    -- evidence" rule INT-040's own standing test enforces for this
    -- table.
    terminal_park_reason TEXT,
    terminal_parked_at TEXT,
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
-- ALLOC-03: a GLOBAL notional ceiling for one strategy (keyed by signal
-- `source`, the same key Track 18's ownership derivation uses). Counted
-- ONCE across every account the strategy can use -- adding accounts never
-- multiplies it. NULL max_notional = no ceiling configured (unset blocks
-- nothing; it is an opt-in gate like max_notional_exposure).
CREATE TABLE IF NOT EXISTS strategy_budgets (
    strategy_key TEXT PRIMARY KEY,
    max_notional REAL,
    updated_at TEXT NOT NULL
);

-- ALLOC-01: one durable logical allocation decision per trade opportunity,
-- created BEFORE any account-specific execution. `intent_id` is derived
-- from (strategy_key, signal_id) -- NOT from an account -- so N eligible
-- accounts can never each own an independent copy of the same
-- opportunity. States: claimed (pool recorded, no account chosen) ->
-- selected (one account bound, pre-submission) -> committed (a
-- submission was attempted; ANY outcome, incl. unknown, is final for
-- destination purposes -- never rerouted) | skipped (explained
-- non-execution, no exposure created). See docs/design/PORTFOLIO_ALLOCATION.md.
CREATE TABLE IF NOT EXISTS allocation_intents (
    intent_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE,
    strategy_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    candidates TEXT NOT NULL,
    selected_account_id TEXT,
    state TEXT NOT NULL,
    reason TEXT,
    trace TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_allocation_intents_state ON allocation_intents (state);

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
-- Track 8: the UNIFIED collector registry -- see app/unified_collectors.py's
-- module docstring for the full design rationale. One row per collector
-- across every provider kind that shares this common registration/
-- qualification/checkpoint/health shape (telegram, pull [slack/twitter],
-- email, website) -- see that module for exactly which of the five
-- pre-existing Track 5/6/7/9 registries were migrated onto this table and
-- which (notification-bridge devices) were deliberately left on their own
-- dedicated table because their shape is genuinely NOT the same (no
-- allowed_uses/qualification_evidence concept, a hashed pairing token
-- rather than a credential_env_var reference, and read-time health-state
-- overrides tied to heartbeat staleness that don't apply to any of the
-- other four).
--
-- `kind` is the top-level registry this row belongs to ('telegram' |
-- 'pull' | 'email' | 'website'); `provider` is that registry's own
-- existing discriminator column, reused verbatim (telegram's
-- connection_mode, pull's provider, email's connection_mode, website's
-- site_format) so migrating existing data is a lossless, mechanical
-- rename, not a re-interpretation.
--
-- `credential_env_var` is a REFERENCE ONLY, never a secret value -- same
-- guarantee as every pre-existing registry's own column of the same name
-- (see docs/security/SECRETS.md).
--
-- `checkpoint` is a JSON-encoded scalar (an int for telegram/email, a
-- string for pull/website's single most-recent-admitted URL) -- each
-- kind's own adapter is still the sole source of truth for what counts
-- as "genuinely newer" before calling advance_collector_checkpoint,
-- exactly like the pre-existing per-table columns it replaces.
--
-- `provider_config` is a JSON object holding whatever fields are
-- genuinely kind-specific and were never common across all five
-- registries to begin with (telegram's chat_id/topic_id/noforwards;
-- pull's auth_mode/target_id/target_label; email's imap_host/imap_port/
-- imap_folder/sender_allowlist/subject_patterns/poll_interval_seconds;
-- website's site_id/feed_url/article_list_url/analyst/
-- auth_state_env_var/checkpoint_seen_urls). These were deliberately NOT
-- promoted to dedicated columns because none of them is queried/filtered/
-- indexed on across kinds -- only `kind`/`provider`/`id` ever are (see
-- the two indexes below) -- so a shared JSON slice loses no real
-- capability while avoiding a table with a different set of NULL columns
-- per kind.
CREATE TABLE IF NOT EXISTS collectors (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    identity_ref TEXT,
    credential_env_var TEXT,
    provider_name TEXT,
    allowed_uses TEXT NOT NULL DEFAULT '["private_trading"]',
    last_qualified_at TEXT,
    qualification_evidence TEXT NOT NULL DEFAULT '{}',
    checkpoint TEXT,
    checkpoint_updated_at TEXT,
    health_state TEXT NOT NULL DEFAULT 'unqualified',
    health_detail TEXT,
    provider_config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_collectors_kind ON collectors (kind);
CREATE INDEX IF NOT EXISTS idx_collectors_kind_provider ON collectors (kind, provider);

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

-- Track 10: the persistent notification-bridge device registry -- see
-- app/notification_bridge.py's module docstring for the full contract.
-- One row per Android device this deployment accepts notification-bridge
-- uploads from.
--
-- `pairing_token_hash` is an argon2id hash (pwdlib) of the per-device
-- bearer token the owner types into the Android app once at pairing time
-- -- the raw token itself is NEVER persisted anywhere (see
-- app/notification_bridge.py's hash_pairing_token/verify_pairing_token).
--
-- `app_packages` is a JSON array of the Android package names this
-- device is authorized to forward notifications for; `provider_mapping`
-- is a JSON object app_package -> {"provider_name": ..., "analyst": ...}
-- used to build an accepted notification's Signal.source/Signal.analyst.
--
-- `recent_completeness` is a JSON array (bounded to
-- app.notification_bridge.COMPLETENESS_WINDOW_SIZE entries, oldest
-- first) of this device's most recent AUTHORIZED events' completeness
-- (true == ContentCompleteness.COMPLETE) -- the real, measurable
-- evidence app.notification_bridge's CONTENT_COMPLETENESS_DEGRADED
-- verdict is computed from (see
-- SignalStore.record_notification_bridge_completeness), never a
-- fabricated/guessed health flip.
--
-- `health_state` is app.notification_bridge.DeviceHealth's own
-- vocabulary. `no_heartbeat_recently` is deliberately NEVER persisted
-- here as such -- it's a read-time computation against
-- `last_heartbeat_at` (see SignalStore.get_notification_bridge_device),
-- since "is the heartbeat stale RIGHT NOW" changes with no write of its
-- own.
-- Track 20: the device-metadata/allowed-blocked-apps columns below
-- (device_name through blocked_apps) are ALL nullable/empty-default and
-- populated ONLY when the Android app itself reports them via
-- POST /ingest/notification-bridge/{device_id}'s optional
-- `device_metadata` payload -- see app.notification_bridge.
-- NotificationBridgeDevice's own docstring for the full "never
-- fabricated, honest None until reported" contract each one follows.
-- `allowed_apps`/`blocked_apps` are device-scoped, ADDITIVE to (never a
-- replacement for) app.phone_escalation's global BROKER_APP_PACKAGES/
-- is_denied_app_package deny-list -- see
-- app.notification_bridge.validate_device_app_lists.
CREATE TABLE IF NOT EXISTS notification_bridge_devices (
    device_id TEXT PRIMARY KEY,
    pairing_token_hash TEXT NOT NULL,
    app_packages TEXT NOT NULL DEFAULT '[]',
    provider_mapping TEXT NOT NULL DEFAULT '{}',
    last_heartbeat_at TEXT,
    recent_completeness TEXT NOT NULL DEFAULT '[]',
    health_state TEXT NOT NULL DEFAULT 'never_paired',
    health_detail TEXT,
    device_name TEXT,
    platform TEXT,
    model TEXT,
    os_version TEXT,
    agent_version TEXT,
    network_status TEXT,
    battery_level INTEGER,
    is_charging INTEGER,
    notification_permission_granted INTEGER,
    accessibility_permission_granted INTEGER,
    screen_control_capability INTEGER,
    ai_agent_capability INTEGER,
    allowed_apps TEXT NOT NULL DEFAULT '[]',
    blocked_apps TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Track 7: the persistent email collector registry -- see
-- app/email_collectors.py's module docstring for the full contract and
-- each enum's own docstring for the real, closed set of values
-- connection_mode/health_state take. One row per mailbox/folder this
-- deployment polls via app/sources/email_source.py's EmailSource.
--
-- `credential_env_var` is a REFERENCE ONLY -- the name of an environment
-- variable this collector's real credential (an IMAP app password) is
-- read from at process startup. Never a secret value itself -- see
-- docs/security/SECRETS.md and docs/security/EMAIL_COLLECTOR.md.
--
-- `sender_allowlist` is a JSON array of sender addresses this collector
-- admits (point 4: never parses every message in the inbox
-- indiscriminately) and `subject_patterns` an optional JSON array of
-- additional substrings a subject must contain at least one of.
--
-- `allowed_uses` is a JSON array of app/email_collectors.py's
-- `AllowedUse` values, defaulting to `["private_trading"]` only -- same
-- isolation guarantee as the telegram_collectors table above.
--
-- `checkpoint_uid` is the per-collector restart-recovery checkpoint
-- (point 5): the last IMAP UID this collector has admitted to LIVE
-- routing. NULL for a collector that has never processed a live message.
-- A historical import never advances this column -- see
-- app/sources/email_source.py's `import_history`.
CREATE TABLE IF NOT EXISTS email_collectors (
    id TEXT PRIMARY KEY,
    connection_mode TEXT NOT NULL,
    identity_ref TEXT NOT NULL,
    credential_env_var TEXT NOT NULL,
    imap_host TEXT NOT NULL,
    imap_port INTEGER NOT NULL DEFAULT 993,
    imap_folder TEXT NOT NULL,
    sender_allowlist TEXT NOT NULL DEFAULT '[]',
    subject_patterns TEXT NOT NULL DEFAULT '[]',
    provider_name TEXT NOT NULL,
    allowed_uses TEXT NOT NULL DEFAULT '["private_trading"]',
    poll_interval_seconds INTEGER NOT NULL DEFAULT 60,
    last_qualified_at TEXT,
    qualification_evidence TEXT NOT NULL DEFAULT '{}',
    checkpoint_uid INTEGER,
    checkpoint_updated_at TEXT,
    health_state TEXT NOT NULL DEFAULT 'no_messages_observed',
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
CREATE INDEX IF NOT EXISTS idx_email_collectors_provider ON email_collectors (provider_name);
CREATE INDEX IF NOT EXISTS idx_email_collectors_mailbox ON email_collectors (imap_host, imap_folder);
-- Track 10: one row per notification event this bridge has ever
-- accepted for auth-checking/processing (ANY outcome -- live-routed,
-- historical-backlog-import-only, needs-human-review, duplicate/retry,
-- or rejected as unauthorized) -- the dedup/audit ledger for
-- (device_id, notification_key), Track 10's own analogue of Track 5's
-- provider-identity dedup (see SignalStore.
-- find_signal_id_by_provider_identity's own docstring for the pattern
-- this mirrors).
--
-- `content_hash` is this event's own content fingerprint
-- (app.notification_bridge.content_fingerprint) -- a redelivery of an
-- already-seen `notification_key` with the SAME hash is a true
-- duplicate/retry (Android's own `StatusBarNotification.key` is stable
-- per notification instance, and a flaky upload may legitimately retry);
-- a DIFFERENT hash for the same `notification_key` is a real Android
-- notification UPDATE, recorded as the NEXT `revision_seq` and exported
-- as a SourceEvent EDIT, never silently treated as a duplicate or a
-- brand new signal.
--
-- `classification` records exactly which of this event's real, honest
-- outcomes applied: "live" (routed to engine.handle_signal),
-- "stale_backlog_import_only" (point 4: posted_at too old relative to
-- this server's own received_at, recorded via the same import_batch-
-- tagged path Track 5 uses for historical import, never live-routed),
-- "needs_review_incomplete_content" (point 5: content_completeness was
-- not "complete" -- recorded, never parsed as if it were the full
-- alert), "duplicate_retry", or "rejected_unauthorized_app_package".
--
-- Track 12: `content_completeness` now stores app.notification_bridge.
-- ContentCompleteness's five-state PER-EVENT classification (see that
-- enum's own docstring) rather than a passthrough of the device's own
-- three-state report. `needs_escalation`/`escalation_status` are the
-- interface Track 13's active AI phone-retrieval escalation layer reads
-- and writes -- see SignalStore.list_notification_bridge_events_
-- needing_escalation's own docstring for the exact read/write contract.
-- `escalation_status` is NULL until an event is actually flagged
-- (needs_escalation=0), then "pending" until Track 13 (or an owner,
-- manually) resolves it to "resolved"/"failed" via
-- SignalStore.resolve_notification_bridge_event_escalation.
CREATE TABLE IF NOT EXISTS notification_bridge_events (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    app_package TEXT NOT NULL,
    notification_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    revision_seq INTEGER NOT NULL DEFAULT 1,
    content_completeness TEXT NOT NULL,
    posted_at TEXT,
    received_at TEXT NOT NULL,
    classification TEXT NOT NULL,
    signal_id TEXT,
    created_at TEXT NOT NULL,
    needs_escalation INTEGER NOT NULL DEFAULT 0,
    escalation_status TEXT
);

CREATE INDEX IF NOT EXISTS idx_notification_bridge_events_key ON notification_bridge_events (device_id, notification_key);
CREATE INDEX IF NOT EXISTS idx_notification_bridge_events_escalation ON notification_bridge_events (needs_escalation, escalation_status);

-- Track 12: cross-transport signal correlation evidence -- see
-- app/signal_correlation.py's own module docstring for the fingerprint
-- this is keyed by. One row per OTHER transport's signal that was
-- compared against an already-recorded "canonical" signal sharing its
-- fingerprint key: `match_type` "corroborating" means it was folded
-- into the canonical signal (no second order -- see app/engine.py's
-- `_handle_signal`), "conflicting" means it shared the fingerprint key
-- but disagreed materially on price/side and was held out of live
-- routing entirely (see Signal.import_batch's "cross_transport_
-- conflict:" prefix) rather than either being silently dropped or
-- silently preferred over the canonical signal.
CREATE TABLE IF NOT EXISTS signal_correlation_evidence (
    id TEXT PRIMARY KEY,
    canonical_signal_id TEXT NOT NULL,
    evidence_signal_id TEXT NOT NULL,
    fingerprint_key TEXT NOT NULL,
    source TEXT NOT NULL,
    channel_id TEXT,
    message_id TEXT,
    price REAL,
    side TEXT,
    received_at TEXT NOT NULL,
    match_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_correlation_evidence_canonical ON signal_correlation_evidence (canonical_signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_correlation_evidence_fp ON signal_correlation_evidence (fingerprint_key);

-- Track 13: per-provider/app active phone-control-retrieval config -- see
-- app/phone_escalation.py's module docstring for the full escalation-only
-- design and app.phone_escalation.CapabilityState for the closed
-- state vocabulary. `app_package` is UNIQUE: one config row per Android
-- app this deployment might ever escalate to. `capability_state` starts
-- 'disabled' for every fresh row (enforced in Python by
-- app.phone_escalation.validate_config_registration, which has no
-- capability_state parameter at all -- see
-- SignalStore.register_phone_escalation_config) and can only ever be
-- promoted/demoted through an explicit, owner-gated call
-- (SignalStore.set_phone_escalation_capability_state, validated against
-- app.phone_escalation.validate_state_transition's DISABLED -> SHADOW ->
-- ENABLED promotion policy).
--
-- `adapter_backend` names which app.phone_escalation.PhoneControlAdapter
-- implementation this provider uses (e.g. 'adb') -- NULL (the default)
-- means no real backend is wired, which app.phone_escalation.
-- evaluate_escalation treats identically to capability_state='disabled'
-- (fail closed: a state without a usable backend must never behave as if
-- retrieval actually ran).
CREATE TABLE IF NOT EXISTS phone_escalation_configs (
    id TEXT PRIMARY KEY,
    app_package TEXT NOT NULL UNIQUE,
    provider_name TEXT NOT NULL,
    adapter_backend TEXT,
    capability_state TEXT NOT NULL DEFAULT 'disabled',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_phone_escalation_configs_app_package ON phone_escalation_configs (app_package);

-- Track 13: the audit ledger of every escalation DECISION this deployment
-- has ever made (attempted or not) -- see
-- app.phone_escalation.EscalationAttempt/EscalationDisposition for the
-- real, honest outcome vocabulary this table's `disposition` column is
-- constrained to at the application layer. Recorded for EVERY captured
-- event that reached app.phone_escalation.evaluate_escalation, never only
-- for the ones where retrieval actually ran -- same "always record, never
-- silently skip" audit convention as notification_bridge_events above.
--
-- `disposition='shadow_logged_only'` rows are, by construction (see
-- app.phone_escalation.evaluate_escalation), never linked to a live
-- signal_id -- shadow-mode extraction is recorded here for the operator
-- to review accuracy against and NEVER fed into engine.handle_signal.
CREATE TABLE IF NOT EXISTS phone_escalation_attempts (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    app_package TEXT NOT NULL,
    notification_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    capability_state_at_attempt TEXT NOT NULL,
    disposition TEXT NOT NULL,
    extraction_status TEXT,
    extraction_detail TEXT,
    signal_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_phone_escalation_attempts_device ON phone_escalation_attempts (device_id, notification_key);

-- Track 20: per-(device, app_package) mobile app configuration -- the
-- backend for "Settings -> Mobile Devices -> Signal Phone -> Apps ->
-- Whop" (see app/notification_bridge.py's own MobileAppConfig docstring
-- for the full field-by-field contract). `package_name` must be one of
-- the owning device's own `app_packages` (app.notification_bridge.
-- validate_mobile_app_config) -- FOREIGN KEY not declared (this
-- codebase's other per-device child tables, e.g.
-- notification_bridge_events, don't declare one either) but enforced at
-- the application layer the same way.
--
-- `active_retrieval_allowed` is this row's own per-DEVICE switch,
-- AND-gated with (never a replacement for) phone_escalation_configs'
-- existing GLOBAL-per-app_package capability_state lifecycle -- see
-- SignalStore.get_mobile_app_config's own docstring for exactly how the
-- two compose, and this table's own design note in
-- docs/adr/ (Track 20) for why phone_escalation_configs was kept global
-- rather than migrated to per-device scoping.
--
-- `notification_title_patterns`/`conversation_patterns`/
-- `expected_screens`/`navigation_recipe`/`content_extraction_schema` are
-- all JSON (list or object) columns -- honest, empty-by-default
-- forward-declared config, never something this table's own writers
-- interpret or execute.
CREATE TABLE IF NOT EXISTS mobile_app_configs (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    display_name TEXT,
    capture_notifications INTEGER NOT NULL DEFAULT 1,
    active_retrieval_allowed INTEGER NOT NULL DEFAULT 0,
    retrieval_mode TEXT NOT NULL DEFAULT 'notification_only',
    notification_title_patterns TEXT NOT NULL DEFAULT '[]',
    conversation_patterns TEXT NOT NULL DEFAULT '[]',
    expected_screens TEXT NOT NULL DEFAULT '[]',
    navigation_recipe TEXT NOT NULL DEFAULT '[]',
    ai_fallback_allowed INTEGER NOT NULL DEFAULT 0,
    max_navigation_steps INTEGER NOT NULL DEFAULT 10,
    timeout_seconds INTEGER NOT NULL DEFAULT 30,
    screenshot_retention TEXT NOT NULL DEFAULT 'none',
    content_extraction_schema TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (device_id, package_name)
);

CREATE INDEX IF NOT EXISTS idx_mobile_app_configs_device ON mobile_app_configs (device_id);

-- Track 14: the Provider/Source/Connection data model -- see
-- app/provider_catalog.py's own module docstring for the full
-- rationale and for exactly how these three tables relate to every
-- earlier registry (collectors, notification_bridge_devices,
-- phone_escalation_configs) this track builds additively on top of,
-- without replacing any of them.
--
-- `providers`: ONE row per real-world signal-provider identity,
-- independent of how many transports (`sources` rows) reach it.
-- `status`/`execution_eligibility`/`certification_state` are each
-- constrained, at the application layer, to
-- app.provider_catalog.ProviderStatus/ExecutionEligibility/
-- CertificationState. `execution_eligibility` is an ADDITIONAL gate
-- that composes with, and never replaces, app/engine.py's own
-- `_check_route_qualified` per-route qualification gate -- see this
-- table's own column and app/provider_catalog.py's docstring for that
-- relationship. NEVER a credential column on this table -- see
-- app/provider_catalog.py's hard rule.
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    logo_url TEXT,
    website TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'onboarding',
    account_ownership TEXT,
    subscription_status TEXT,
    classification TEXT,
    asset_classes TEXT NOT NULL DEFAULT '[]',
    strategy_types TEXT NOT NULL DEFAULT '[]',
    provider_timezone TEXT,
    execution_eligibility TEXT NOT NULL DEFAULT 'disabled',
    default_parser_profile TEXT,
    max_entry_age_seconds INTEGER,
    stale_exit_policy TEXT,
    min_parse_confidence REAL,
    correlation_window_seconds INTEGER,
    risk_policy_ref TEXT,
    certification_state TEXT NOT NULL DEFAULT 'uncertified',
    certification_version TEXT,
    certified_at TEXT,
    operator_notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_providers_status ON providers (status);

-- `sources`: MANY rows per provider, one per transport/channel that
-- provider's alerts arrive through -- this is the layer that
-- represents "the same provider sends the same trade through Whop,
-- Telegram, email, SMS and a website" as 5 `sources` rows under 1
-- `providers` row, each with its own `role`
-- (PRIMARY/SECONDARY/FALLBACK/RECONCILIATION/DISCOVERY_ONLY -- see
-- app.provider_catalog.SourceRole). `connection_id` is a nullable FK
-- (SQLite does not enforce it across `ALTER`-free CREATE IF NOT
-- EXISTS bootstraps in this codebase's existing convention -- see
-- every other table above -- so it's application-enforced, in
-- app/db.py's `register_source`) to the reusable `connections` row
-- this source's events actually arrive over; NULL for a source with no
-- connection registered yet (e.g. discovered but not yet wired up).
-- `platform` is an open string (telegram/slack/twitter/email/website/
-- notification_bridge/whop/...), matching `connections.connection_type`
-- in spirit -- see app/provider_catalog.py's docstring for why this is
-- deliberately not a closed enum.
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    source_type TEXT,
    source_native_id TEXT,
    display_name TEXT,
    url_or_reference TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    role TEXT NOT NULL DEFAULT 'PRIMARY',
    capture_method TEXT,
    connection_id TEXT,
    parser_profile TEXT,
    asset_classes TEXT NOT NULL DEFAULT '[]',
    strategy_types TEXT NOT NULL DEFAULT '[]',
    freshness_policy TEXT NOT NULL DEFAULT '{}',
    dedup_policy TEXT NOT NULL DEFAULT '{}',
    execution_eligibility TEXT NOT NULL DEFAULT 'disabled',
    health_state TEXT NOT NULL DEFAULT 'unqualified',
    last_event_at TEXT,
    last_success_at TEXT,
    last_error_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- Track 24: an opaque, adapter-owned poll checkpoint (JSON-encoded
    -- scalar, same "opaque to this table, meaningful only to the
    -- adapter that wrote it" convention as `collectors.checkpoint`) --
    -- see app/sources/rss_source.py's own module docstring for why a
    -- new column here, rather than a new `collectors` row, is this
    -- adapter's checkpoint home: `register_collector`/`advance_
    -- collector_checkpoint` enforce a CLOSED `CollectorKind` enum
    -- (telegram/pull/email/website) -- widening that enum for an
    -- adjacent, source-observation-pipeline adapter this track does not
    -- wire into the pre-existing collector registries would be a wider,
    -- unrelated blast radius than this one nullable column. `sources`
    -- already has exactly one row per transport (Track 14) -- a bounded,
    -- source-scoped checkpoint fits that shape directly.
    acquisition_checkpoint TEXT
);

CREATE INDEX IF NOT EXISTS idx_sources_provider_id ON sources (provider_id);
CREATE INDEX IF NOT EXISTS idx_sources_connection_id ON sources (connection_id);

-- `connections`: the reusable, credential-bearing transport a `sources`
-- row points at. ONE connection can serve MANY sources across MANY
-- providers -- see app/provider_catalog.py's docstring for the "one
-- Gmail account, 15 providers" motivating example. `credential_
-- reference` is ONLY an environment-variable NAME (or an existing hash
-- reference, e.g. notification-bridge's pairing-token hash) -- NEVER a
-- raw credential value, application-enforced by
-- app.connections.validate_connection_registration. `capabilities` is
-- a JSON object over the keys app.connections.CONNECTION_CAPABILITY_
-- KEYS documents (realtime_events/history/.../active_retrieval) --
-- this track only reserves the shape, it does not build capability
-- discovery/introspection itself.
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    connection_type TEXT NOT NULL,
    display_name TEXT,
    credential_reference TEXT,
    authentication_type TEXT,
    account_identity TEXT,
    connection_state TEXT NOT NULL DEFAULT 'unconfigured',
    authorization_state TEXT NOT NULL DEFAULT 'unauthorized',
    scopes TEXT NOT NULL DEFAULT '[]',
    capabilities TEXT NOT NULL DEFAULT '{}',
    rate_limits TEXT NOT NULL DEFAULT '{}',
    cost_info TEXT NOT NULL DEFAULT '{}',
    last_authenticated_at TEXT,
    token_expires_at TEXT,
    last_heartbeat_at TEXT,
    last_successful_event_at TEXT,
    last_error_at TEXT,
    last_error_detail TEXT,
    retry_state TEXT NOT NULL DEFAULT '{}',
    health_score REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_connections_connection_type ON connections (connection_type);

-- Track 19: append-only connection cost-event ledger -- see
-- SignalStore.record_connection_cost_event/get_connection_cost_summary.
-- Same "audit ledger, only ever appended to, never mutated/deleted"
-- convention as signal_correlation_evidence/phone_escalation_attempts.
-- `amount`/`category` are the only required fields (a recorded cost is
-- always attributed to SOME category, e.g. "api"/"ai"/"infra"); every
-- other numeric field (ai_calls/tokens/browser_minutes/
-- mobile_agent_calls) is NULL unless a caller actually recorded one --
-- never a fabricated placeholder. This table only RECORDS/AGGREGATES
-- costs a caller reports -- it never auto-detects real spend from any
-- live provider API (X/Twilio/etc. billing), which is explicitly out of
-- scope for this track (see app/db.py's own record_connection_cost_event
-- docstring).
CREATE TABLE IF NOT EXISTS connection_cost_events (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    category TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 1,
    ai_calls INTEGER,
    tokens INTEGER,
    browser_minutes REAL,
    mobile_agent_calls INTEGER,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_connection_cost_events_connection_id ON connection_cost_events (connection_id);
CREATE INDEX IF NOT EXISTS idx_connection_cost_events_occurred_at ON connection_cost_events (connection_id, occurred_at);

-- Track 17: provider certification checklist -- see
-- app/certification.py's module docstring for the full design. ONE row
-- per (provider_id, source_id, asset_class, account_route, check_name)
-- -- the scope tuple the user's own spec requires ("scoped by provider
-- x source x asset class x account/broker route, not just provider"),
-- plus which of the 14 checks this row is. `status` is one of
-- app.certification.CheckStatus's own values, starting 'NOT_RUN' for
-- every freshly-created row (never a fabricated default PASS).
-- `evidence` is a JSON object -- for an AUTOMATED check (see
-- app.certification.CHECK_KIND), the real data point(s)
-- app/certification_evidence.py computed this verdict from; for an
-- ATTESTATION_ONLY check, the operator's own note describing what they
-- verified (REQUIRED, never empty, for a PASS/FAIL -- see
-- app.certification.validate_check_record). `checked_by` is the owner/
-- operator identity that recorded this row -- NULL only for an
-- AUTOMATED check's freshly-recomputed view (nobody "checked" it by
-- hand; see SignalStore.compute_certification_check). There is
-- DELIBERATELY no `live_eligible` column anywhere in this table or
-- schema -- that is always a derived computation
-- (app.certification.is_live_eligible) over this table's own rows,
-- never a separately-settable flag that could drift out of sync.
CREATE TABLE IF NOT EXISTS certification_checks (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    account_route TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NOT_RUN',
    evidence TEXT NOT NULL DEFAULT '{}',
    checked_at TEXT,
    checked_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider_id, source_id, asset_class, account_route, check_name)
);

CREATE INDEX IF NOT EXISTS idx_certification_checks_scope
    ON certification_checks (provider_id, source_id, asset_class, account_route);

-- Track 17: SHADOW MODE results -- "what would the live system have
-- done?" without submitting anything. See app/shadow_mode.py's module
-- docstring for the full design (real routing/sizing logic reused, no
-- broker ever touched). ONE row per computed hypothetical order --
-- `signal_id` is the real triggering signal, `computed_hypothetical_order`
-- is the full app.shadow_mode.ShadowOrderIntent, serialized (symbol,
-- side, quantity, expected_entry, stop_price, targets) -- and
-- `policy_reference` is the real config/version string that decision
-- was computed from (the user's own example: "Why: TradeAlgo Options
-- Policy v4"), never a decorative label.
CREATE TABLE IF NOT EXISTS shadow_mode_results (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL,
    expected_entry REAL,
    stop_price REAL,
    targets TEXT NOT NULL DEFAULT '[]',
    policy_reference TEXT NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    computed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_mode_results_signal_id ON shadow_mode_results (signal_id);
CREATE INDEX IF NOT EXISTS idx_shadow_mode_results_provider_id ON shadow_mode_results (provider_id);

-- Track 15: sample-driven parser tooling -- see app/parser_tooling.py's
-- module docstring for the full rationale. Additive on top of Track 14's
-- providers/sources/connections tables above, never modifying them
-- (`sources.parser_profile` already reserved that column; this track
-- gives it something real to point at, without wiring it into the live
-- signal-handling path -- see app/parser_tooling.py's own docstring).
--
-- `parser_samples`: one row per historical raw message a provider's
-- "learn from samples" workflow was shown, plus its automatic
-- classification/extraction (`app.parser_tooling.classify_message_type`/
-- `extract_fields`) and, once an owner corrects any field, the
-- owner-supplied ground truth for that same message. `is_corrected`
-- distinguishes a still-automatic sample from one an owner has reviewed
-- and confirmed/fixed -- only corrected samples are usable as ground
-- truth for a future accuracy computation (see
-- `SignalStore.correct_parser_sample`'s own docstring).
CREATE TABLE IF NOT EXISTS parser_samples (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    provider_id TEXT,
    raw_text TEXT NOT NULL,
    message_type TEXT,
    extracted_fields TEXT NOT NULL DEFAULT '{}',
    disposition_outcome TEXT,
    is_corrected INTEGER NOT NULL DEFAULT 0,
    corrected_message_type TEXT,
    corrected_fields TEXT,
    correction_note TEXT,
    corrected_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parser_samples_source_id ON parser_samples (source_id);
CREATE INDEX IF NOT EXISTS idx_parser_samples_provider_id ON parser_samples (provider_id);
CREATE INDEX IF NOT EXISTS idx_parser_samples_is_corrected ON parser_samples (is_corrected);

-- `parser_profiles`: one row per versioned parser configuration for one
-- provider (`app.parser_tooling.ParserProfileStatus` -- DRAFT/TESTED/
-- SHADOW/CERTIFIED/ACTIVE/RETIRED, application-enforced by
-- `app.parser_tooling.validate_profile_transition`, the same one-step-
-- at-a-time convention as `app/phone_escalation.py`'s `CapabilityState`).
-- At most one ACTIVE row per `provider_id` at a time -- enforced
-- atomically by `SignalStore.promote_parser_profile` (demotes the
-- previous ACTIVE row to RETIRED in the same transaction). A RETIRED
-- row's data is never deleted or overwritten -- its `accuracy_metrics`/
-- `sample_count`/`test_count` stay exactly as recorded, so its past
-- behavior stays fully inspectable. `fallback_model` is an honest
-- placeholder (`'none'` unless a real one is wired) -- no LLM fallback
-- call is implemented by this track (see app/parser_tooling.py's
-- docstring, same scoping as Track 13's `SignalExtractor` stub).
CREATE TABLE IF NOT EXISTS parser_profiles (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    sample_count INTEGER NOT NULL DEFAULT 0,
    test_count INTEGER NOT NULL DEFAULT 0,
    accuracy_metrics TEXT NOT NULL DEFAULT '{}',
    supported_message_types TEXT NOT NULL DEFAULT '[]',
    fallback_model TEXT NOT NULL DEFAULT 'none',
    prompt_version TEXT,
    schema_version TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    activated_at TEXT,
    retired_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_parser_profiles_provider_id ON parser_profiles (provider_id);
CREATE INDEX IF NOT EXISTS idx_parser_profiles_status ON parser_profiles (status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_parser_profiles_provider_active
    ON parser_profiles (provider_id)
    WHERE status = 'active';

-- Track 24: `source_observations` -- the transport-agnostic adapter-
-- boundary record (see app/sources/adapter_contract.py's own module
-- docstring for the full design). Generalizes app/notification_bridge.py's
-- `notification_bridge_events` shape (`content_completeness`/
-- `content_hash`/`revision_seq`) from one specific transport (Android
-- notification capture) into a shape any `probe`/`fetch`/`poll`/
-- `normalize` adapter can write to, regardless of backend (RSS today;
-- any future adapter built against the same contract later).
--
-- `connection_id`/`provider_id`/`source_id` are application-enforced
-- references into `connections`/`providers`/`sources` (same "no real
-- DB-level FOREIGN KEY, caller validates existence" convention as every
-- other table in this schema -- see `sources`' own comment above) --
-- `provider_id`/`source_id` are nullable because a `fetch`/`poll` call
-- can genuinely happen against a `connection` before it has been wired
-- to a registered `sources` row (e.g. a one-off `probe`/`fetch` during
-- setup) -- never fabricated to a placeholder id.
--
-- `observation_kind` is 'created'|'edited'|'deleted'|'retrieved' (an
-- adapter's own report of what KIND of observation this is -- most RSS
-- entries are 'retrieved', since a public feed gives no reliable way to
-- distinguish a genuinely new item from a republished/edited one beyond
-- `revision_identifier`/`content_hash` changing).
--
-- `completeness` reuses app.notification_bridge.ContentCompleteness's
-- exact five states verbatim (COMPLETE/PARTIAL/POINTER_ONLY/TRUNCATED/
-- UNKNOWN) -- never a new vocabulary invented for this table, per the
-- Track 24 brief's own instruction. `content_hash`/`revision_seq`
-- mirror `notification_bridge_events`' own dedup/revision columns
-- field-for-field.
--
-- `purpose` ('research'|'backfill'|'signal_candidate') and
-- `eligibility_state` are this table's OWN disposition gate -- an
-- observation becomes eligible to ever become a real `Signal` ONLY when
-- an adapter explicitly classifies it `signal_candidate` AND the
-- `sources` row that produced it was explicitly operator-configured for
-- that (see `app.sources.rss_source.RssSourceAdapter`'s own docstring).
-- `rejection_reason` is nullable and populated ONLY when `eligibility_
-- state` is a rejected state -- never fabricated for an eligible row.
CREATE TABLE IF NOT EXISTS source_observations (
    id TEXT PRIMARY KEY,
    connection_id TEXT,
    provider_id TEXT,
    source_id TEXT,
    platform TEXT NOT NULL,
    source_namespace TEXT,
    original_item_id TEXT NOT NULL,
    canonical_url TEXT,
    revision_identifier TEXT,
    observation_kind TEXT NOT NULL,
    content_hash TEXT,
    revision_seq INTEGER NOT NULL DEFAULT 1,
    source_authored_at TEXT,
    source_updated_at TEXT,
    first_observed_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    timestamp_origin TEXT,
    timestamp_uncertain INTEGER NOT NULL DEFAULT 0,
    completeness TEXT NOT NULL,
    extracted_text TEXT,
    attachment_refs TEXT NOT NULL DEFAULT '[]',
    adapter_name TEXT NOT NULL,
    backend TEXT,
    parser_version TEXT,
    retrieval_method TEXT,
    correlation_id TEXT,
    acquisition_run_id TEXT,
    purpose TEXT NOT NULL DEFAULT 'research',
    eligibility_state TEXT NOT NULL DEFAULT 'not_eligible',
    rejection_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_observations_source_id ON source_observations (source_id, retrieved_at);
CREATE INDEX IF NOT EXISTS idx_source_observations_connection_id ON source_observations (connection_id);
CREATE INDEX IF NOT EXISTS idx_source_observations_eligibility ON source_observations (eligibility_state);

CREATE INDEX IF NOT EXISTS idx_orders_executed_at ON orders (executed_at);
CREATE INDEX IF NOT EXISTS idx_orders_account_id ON orders (account_id);
CREATE INDEX IF NOT EXISTS idx_orders_signal_id ON orders (signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS idx_signals_received_at ON signals (received_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_export_events_undelivered ON export_events (source_stream, export_sequence) WHERE delivered_at IS NULL;
CREATE TABLE IF NOT EXISTS daily_pnl (
    id INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    date DATE NOT NULL,
    opening_equity DECIMAL(18, 8),
    closing_equity DECIMAL(18, 8),
    realized_pnl DECIMAL(18, 8),
    unrealized_pnl DECIMAL(18, 8),
    fees DECIMAL(18, 8),
    slippage DECIMAL(18, 8),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (account_id, date)
);
CREATE TABLE IF NOT EXISTS margin_call_alerts (
    id INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    alert_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    current_equity DECIMAL(18, 8) NOT NULL,
    maintenance_requirement DECIMAL(18, 8) NOT NULL,
    excess_margin DECIMAL(18, 8) NOT NULL,
    broker TEXT NOT NULL,
    resolved BOOLEAN DEFAULT 0 NOT NULL,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_daily_pnl_account_id ON daily_pnl(account_id);
CREATE INDEX IF NOT EXISTS ix_margin_call_alerts_account_id ON margin_call_alerts(account_id);
-- F-08: heartbeat table for disk write capability and health checks
CREATE TABLE IF NOT EXISTS health_heartbeat (
    id INTEGER PRIMARY KEY,
    last_write TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    account_id TEXT,
    message TEXT NOT NULL,
    payload TEXT,
    acknowledged_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alerts_account_id ON alerts(account_id);
CREATE INDEX IF NOT EXISTS ix_alerts_unacknowledged ON alerts(acknowledged_at) WHERE acknowledged_at IS NULL;
"""


#: Additive migrations for columns added after a table already existed —
#: `CREATE TABLE IF NOT EXISTS` above only helps a brand-new database.
#: Each entry is applied with ALTER TABLE, ignoring the "duplicate column"
#: error SQLite raises when it's already there (no IF NOT EXISTS support
#: for columns before SQLite 3.35, and this stays compatible with older
#: builds rather than assuming a version).
_COLUMN_MIGRATIONS = [
    ("capital_reservations", "strategy_key", "TEXT"),
    ("config_routing_rules", "delivery_mode", "TEXT NOT NULL DEFAULT 'single'"),
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
    # Track 12 (cross-transport correlation): the discrete part of
    # app.signal_correlation.fingerprint_key for this signal, computed
    # and stored by save_signal ONLY when there's real provider identity
    # (channel_id) to correlate from -- NULL for every signal this
    # doesn't apply to (an honest "not eligible", never a fabricated
    # key). See app/signal_correlation.py's own module docstring and
    # SignalStore.find_correlation_candidates.
    ("signals", "correlation_fingerprint", "TEXT"),
    # Track 12 (per-event completeness escalation, app/notification_
    # bridge.py's ContentCompleteness): see notification_bridge_events's
    # own CREATE TABLE comment above for the read/write contract these
    # serve (SignalStore.list_notification_bridge_events_needing_
    # escalation / resolve_notification_bridge_event_escalation).
    ("notification_bridge_events", "needs_escalation", "INTEGER NOT NULL DEFAULT 0"),
    ("notification_bridge_events", "escalation_status", "TEXT"),
    # Track 16 (canonical signal lifecycle -- the user's own spec: "Store:
    # source created time, source modified time, first observed time,
    # received time, parsed time, decision time"). `received_at` already
    # existed (Track 1); these five are the rest, each NULL/honestly
    # absent for every signal (this codebase's own adapters included)
    # that doesn't populate it -- never guessed/backfilled from
    # received_at. See app/models.py's Signal docstrings for exactly what
    # each one means and app/signal_freshness.py's own module docstring
    # for why freshness math must never reduce to received_at alone.
    ("signals", "source_created_at", "TEXT"),
    ("signals", "source_modified_at", "TEXT"),
    ("signals", "first_observed_at", "TEXT"),
    ("signals", "parsed_at", "TEXT"),
    ("signals", "decision_at", "TEXT"),
    # Track 16 (freshness config, generalizing Track 14's reserved-but-
    # unwired providers.max_entry_age_seconds/stale_exit_policy/
    # correlation_window_seconds -- see app/signal_freshness.py's module
    # docstring): the remaining fields the user's own spec named that
    # Track 14 didn't already reserve a column for.
    ("providers", "max_add_age_seconds", "INTEGER"),
    ("providers", "adjustment_stale_behavior", "TEXT"),
    ("providers", "timestamp_source_preference", "TEXT"),
    ("providers", "clock_skew_tolerance_seconds", "INTEGER"),
    ("providers", "recovered_event_behavior", "TEXT"),
    # Track 16 (conflict-resolution policy -- see
    # app/signal_correlation.py's own ConflictResolutionPolicy docstring).
    # Default 'HOLD': the conservative floor, and Track 12's own original
    # (and still current, for any provider that never sets this) behavior
    # -- adding this column can never change what an existing/unconfigured
    # provider's conflicting signals do.
    ("providers", "conflict_resolution_policy", "TEXT NOT NULL DEFAULT 'HOLD'"),
    # Track 16: the explicit, operator-set source id PROVIDER_DETERMINISTIC
    # resolves conflicts by trusting -- see ConflictResolutionPolicy's own
    # docstring for why this is a structural comparison, never a runtime
    # judgment call.
    ("providers", "deterministic_primary_source_id", "TEXT"),
    # Track 16: the correlation TOLERANCE fields the user's own spec named
    # ("entry tolerance") that Track 14 didn't already reserve a column
    # for -- correlation_window_seconds (timestamp tolerance) already
    # existed. See app/signal_correlation.py's module docstring for where
    # this overrides the global SIGNAL_CORRELATION_PRICE_TOLERANCE_PCT
    # default, per provider.
    ("providers", "correlation_price_tolerance_pct", "REAL"),
    # Track 20 (mobile devices as first-class infrastructure -- see
    # app.notification_bridge.NotificationBridgeDevice's own docstring for
    # the full contract each of these follows: nullable/empty-default,
    # populated ONLY when the Android app itself reports it, never
    # fabricated/guessed/defaulted server-side).
    ("notification_bridge_devices", "device_name", "TEXT"),
    ("notification_bridge_devices", "platform", "TEXT"),
    ("notification_bridge_devices", "model", "TEXT"),
    ("notification_bridge_devices", "os_version", "TEXT"),
    ("notification_bridge_devices", "agent_version", "TEXT"),
    ("notification_bridge_devices", "network_status", "TEXT"),
    ("notification_bridge_devices", "battery_level", "INTEGER"),
    ("notification_bridge_devices", "is_charging", "INTEGER"),
    ("notification_bridge_devices", "notification_permission_granted", "INTEGER"),
    ("notification_bridge_devices", "accessibility_permission_granted", "INTEGER"),
    ("notification_bridge_devices", "screen_control_capability", "INTEGER"),
    ("notification_bridge_devices", "ai_agent_capability", "INTEGER"),
    ("notification_bridge_devices", "allowed_apps", "TEXT NOT NULL DEFAULT '[]'"),
    ("notification_bridge_devices", "blocked_apps", "TEXT NOT NULL DEFAULT '[]'"),
    # Track 24 -- see `sources`' own CREATE TABLE comment above.
    ("sources", "acquisition_checkpoint", "TEXT"),
    # Track 42 -- see `export_events`' own CREATE TABLE comment above.
    ("export_events", "terminal_park_reason", "TEXT"),
    ("export_events", "terminal_parked_at", "TEXT"),
    # E04 (bounded) / E10: fee tracking for financial correctness -- see
    # the `orders` table's own SCHEMA comment above. All three columns
    # report the broker's own values directly, never fabricated. NULL when
    # the broker doesn't report them (some brokers don't charge fees, some
    # don't expose them, some don't compute slippage).
    ("orders", "fee", "DECIMAL(18, 8)"),
    ("orders", "fee_currency", "TEXT"),
    ("orders", "slippage", "DECIMAL(18, 8)"),
    # E-11/B-10: multi-currency support -- account base currency and
    # per-order price currency. See app/models.py's DestinationAccount and
    # OrderResult docstrings for full semantics. NULL for pre-existing
    # rows; new rows should populate these honestly (never fabricated from
    # symbol syntax). config_accounts.currency: ISO 4217 code for this
    # account's base currency (e.g., 'USD', 'EUR'). orders.price_currency:
    # ISO 4217 code for the currency in which filled_price is quoted on
    # this specific order.
    ("config_accounts", "currency", "TEXT"),
    ("orders", "price_currency", "TEXT"),
    # E04 (bounded): daily loss limit and minimum equity threshold for risk control
    # F-04 fix: these were in SCHEMA but not in _COLUMN_MIGRATIONS, causing
    # fresh DBs to have them but upgraded DBs (bootstrapped pre-0036) to lack them.
    ("config_accounts", "daily_loss_limit_percent", "DECIMAL(5, 2)"),
    ("config_accounts", "min_equity_threshold", "DECIMAL(18, 8)"),
    # B-11: maximum gross leverage ceiling
    ("config_accounts", "max_gross_leverage", "DECIMAL(5, 2)"),
    # WP-08 (A-01): signal intent derivation -- see Intent enum and
    # Signal.intent's own docstring for why intent is distinct from side.
    ("signals", "intent", "TEXT"),
    # WP-08 (A-01): reduce-fraction for partial position reductions.
    ("signals", "reduce_fraction", "REAL"),
    # WP-08 (B-14): per-account short-selling permission gate -- see
    # DestinationAccount.allow_short's own docstring.
    ("config_accounts", "allow_short", "INTEGER NOT NULL DEFAULT 0"),
]


#: Track 24: `source_observations.observation_kind`'s closed vocabulary --
#: see that table's own CREATE TABLE comment.
_SOURCE_OBSERVATION_KINDS = ("created", "edited", "deleted", "retrieved")

#: Track 24: `source_observations.purpose`'s closed vocabulary -- see
#: that table's own CREATE TABLE comment and
#: app/sources/rss_source.py's module docstring for how this gates
#: whether an observation may ever become a real `Signal`.
_SOURCE_OBSERVATION_PURPOSES = ("research", "backfill", "signal_candidate")


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
            # Same "must run after the migration loop" reasoning as the
            # index just above -- see this column's own _COLUMN_MIGRATIONS
            # comment.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_signals_correlation_fingerprint "
                "ON signals (correlation_fingerprint)"
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
        change adds as a proper revision.

        F-04 fix: Re-stamp to head whenever version_num != alembic_code_head()
        to handle schema drift from deployed/upgraded DBs where bootstrap
        applied more migrations than the database is currently stamped at.
        """
        with self._connect() as conn:
            already_tracked = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            ).fetchone()

        if not already_tracked:
            # Fresh database: stamp to head
            command.stamp(_alembic_config(self.db_path), "head")
            return

        # Database already has alembic_version table: check if it matches code head
        current_version = self.schema_version()
        code_head = alembic_code_head()

        if current_version != code_head:
            # F-04: drift detected; re-stamp to current code head since bootstrap
            # already applied all the migrations
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

    def database_write_ok(self) -> bool:
        """F-08: Test write capability using a cheap heartbeat probe.
        Returns True if we can write to the database, False if the disk
        is full, read-only, or otherwise unable to accept writes."""
        try:
            with self._connect() as conn:
                # INSERT OR REPLACE into the heartbeat row with current timestamp
                conn.execute(
                    "INSERT OR REPLACE INTO health_heartbeat (id, last_write) VALUES (1, CURRENT_TIMESTAMP)"
                )
            return True
        except Exception:  # noqa: BLE001 - health check must not raise
            return False

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
        # Track 12 (cross-transport correlation): computed and stored
        # ONLY when this signal has real provider identity to correlate
        # from -- see this column's own _COLUMN_MIGRATIONS comment and
        # app/signal_correlation.py's own module docstring. `None` for
        # every signal this doesn't apply to, never a fabricated key.
        correlation_fingerprint = None
        if signal.channel_id is not None:
            from app.signal_correlation import fingerprint_key

            correlation_fingerprint = fingerprint_key(signal)

        # WP-44: serialize contract specs into raw["contract_spec"] for
        # persistence (these specs are runtime-only fields on Signal but need
        # to be recoverable for UI interpretation, so serialize them into raw).
        raw_data = signal.raw.copy()
        contract_spec = {}
        if signal.option is not None:
            contract_spec["option"] = {
                "underlying": signal.option.underlying,
                "strike": signal.option.strike,
                "right": signal.option.right,
                "expiry": signal.option.expiry,
                "multiplier": signal.option.multiplier,
            }
        if signal.future is not None:
            contract_spec["future"] = {
                "contract": signal.future.contract,
                "expiry": signal.future.expiry,
                "multiplier": signal.future.multiplier,
            }
        if signal.fx is not None:
            contract_spec["fx"] = {
                "base_currency": signal.fx.base_currency,
                "quote_currency": signal.fx.quote_currency,
                "lot_size": signal.fx.lot_size,
            }
        if signal.crypto_derivative is not None:
            contract_spec["crypto_derivative"] = {
                "underlying": signal.crypto_derivative.underlying,
                "leverage": signal.crypto_derivative.leverage,
            }
        if contract_spec:
            raw_data["contract_spec"] = contract_spec

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO signals
                   (id, source, symbol, side, asset_class, quantity, price, stop_loss, take_profit,
                    analyst, received_at, raw, import_batch, channel_id, message_id, revision_id,
                    correlation_fingerprint, source_created_at, source_modified_at, first_observed_at,
                    parsed_at, decision_at, intent, reduce_fraction)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    json.dumps(raw_data),
                    signal.import_batch,
                    signal.channel_id,
                    signal.message_id,
                    signal.revision_id,
                    correlation_fingerprint,
                    signal.source_created_at.isoformat() if signal.source_created_at else None,
                    signal.source_modified_at.isoformat() if signal.source_modified_at else None,
                    signal.first_observed_at.isoformat() if signal.first_observed_at else None,
                    signal.parsed_at.isoformat() if signal.parsed_at else None,
                    signal.decision_at.isoformat() if signal.decision_at else None,
                    signal.intent.value if signal.intent is not None else None,
                    signal.reduce_fraction,
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

    # -- Track 12: cross-transport signal correlation (app/signal_correlation.py) --

    def find_correlation_candidates(
        self, *, fingerprint_key: str, exclude_channel_id: str, since: datetime, until: datetime
    ) -> list[dict]:
        """Every already-recorded signal sharing this exact discrete
        fingerprint key, received within [`since`, `until`], from a
        DIFFERENT `channel_id` than the new signal's own -- i.e. real
        candidates for "the SAME underlying alert, arriving via a
        DIFFERENT transport" (same-channel matches are already handled,
        earlier and more strongly, by `find_signal_id_by_provider_
        identity`'s exact-identity dedup -- this is deliberately scoped
        to never re-cover that ground). Ordered earliest-first so the
        first real match is the natural "canonical" signal an evidence
        row attaches to. `exclude_channel_id` is required (not optional)
        -- see `app/engine.py`'s own call site for why this is only ever
        invoked for a signal that already has real channel_id/message_id
        identity."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, source, symbol, side, price, channel_id, message_id, received_at
                   FROM signals
                   WHERE correlation_fingerprint = ? AND channel_id IS NOT NULL AND channel_id != ?
                         AND received_at >= ? AND received_at <= ?
                   ORDER BY received_at ASC""",
                (fingerprint_key, exclude_channel_id, since.isoformat(), until.isoformat()),
            ).fetchall()
        return [
            {
                "id": r[0],
                "source": r[1],
                "symbol": r[2],
                "side": r[3],
                "price": r[4],
                "channel_id": r[5],
                "message_id": r[6],
                "received_at": r[7],
            }
            for r in rows
        ]

    def record_signal_correlation_evidence(
        self,
        *,
        canonical_signal_id: str,
        evidence_signal_id: str,
        fingerprint_key: str,
        source: str,
        channel_id: str | None,
        message_id: str | None,
        price: float | None,
        side: str | None,
        received_at: datetime,
        match_type: str,
    ) -> str:
        """Appends one evidence row -- `match_type` is `"corroborating"`
        or `"conflicting"` (see `signal_correlation_evidence`'s own
        CREATE TABLE comment); this store never validates that string
        against an enum itself (the caller, app/engine.py, always passes
        a `CorrelationOutcome.value`), same "vocabulary owned by the
        calling module" convention as `save_notification_bridge_event`'s
        own `classification` parameter."""
        evidence_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO signal_correlation_evidence
                       (id, canonical_signal_id, evidence_signal_id, fingerprint_key, source, channel_id,
                        message_id, price, side, received_at, match_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence_id,
                    canonical_signal_id,
                    evidence_signal_id,
                    fingerprint_key,
                    source,
                    channel_id,
                    message_id,
                    price,
                    side,
                    received_at.isoformat(),
                    match_type,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return evidence_id

    def list_signal_correlation_evidence(self, canonical_signal_id: str) -> list[dict]:
        """Every corroborating/conflicting evidence row recorded against
        one canonical signal id -- an audit read, e.g. "which other
        transports also reported this exact trade"."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, canonical_signal_id, evidence_signal_id, fingerprint_key, source, channel_id,
                          message_id, price, side, received_at, match_type, created_at
                   FROM signal_correlation_evidence WHERE canonical_signal_id = ? ORDER BY created_at ASC""",
                (canonical_signal_id,),
            ).fetchall()
        return [self._signal_correlation_evidence_row_to_dict(r) for r in rows]

    def list_conflicting_signal_correlations(self) -> list[dict]:
        """Every recorded `CONFLICTING_SOURCE_DATA` evidence row, across
        every canonical signal -- the review-facing read for "two
        transports disagreed on the same trade and neither was silently
        preferred" (see app/engine.py's `_handle_signal` and this
        table's own CREATE TABLE comment). The conflicting signal itself
        (never engine.handle_signal'd) is recorded separately via
        `save_signal` under an `import_batch` of
        `"cross_transport_conflict:{canonical_signal_id}"` -- fetch it
        with `get_signal_raw`/a direct read of the `signals` table by
        this row's own `evidence_signal_id` for its full parsed content."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, canonical_signal_id, evidence_signal_id, fingerprint_key, source, channel_id,
                          message_id, price, side, received_at, match_type, created_at
                   FROM signal_correlation_evidence WHERE match_type = 'conflicting' ORDER BY created_at DESC"""
            ).fetchall()
        return [self._signal_correlation_evidence_row_to_dict(r) for r in rows]

    @staticmethod
    def _signal_correlation_evidence_row_to_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "canonical_signal_id": row[1],
            "evidence_signal_id": row[2],
            "fingerprint_key": row[3],
            "source": row[4],
            "channel_id": row[5],
            "message_id": row[6],
            "price": row[7],
            "side": row[8],
            "received_at": row[9],
            "match_type": row[10],
            "created_at": row[11],
        }

    # --- Track 16: transport dedup vs. semantic correlation -- kept as    -
    # two DISTINCT read paths, never conflated (the user's own spec: "Same
    # Telegram update delivered twice = transport duplicate. Same provider
    # trade delivered by Telegram + Whop + email = semantic duplicate.
    # Never conflate them -- two distinct pages/metrics/read paths.") ----

    def list_transport_duplicate_groups(self) -> list[dict]:
        """TRANSPORT-level duplication/revision groups -- see this
        method's own note on what is and isn't observable given
        `find_signal_id_by_provider_identity`'s existing collapse-onto-
        one-row dedup: an EXACT (channel_id, message_id, revision_id)
        redelivery is, by design, never persisted twice (`save_signal`'s
        `INSERT OR REPLACE` reuses the same signal id for it) -- there is
        no separate row a listing here could show for that specific case,
        and inventing a redelivery counter nothing else in this codebase
        tracks would not be honest. What IS observable, and IS still a
        transport-level (never cross-transport/semantic) fact, is a
        (channel_id, message_id) identity seen under more than one
        `revision_id` -- i.e. the SAME message, on the SAME transport,
        edited more than once. Grouped and returned only where more than
        one revision exists (a single-revision message is not a
        duplicate of anything). Never conflated with
        `list_semantic_correlations`/`list_conflicting_signal_
        correlations` (a DIFFERENT channel_id) -- see this module's own
        section docstring."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT channel_id, message_id, id, revision_id, received_at, symbol, side, source
                   FROM signals WHERE channel_id IS NOT NULL AND message_id IS NOT NULL
                   ORDER BY channel_id, message_id, received_at ASC"""
            ).fetchall()
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            key = (r[0], r[1])
            groups.setdefault(key, []).append(
                {
                    "signal_id": r[2],
                    "revision_id": r[3],
                    "received_at": r[4],
                    "symbol": r[5],
                    "side": r[6],
                    "source": r[7],
                }
            )
        return [
            {"channel_id": k[0], "message_id": k[1], "revision_count": len(v), "revisions": v}
            for k, v in groups.items()
            if len(v) > 1
        ]

    def list_semantic_correlations(self) -> list[dict]:
        """Cross-transport SEMANTIC correlation -- Track 12's `signal_
        correlation_evidence` table, BOTH corroborating and conflicting
        rows, grouped by `canonical_signal_id`. Distinct from
        `list_transport_duplicate_groups` (same-transport redelivery/
        revision) -- never the same read path, per this module's own
        section docstring. This is the read path for the user's own spec
        display example ("Canonical Signal #18421 / Telegram 09:31:02 ✓ /
        Whop 09:31:04 ✓ / Email 09:31:09 ✓ / 3 observations / 1 canonical
        signal / 1 execution") -- `observations` below is `len(evidence) +
        1` (the canonical signal itself counts as the first observation).
        `execution_count` is how many of this canonical signal's own
        orders (via `list_orders_for_signal`) are non-REJECTED/non-ERROR
        -- a real, derived count, never fabricated."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, canonical_signal_id, evidence_signal_id, fingerprint_key, source, channel_id,
                          message_id, price, side, received_at, match_type, created_at
                   FROM signal_correlation_evidence ORDER BY canonical_signal_id, created_at ASC"""
            ).fetchall()
        groups: dict[str, list[dict]] = {}
        for r in rows:
            d = self._signal_correlation_evidence_row_to_dict(r)
            groups.setdefault(d["canonical_signal_id"], []).append(d)
        result = []
        for canonical_id, evidence in groups.items():
            orders = self.list_orders_for_signal(canonical_id)
            execution_count = sum(1 for o in orders if o["status"] in ("filled", "pending"))
            result.append(
                {
                    "canonical_signal_id": canonical_id,
                    "observations": len(evidence) + 1,
                    "evidence": evidence,
                    "has_conflict": any(e["match_type"] == "conflicting" for e in evidence),
                    "execution_count": execution_count,
                }
            )
        return result

    def get_primary_source_native_id(self, provider_id: str) -> tuple[str | None, int]:
        """`(native_id, count)` of this provider's PRIMARY-role `sources`
        rows -- used by `app.signal_correlation.ConflictResolutionPolicy.
        REQUIRE_PRIMARY_SOURCE` (see that enum's own docstring).
        `native_id` is only ever non-`None` when `count == 1` -- an
        ambiguous 0-or-many-PRIMARY provider must never be silently
        resolved to "the first one found"."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source_native_id FROM sources WHERE provider_id = ? AND role = 'PRIMARY' AND enabled = 1",
                (provider_id,),
            ).fetchall()
        if len(rows) != 1:
            return None, len(rows)
        return rows[0][0], 1

    def get_signal(self, signal_id: str) -> dict | None:
        """The full `signals` row (every column, including Track 16's
        timeline fields) for one signal id -- distinct from
        `get_signal_raw` (which returns only the `raw` JSON blob).
        `None` when no such signal is recorded."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, source, symbol, side, asset_class, quantity, price, stop_loss, take_profit,
                          analyst, received_at, import_batch, channel_id, message_id, revision_id,
                          correlation_fingerprint, source_created_at, source_modified_at, first_observed_at,
                          parsed_at, decision_at, intent, reduce_fraction
                   FROM signals WHERE id = ?""",
                (signal_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "source": row[1],
            "symbol": row[2],
            "side": row[3],
            "asset_class": row[4],
            "quantity": row[5],
            "price": row[6],
            "stop_loss": row[7],
            "take_profit": row[8],
            "analyst": row[9],
            "received_at": row[10],
            "import_batch": row[11],
            "channel_id": row[12],
            "message_id": row[13],
            "revision_id": row[14],
            "correlation_fingerprint": row[15],
            "source_created_at": row[16],
            "source_modified_at": row[17],
            "first_observed_at": row[18],
            "parsed_at": row[19],
            "decision_at": row[20],
            "intent": row[21],
            "reduce_fraction": row[22],
        }

    def get_signal_lifecycle(self, signal_id: str) -> dict | None:
        """The canonical signal lifecycle timeline (the user's own spec:
        "Every canonical signal needs a timeline: received, parsed,
        correlated, validation passed, risk approved, order submitted,
        accepted, filled, [later] target update received, position
        adjusted") ASSEMBLED AT READ TIME from data this codebase already
        persists elsewhere -- no new event-log table (see this task's
        own final report for why: every step below already has a real,
        queryable source of truth, so a separate log would only be able
        to drift from it).

        Sources, per step:
        - received/parsed/decision: `signals.received_at`/`parsed_at`/
          `decision_at` (Track 16's own new timeline columns).
        - correlated: the earliest `signal_correlation_evidence` row this
          signal appears in, either as the canonical signal or as
          evidence for one (Track 12).
        - validation_passed / risk_approved: this codebase's route-
          qualification gate (`_check_route_qualified`) and capital-
          admission check (`_try_reserve_capital`) both run SYNCHRONOUSLY,
          immediately before `broker.place_order`, with no separately
          timestamped fact of their own -- inferred (`"inferred": True`)
          from the existence of a non-REJECTED-for-that-reason order row,
          sharing that order's own `submitted_at`, never a fabricated
          independent timestamp. Omitted entirely for a signal with no
          such order (nothing to infer it from).
        - order_submitted / order_accepted / order_filled / rejected: one
          entry per `orders` row for this signal id (`submitted_at`,
          `broker_order_id` presence, `status`/`executed_at`).
        - position_adjusted: every OTHER order row whose `family_id`
          equals this signal id (a later close/adjustment belonging to
          the position THIS signal opened) -- `app/engine.py`'s own
          DB-0X `family_id` convention.

        Returns `None` when this signal id was never recorded at all."""
        signal = self.get_signal(signal_id)
        if signal is None:
            return None

        events: list[dict] = []

        def add(kind: str, at: str | None, **detail: Any) -> None:
            if at is None:
                return
            events.append({"kind": kind, "at": at, **detail})

        add("received", signal["received_at"])
        add("parsed", signal["parsed_at"])
        add("decision", signal["decision_at"])
        add("source_created", signal["source_created_at"])
        add("source_modified", signal["source_modified_at"])
        add("first_observed", signal["first_observed_at"])

        with self._connect() as conn:
            corr_row = conn.execute(
                """SELECT created_at, canonical_signal_id, evidence_signal_id, match_type FROM
                   signal_correlation_evidence WHERE canonical_signal_id = ? OR evidence_signal_id = ?
                   ORDER BY created_at ASC LIMIT 1""",
                (signal_id, signal_id),
            ).fetchone()
        if corr_row is not None:
            add(
                "correlated",
                corr_row[0],
                canonical_signal_id=corr_row[1],
                evidence_signal_id=corr_row[2],
                match_type=corr_row[3],
            )

        for order in self.list_orders_for_signal(signal_id):
            submitted_at = order.get("executed_at")
            if order["status"] == "rejected":
                add("rejected", submitted_at, order_id=order["id"], message=order.get("message"))
                continue
            if order["status"] == "error":
                add("error", submitted_at, order_id=order["id"], message=order.get("message"))
                continue
            add(
                "validation_passed",
                submitted_at,
                order_id=order["id"],
                inferred=True,
            )
            add(
                "risk_approved",
                submitted_at,
                order_id=order["id"],
                inferred=True,
            )
            add("order_submitted", submitted_at, order_id=order["id"], account_id=order["account_id"])
            if order.get("broker_order_id"):
                add("order_accepted", submitted_at, order_id=order["id"], broker_order_id=order["broker_order_id"])
            if order["status"] == "filled":
                add(
                    "order_filled",
                    submitted_at,
                    order_id=order["id"],
                    filled_quantity=order.get("filled_quantity"),
                    filled_price=order.get("filled_price"),
                )

        with self._connect() as conn:
            family_rows = conn.execute(
                """SELECT id, status, executed_at, message, purpose, filled_quantity, account_id
                   FROM orders WHERE family_id = ? AND signal_id != ? ORDER BY executed_at ASC""",
                (signal_id, signal_id),
            ).fetchall()
        for r in family_rows:
            add(
                "position_adjusted",
                r[2],
                order_id=r[0],
                status=r[1],
                message=r[3],
                purpose=r[4],
                filled_quantity=r[5],
                account_id=r[6],
            )

        events.sort(key=lambda e: e["at"])
        return {"signal_id": signal_id, "signal": signal, "events": events}

    def _provider_signed_deltas(
        self, conn: sqlite3.Connection, symbol: str, *, account_id: str | None = None, source: str | None = None
    ) -> list[tuple[str, str, float]]:
        """Track 18: the ONE shared computation behind both
        `get_position_provider_allocations` (Track 16's read-only
        visibility endpoint) and `get_provider_position_ownership` (Track
        18's live enforcement gate) -- every `orders` row's `applied_
        execution_delta`, signed by `side` (BUY positive, SELL negative;
        this is AUD-01's distinct-field quantity model, the ONLY quantity
        this codebase ever applies to a tracked position -- see
        app/engine.py's own module docstring), joined through `signals.
        source` to attribute it to a provider. Returns
        `(account_id, source, signed_delta)` tuples, one per matching
        order row -- callers aggregate as they need. Optionally scoped to
        one `account_id` and/or `source` so the narrow enforcement call
        doesn't have to fetch and filter every account's full history,
        while still running the EXACT SAME query shape as the visibility
        endpoint (never a second, independently-written computation that
        could drift out of sync with what that endpoint reports)."""
        query = (
            "SELECT o.account_id, s.source, o.side, o.applied_execution_delta "
            "FROM orders o JOIN signals s ON o.signal_id = s.id "
            "WHERE o.symbol = ? AND o.applied_execution_delta IS NOT NULL AND o.applied_execution_delta != 0"
        )
        params: list[Any] = [symbol]
        if account_id is not None:
            query += " AND o.account_id = ?"
            params.append(account_id)
        if source is not None:
            query += " AND s.source = ?"
            params.append(source)
        rows = conn.execute(query, params).fetchall()
        return [(row[0], row[1], (row[3] if row[2] == "buy" else -row[3])) for row in rows]

    def get_position_provider_allocations(self, symbol: str) -> dict:
        """READ-ONLY visibility: broker-level (per-account) tracked
        position for `symbol` vs. each provider's own best-effort
        ATTRIBUTABLE contribution to it, derived from this service's
        existing AUD-01 distinct-field quantity model (`orders.
        applied_execution_delta`, signed by `side`, joined to `signals.
        source` -- the ONLY quantity this codebase ever applies to a
        tracked position; see app/engine.py's own module docstring
        section on it -- see `_provider_signed_deltas` for the shared
        computation). This read itself stays DERIVED/INFORMATIONAL ONLY,
        never consulted by any live-routing decision -- but Track 18 added
        a SEPARATE, narrower enforcement gate (`get_provider_position_
        ownership`, consulted by app/engine.py's close/exit resolution
        BEFORE submitting an order) that reuses this exact same
        computation rather than a second, parallel one. See that method's
        own docstring for what changed and app/engine.py's `_resolve_and_
        submit_plain_close`/`_handle_managed_close` for where it's used."""
        with self._connect() as conn:
            position_rows = conn.execute(
                "SELECT account_id, net_quantity, updated_at FROM positions WHERE symbol = ? AND net_quantity != 0",
                (symbol,),
            ).fetchall()
            contribution_rows = self._provider_signed_deltas(conn, symbol)
        by_account: dict[str, dict[str, float]] = {}
        for account_id, source, signed in contribution_rows:
            by_account.setdefault(account_id, {}).setdefault(source, 0.0)
            by_account[account_id][source] += signed
        accounts = []
        for account_id, total_quantity, updated_at in position_rows:
            provider_totals = by_account.get(account_id, {})
            accounts.append(
                {
                    "account_id": account_id,
                    "total_quantity": total_quantity,
                    "updated_at": updated_at,
                    "provider_allocations": [
                        {"provider": provider, "attributable_quantity": qty}
                        for provider, qty in provider_totals.items()
                    ],
                    "unattributed_quantity": total_quantity - sum(provider_totals.values()),
                }
            )
        return {"symbol": symbol, "accounts": accounts}

    def get_provider_position_ownership(self, account_id: str, symbol: str, source: str) -> tuple[float, float]:
        """Track 18's ENFORCEMENT source of truth for per-provider
        position ownership, consulted by app/engine.py's `_resolve_and_
        submit_plain_close`/`_handle_managed_close` BEFORE constructing or
        submitting a close/exit order -- reuses the exact same computation
        as `get_position_provider_allocations` (`_provider_signed_deltas`
        above), scoped to one (account_id, symbol, source) triple, rather
        than a second, independently-written query that could drift out
        of sync with what that visibility endpoint reports.

        Returns `(this_provider_quantity, total_attributed_quantity)`:

        - `this_provider_quantity`: this `source`'s own signed net
          contribution to this account/symbol's tracked position
          (positive = net long, negative = net short; 0.0 when this
          source has no attributable orders at all here).
        - `total_attributed_quantity`: the SAME signed sum across EVERY
          source that has ever contributed to this account/symbol --
          i.e. what `get_position_provider_allocations` would report as
          this account's total attributed quantity (attributed +
          unattributed, since this is unfiltered by source).

        The caller uses `total_attributed_quantity == 0` to distinguish
        two genuinely different situations: (a) real multi-provider
        attribution data exists, so a requesting source with 0 of it is a
        real ownership conflict (Track 16's finding) and must be
        rejected, vs. (b) NO order/signal attribution exists at all for
        this account/symbol -- e.g. a position reconciled or seeded
        outside this service's own tracked order pipeline (see
        `DestinationAccount.exclusive_writer_qualified` and
        `_reconcile_before_plain_close`'s "manual intervention, an
        external fill placed directly at the broker" cases) -- where
        there is no competing-provider data to gate against at all, and
        failing closed here would regress an already-supported,
        unrelated scenario rather than fix the multi-provider pooling gap
        this method exists for. See app/engine.py's call sites for
        exactly how each is handled."""
        with self._connect() as conn:
            rows = self._provider_signed_deltas(conn, symbol, account_id=account_id)
        this_provider = 0.0
        total = 0.0
        for _account_id, row_source, signed in rows:
            total += signed
            if row_source == source:
                this_provider += signed
        return this_provider, total

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

        `result.fee`/`result.fee_currency`/`result.slippage` (E10, fee
        tracking): pass directly from the OrderResult returned by the broker
        adapter. These are None when the broker doesn't report them -- never
        fabricated. Fee tracking feeds into daily_pnl aggregation for
        account economics and performance analytics.
        """
        stored_filled_quantity = applied_quantity if applied_quantity is not None else result.filled_quantity
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO orders
                   (account_id, broker, symbol, side, requested_quantity, signal_id, status,
                    broker_order_id, filled_quantity, filled_price, message, executed_at, reserved_notional,
                    submitted_at, protection_confirmed_at, purpose, family_id,
                    confirmed_cumulative_fill, applied_execution_delta, outstanding_possible_fill,
                    reserved_quantity, acknowledged_quantity, fee, fee_currency, slippage)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    result.fee,
                    result.fee_currency,
                    result.slippage,
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
        """Every export event not yet marked delivered AND not yet marked
        terminally parked, oldest first by (source_stream,
        export_sequence) -- what the relay worker (app/relay_worker.py)
        polls and forwards. Reconstructs the exact `EventEnvelope` that
        was appended (S6: "the relay never reconstructs or reinterprets
        it, only forwards these exact bytes"), never a freshly-built one
        from the row's own columns.

        Track 42: `terminal_park_reason IS NULL` excludes an event the
        relay worker has already classified as STRUCTURALLY parked (see
        `mark_export_events_terminally_parked`) -- redelivering the exact
        same bytes can never change that outcome (the commercial side's
        own idempotent dedup returns the already-stored, still-parked
        row without re-running any projection logic), so resending it
        forever would be pure noise, never progress. An event parked for
        a TRANSIENT reason (e.g. `fee_target_not_found`) is deliberately
        NOT excluded here -- it stays undelivered and is polled again
        next cycle, same as `unregistered_stream`/`integrity_error`
        already were before this track."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT envelope_json FROM export_events
                   WHERE delivered_at IS NULL AND terminal_park_reason IS NULL
                   ORDER BY source_stream, export_sequence LIMIT ?""",
                (limit,),
            ).fetchall()
        return [EventEnvelope.model_validate_json(row[0]) for row in rows]

    def list_export_events(
        self,
        *,
        limit: int = 50,
        source_stream: str | None = None,
        delivered: bool | None = None,
    ) -> list[dict]:
        """Track 33: the one real, live-queryable view of this outbox --
        same shape/convention as `list_recent_orders`/`list_recent_signals`
        (newest first, bounded `limit`, optional narrowing filter) so
        `GET /export-events` (app/main.py) can let an operator actually
        SEE what's in the outbox without a direct DB connection, rather
        than only `export_outbox_backlog`'s aggregate count. `delivered`
        narrows to delivered-only (`True`), undelivered-only (`False`), or
        both (`None`, the default) -- distinct from `list_undelivered_
        export_events` above, which is relay-worker-facing (returns real
        `EventEnvelope`s, oldest first, undelivered only) rather than
        operator-facing."""
        clauses: list[str] = []
        params: list[Any] = []
        if source_stream is not None:
            clauses.append("source_stream = ?")
            params.append(source_stream)
        if delivered is True:
            clauses.append("delivered_at IS NOT NULL")
        elif delivered is False:
            clauses.append("delivered_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT event_id, event_type, source_stream, export_sequence, envelope_json,
                           payload_hash, appended_at, delivered_at, terminal_park_reason, terminal_parked_at
                    FROM export_events {where}
                    ORDER BY appended_at DESC, export_sequence DESC LIMIT ?""",
                params,
            ).fetchall()
        return [self._export_event_row_to_dict(row) for row in rows]

    def get_export_event(self, event_id: str) -> dict | None:
        """One outbox event by its own idempotency key, or `None` if no
        such event was ever appended -- same "real `None`, never a raw
        `IndexError`/empty-tuple" convention as `get_source`/`get_connection`."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT event_id, event_type, source_stream, export_sequence, envelope_json,
                          payload_hash, appended_at, delivered_at, terminal_park_reason, terminal_parked_at
                   FROM export_events WHERE event_id = ?""",
                (event_id,),
            ).fetchone()
        return self._export_event_row_to_dict(row) if row else None

    def _export_event_row_to_dict(self, row: tuple) -> dict:
        envelope = EventEnvelope.model_validate_json(row[4])
        return {
            "event_id": row[0],
            "event_type": row[1],
            "source_stream": row[2],
            "export_sequence": row[3],
            "payload_hash": row[5],
            "appended_at": row[6],
            "delivered_at": row[7],
            # Track 42: set only when the relay worker classified this
            # event's own relay-reported "parked" status as STRUCTURAL
            # (see `mark_export_events_terminally_parked` and
            # app/relay_worker.py's `classify_parked_reason`) -- both
            # NULL for a never-parked event, a still-transiently-parked
            # one, or a genuinely delivered/applied one.
            "terminal_park_reason": row[8],
            "terminal_parked_at": row[9],
            # The owner is the same audience `GET /signals`/`GET /orders`
            # already hand full signal/order content to, and this payload
            # is the typed signal_platform_contracts payload (subject/
            # quantities/prices), never a raw credential or secret (those
            # live only as `credential_reference` env-var NAMES -- see
            # app/connections.py's own docstring) -- safe to return as-is,
            # same "forward these exact bytes, never reinterpret" rule
            # `list_undelivered_export_events` already follows.
            "payload": envelope.payload,
        }

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

    def mark_export_events_terminally_parked(self, event_ids_and_reasons: list[tuple[str, str]]) -> None:
        """Track 42: mark each `(event_id, reason)` pair as terminally
        parked -- a relay-reported `"parked"` status app/relay_worker.py's
        own `classify_parked_reason` decided is STRUCTURAL (can never
        resolve without a code change on the commercial side, e.g.
        `source_event_kind_not_ledger_representable`). Deliberately
        `delivered_at`-independent and never touches it: this event was
        never economically applied, so it must never read back as
        "delivered" to anything (`list_export_events`'s `delivered`
        filter, dashboards, etc.) -- see `export_events`' own CREATE
        TABLE comment for why this is its own column.

        Idempotent and non-destructive: an event already terminally
        parked (for the same or a different reason -- the relay's own
        classification of the SAME `parked_reason` string never changes
        between polls) is simply not matched again by the `WHERE
        terminal_park_reason IS NULL` guard, so the original
        `terminal_parked_at` timestamp is preserved rather than reset on
        every subsequent poll of an event this method has already
        excluded from `list_undelivered_export_events`."""
        if not event_ids_and_reasons:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                """UPDATE export_events SET terminal_park_reason = ?, terminal_parked_at = ?
                   WHERE event_id = ? AND terminal_park_reason IS NULL""",
                [(reason, now, event_id) for event_id, reason in event_ids_and_reasons],
            )

    def terminally_parked_export_event_count(self) -> int:
        """Track 42: a real, live count of export events the relay
        worker has classified as structurally, permanently parked (see
        `mark_export_events_terminally_parked`) -- surfaced on `GET
        /health` the same informational-only way INT-040's own
        `outbox_backlog_*` fields are (app/main.py): a nonzero count
        means a human should look at WHY (a `terminal_park_reason` this
        build genuinely cannot resolve on its own), never something
        this health check gates `status` on, same reasoning as
        `outbox_backlog_ok`'s own docstring there."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM export_events WHERE terminal_park_reason IS NOT NULL"
            ).fetchone()
        return row[0]

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
        self,
        reservation_id: str,
        account_id: str,
        notional: float,
        signal_id: str | None = None,
        strategy_key: str | None = None,
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
                "INSERT INTO capital_reservations "
                "(id, account_id, notional, signal_id, created_at, resolved_at, strategy_key) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (reservation_id, account_id, notional, signal_id, datetime.now(timezone.utc).isoformat(), strategy_key),
            )

    def resolve_one_capital_reservation(
        self, account_id: str, reservation_id: str | None, notional: float, signal_id: str | None = None
    ) -> None:
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
            row = None
            if signal_id is not None:
                # ALLOC-03: an exact (account, signal, notional) match first,
                # so a strategy-tagged reservation is never resolved by
                # another strategy's identically-sized one on that account.
                row = conn.execute(
                    "SELECT id FROM capital_reservations WHERE account_id = ? AND notional = ? AND signal_id = ? "
                    "AND resolved_at IS NULL LIMIT 1",
                    (account_id, notional, signal_id),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT id FROM capital_reservations WHERE account_id = ? AND notional = ? "
                    "AND resolved_at IS NULL LIMIT 1",
                    (account_id, notional),
                ).fetchone()
            if row is None:
                return
            conn.execute(
                "UPDATE capital_reservations SET resolved_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row[0]),
            )

    # ------------------------------------------------------------------
    # ALLOC-03: strategy budgets + joint, cross-process-safe admission
    # ------------------------------------------------------------------
    def set_strategy_budget(self, strategy_key: str, max_notional: float | None) -> None:
        if max_notional is not None and not (max_notional > 0):
            raise ValueError("max_notional must be a positive number or None")
        with self._immediate() as conn:
            conn.execute(
                "INSERT INTO strategy_budgets (strategy_key, max_notional, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(strategy_key) DO UPDATE SET max_notional = excluded.max_notional, "
                "updated_at = excluded.updated_at",
                (strategy_key, max_notional, datetime.now(timezone.utc).isoformat()),
            )

    def get_strategy_budget(self, strategy_key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT strategy_key, max_notional, updated_at FROM strategy_budgets WHERE strategy_key = ?",
                (strategy_key,),
            ).fetchone()
        return None if row is None else {"strategy_key": row[0], "max_notional": row[1], "updated_at": row[2]}

    def list_strategy_budgets(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT strategy_key, max_notional, updated_at FROM strategy_budgets ORDER BY strategy_key"
            ).fetchall()
        return [{"strategy_key": r[0], "max_notional": r[1], "updated_at": r[2]} for r in rows]

    def delete_strategy_budget(self, strategy_key: str) -> None:
        with self._immediate() as conn:
            conn.execute("DELETE FROM strategy_budgets WHERE strategy_key = ?", (strategy_key,))

    def sum_unresolved_strategy_reservations(self, strategy_key: str) -> float:
        """Outstanding reserved notional for one strategy ACROSS every
        account and every process sharing this database file."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(notional), 0) FROM capital_reservations "
                "WHERE strategy_key = ? AND resolved_at IS NULL",
                (strategy_key,),
            ).fetchone()
        return float(row[0])

    def reserve_strategy_checked(
        self,
        reservation_id: str,
        account_id: str,
        notional: float,
        *,
        signal_id: str | None,
        strategy_key: str,
        ceiling: float,
        confirmed_notional,
    ) -> tuple[bool, float, float]:
        """Atomically (one BEGIN IMMEDIATE transaction) check
        `confirmed + outstanding reservations + notional <= ceiling` for
        the strategy and, only if it fits, insert the reservation.
        `confirmed_notional` is a zero-arg callable evaluated AFTER the
        write lock is held, so no other process can commit a fill or a
        reservation between the read and the insert. No broker or network
        call may happen inside it. Returns (admitted, confirmed, pending).
        """
        with self._immediate() as conn:
            confirmed = float(confirmed_notional())
            pending = float(
                conn.execute(
                    "SELECT COALESCE(SUM(notional), 0) FROM capital_reservations "
                    "WHERE strategy_key = ? AND resolved_at IS NULL",
                    (strategy_key,),
                ).fetchone()[0]
            )
            if confirmed + pending + notional > ceiling:
                return False, confirmed, pending
            conn.execute(
                "INSERT INTO capital_reservations "
                "(id, account_id, notional, signal_id, created_at, resolved_at, strategy_key) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (
                    reservation_id,
                    account_id,
                    notional,
                    signal_id,
                    datetime.now(timezone.utc).isoformat(),
                    strategy_key,
                ),
            )
        return True, confirmed, pending

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
                          stop_loss, take_profit, raw, import_batch, intent, reduce_fraction
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
                # WP-44: signal interpretation fields for UI display.
                # `intent` is the derived/explicit trading intent from the signal.
                # `reduce_fraction` (0 < x ≤ 1) is the fraction of position to reduce
                # when intent is REDUCE. Contract specs are in raw["contract_spec"]
                # if present (see save_signal). asset_class_inferred is a boolean
                # in raw["asset_class_inferred"] indicating whether the asset class
                # was inferred from symbol shape rather than source-declared.
                "intent": r[13],
                "reduce_fraction": r[14],
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
                opened = self._command_ledger_row_to_entry(
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
                opened.newly_opened = True
                return opened
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

    def update_account_paper_order_id_sequence(self, account_id: str, sequence: int) -> None:
        """WP-38 (G-C-24): update the persistent paper order ID sequence for an
        account without changing other account fields. Called after a paper
        broker fill to persist the updated sequence."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE config_accounts SET paper_order_id_sequence = ? WHERE account_id = ?",
                (sequence, account_id),
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
                "SELECT id, source, destinations, symbol_filter, delivery_mode FROM config_routing_rules ORDER BY id"
            ).fetchall()
        return [
            {
                "id": r[0],
                "source": r[1],
                "destinations": json.loads(r[2]),
                "symbol_filter": json.loads(r[3]) if r[3] else None,
                "delivery_mode": r[4] or "single",
            }
            for r in rows
        ]

    def insert_config_routing_rule(
        self,
        source: str,
        destinations: list[str],
        symbol_filter: list[str] | None = None,
        delivery_mode: str = "single",
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO config_routing_rules (source, destinations, symbol_filter, delivery_mode) VALUES (?, ?, ?, ?)",
                (source, json.dumps(destinations), json.dumps(symbol_filter) if symbol_filter else None, delivery_mode),
            )
            assert cursor.lastrowid is not None  # see save_order_result's identical comment
            return cursor.lastrowid

    def update_config_routing_rule(
        self,
        rule_id: int,
        source: str,
        destinations: list[str],
        symbol_filter: list[str] | None = None,
        delivery_mode: str = "single",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE config_routing_rules SET source = ?, destinations = ?, symbol_filter = ?, delivery_mode = ? "
                "WHERE id = ?",
                (
                    source,
                    json.dumps(destinations),
                    json.dumps(symbol_filter) if symbol_filter else None,
                    delivery_mode,
                    rule_id,
                ),
            )

    def delete_config_routing_rule(self, rule_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM config_routing_rules WHERE id = ?", (rule_id,))

    # ------------------------------------------------------------------
    # ALLOC-01: allocation intents (one logical decision per opportunity)
    # ------------------------------------------------------------------
    @contextmanager
    def _immediate(self, *, attempts: int = 8, backoff: float = 0.05) -> Iterator[sqlite3.Connection]:
        """Short write transaction opened with BEGIN IMMEDIATE (SQLite
        allows one writer; a second concurrent writer gets SQLITE_BUSY).
        Contention is retried with bounded backoff; callers must keep the
        body short and must never make a broker/network call inside it."""
        import time

        conn = sqlite3.connect(self.db_path, timeout=3.0)  # v8 engineering default sqlite_busy_timeout_ms=3000
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            for attempt in range(attempts):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc) and "busy" not in str(exc):
                        raise
                    if attempt == attempts - 1:
                        raise
                    time.sleep(backoff * (2**attempt))
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _intent_row(row: sqlite3.Row | tuple | None) -> dict | None:
        if row is None:
            return None
        return {
            "intent_id": row[0],
            "signal_id": row[1],
            "strategy_key": row[2],
            "symbol": row[3],
            "side": row[4],
            "candidates": json.loads(row[5]),
            "selected_account_id": row[6],
            "state": row[7],
            "reason": row[8],
            "trace": json.loads(row[9]) if row[9] else None,
            "created_at": row[10],
            "updated_at": row[11],
        }

    _INTENT_COLS = (
        "intent_id, signal_id, strategy_key, symbol, side, candidates, selected_account_id, state, reason, "
        "trace, created_at, updated_at"
    )

    def get_allocation_intent(self, signal_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._INTENT_COLS} FROM allocation_intents WHERE signal_id = ?", (signal_id,)
            ).fetchone()
        return self._intent_row(row)

    def claim_allocation_intent(
        self, signal_id: str, *, strategy_key: str, symbol: str, side: str, candidates: list[str]
    ) -> dict:
        """Idempotent across threads and processes: the first claimant
        creates the row (state 'claimed'); every later claimant for the
        same signal gets that same row back, including whatever account
        it is already bound to."""
        import hashlib

        intent_id = hashlib.sha256(f"{strategy_key}|{signal_id}".encode()).hexdigest()[:32]
        now = datetime.now(timezone.utc).isoformat()
        with self._immediate() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO allocation_intents "
                "(intent_id, signal_id, strategy_key, symbol, side, candidates, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'claimed', ?, ?)",
                (intent_id, signal_id, strategy_key, symbol, side, json.dumps(candidates), now, now),
            )
            row = conn.execute(
                f"SELECT {self._INTENT_COLS} FROM allocation_intents WHERE signal_id = ?", (signal_id,)
            ).fetchone()
        intent = self._intent_row(row)
        assert intent is not None
        return intent

    def bind_allocation_intent(self, signal_id: str, account_id: str) -> bool:
        """Atomically bind the intent to ONE account. True when this call
        bound it or it was already bound to the same account; False when
        a different account already won or the intent is terminal
        (skipped). Never rebinds."""
        now = datetime.now(timezone.utc).isoformat()
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT selected_account_id, state FROM allocation_intents WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            if row is None:
                return False
            selected, state = row
            if selected is not None:
                return selected == account_id and state in ("selected", "committed")
            if state != "claimed":
                return False
            conn.execute(
                "UPDATE allocation_intents SET selected_account_id = ?, state = 'selected', updated_at = ? "
                "WHERE signal_id = ?",
                (account_id, now, signal_id),
            )
        return True

    def release_allocation_binding(self, signal_id: str, account_id: str) -> bool:
        """Undo a binding ONLY when no submission was attempted (state
        'selected'): used when the chosen account is rejected before it
        ever reaches a broker. A committed intent is never released."""
        now = datetime.now(timezone.utc).isoformat()
        with self._immediate() as conn:
            cur = conn.execute(
                "UPDATE allocation_intents SET selected_account_id = NULL, state = 'claimed', updated_at = ? "
                "WHERE signal_id = ? AND selected_account_id = ? AND state = 'selected'",
                (now, signal_id, account_id),
            )
        return cur.rowcount > 0

    def commit_allocation_intent(self, signal_id: str, account_id: str, *, reason: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._immediate() as conn:
            conn.execute(
                "UPDATE allocation_intents SET state = 'committed', reason = COALESCE(?, reason), updated_at = ? "
                "WHERE signal_id = ? AND selected_account_id = ? AND state IN ('selected', 'committed')",
                (reason, now, signal_id, account_id),
            )

    def skip_allocation_intent(self, signal_id: str, *, reason: str, trace: list[dict] | None = None) -> None:
        """Explained non-execution. Only a not-yet-bound intent can be
        skipped; an intent already bound/committed keeps its account."""
        now = datetime.now(timezone.utc).isoformat()
        with self._immediate() as conn:
            conn.execute(
                "UPDATE allocation_intents SET state = 'skipped', reason = ?, trace = ?, updated_at = ? "
                "WHERE signal_id = ? AND state = 'claimed'",
                (reason, json.dumps(trace) if trace is not None else None, now, signal_id),
            )

    def list_allocation_intents(self, *, state: str | None = None, limit: int = 200) -> list[dict]:
        query = f"SELECT {self._INTENT_COLS} FROM allocation_intents"
        params: list[Any] = []
        if state is not None:
            query += " WHERE state = ?"
            params.append(state)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [i for i in (self._intent_row(r) for r in rows) if i is not None]

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
        """Every order with an actual fill (status='filled' or filled_quantity > 0) across every account, oldest first, joined
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
                   WHERE o.status = 'filled' OR o.filled_quantity > 0
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
        4. TRK-27 (Finding 2): `route_key` already has qualification
           history recorded under a DIFFERENT `product_type` for this same
           (adapter_type, route_key, asset_class) -- e.g. "spot" already
           recorded, and this call asks to also record "perpetual" under
           the identical route_key. See `app/engine.py`'s
           `_UNDECLARED_ROUTE_PRODUCT_TYPE` comment for the gap this
           closes: the live-routing gate identifies a route purely by
           (adapter_type, route_key, asset_class) and always checks a
           single, fixed `product_type` ("default") -- it has no way to
           tell which of two real products sharing one route_key a given
           signal is actually for. Letting an operator record qualification
           history under two different product_types for the same
           route_key would silently let an unqualified product ride on a
           qualified account's route-qualification state (whichever
           product_type the live gate actually checks is the only one that
           was ever real protection; the other was never enforced at all).
           This codebase's own existing, supported convention is a
           DISTINCT route_key per product (see
           app/qualification.py's module docstring's ccxt_binance_spot /
           ccxt_binance_perp example) -- fails closed here rather than
           silently accepting a configuration this system has no way to
           honor correctly.

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
            existing_product_types = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT product_type FROM route_qualifications "
                    "WHERE adapter_type = ? AND route_key = ? AND asset_class = ?",
                    (adapter_type, route_key, asset_class),
                ).fetchall()
            }
            if existing_product_types and product_type not in existing_product_types:
                raise QualificationError(
                    f"route ({adapter_type}/{route_key}/{asset_class}) already has qualification "
                    f"history recorded under product_type(s) {sorted(existing_product_types)} -- "
                    f"refusing to also record a DIFFERENT product_type ('{product_type}') for the "
                    "SAME route_key: the live-routing gate (app/engine.py) identifies a route by "
                    "(adapter_type, route_key, asset_class) and always checks one fixed product_type, "
                    "so two product_types sharing one route_key can never be correctly distinguished "
                    "there -- whichever one isn't actually checked would silently ride on the other's "
                    "qualification state. Use a distinct route_key per product instead (see "
                    "app/qualification.py's module docstring's ccxt_binance_spot/ccxt_binance_perp "
                    "convention)."
                )

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

    # -- Track 8: the UNIFIED collector registry (app/unified_collectors.py) --
    # Generic CRUD shared by the telegram/pull/email/website registries
    # below -- see app/unified_collectors.py's module docstring for the
    # full design rationale, including why notification-bridge devices
    # are NOT on this table.

    def _collector_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "kind": row[1],
            "provider": row[2],
            "identity_ref": row[3],
            "credential_env_var": row[4],
            "provider_name": row[5],
            "allowed_uses": json.loads(row[6]) if row[6] else [],
            "last_qualified_at": row[7],
            "qualification_evidence": json.loads(row[8]) if row[8] else {},
            "checkpoint": json.loads(row[9]) if row[9] is not None else None,
            "checkpoint_updated_at": row[10],
            "health_state": row[11],
            "health_detail": row[12],
            "provider_config": json.loads(row[13]) if row[13] else {},
            "created_at": row[14],
            "updated_at": row[15],
        }

    _COLLECTOR_COLUMNS = (
        "id, kind, provider, identity_ref, credential_env_var, provider_name, allowed_uses, "
        "last_qualified_at, qualification_evidence, checkpoint, checkpoint_updated_at, health_state, "
        "health_detail, provider_config, created_at, updated_at"
    )

    def register_collector(
        self,
        *,
        collector_id: str,
        kind: str,
        provider: str,
        identity_ref: str | None,
        credential_env_var: str | None,
        provider_name: str | None,
        allowed_uses: list[str],
        provider_config: dict,
        default_health_state: str = "unqualified",
    ) -> dict:
        """Insert (or, idempotently, re-describe) one `collectors` row.
        Mirrors every pre-existing per-registry `register_*` method's own
        contract exactly: the CALLER (each kind's own `SignalStore.
        register_*` wrapper) is responsible for running that provider's
        own `validate_registration` BEFORE calling this -- this method
        itself only validates `kind` (an unrecognized kind is a
        programming error in this codebase, never a user input, so it
        raises rather than silently accepting an unknown registry).

        Re-registering the SAME `collector_id` replaces its identity/
        connection fields (`provider`, `identity_ref`,
        `credential_env_var`, `provider_name`, `allowed_uses`,
        `provider_config`) but preserves `qualification_evidence`,
        `last_qualified_at`, `checkpoint`, `checkpoint_updated_at`,
        `health_state`, `health_detail`, and `created_at` -- same
        "re-describing must not silently reset already-observed
        evidence" precedent every pre-existing registry's own
        `register_*` method already enforced. A caller that wants to
        preserve a `provider_config` sub-field across re-registration
        (e.g. Telegram's `noforwards`, website's `checkpoint_seen_urls`)
        must read the existing row first and merge that field into the
        new `provider_config` itself -- this method always replaces
        `provider_config` as a whole with what it's given."""
        try:
            CollectorKind(kind)
        except ValueError as exc:
            raise UnifiedCollectorError(
                f"kind must be one of {[k.value for k in CollectorKind]}, got {kind!r}"
            ) from exc
        now = now_utc().isoformat()
        with self._connect() as conn:
            existing = conn.execute("SELECT created_at FROM collectors WHERE id = ?", (collector_id,)).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """INSERT INTO collectors
                       (id, kind, provider, identity_ref, credential_env_var, provider_name, allowed_uses,
                        qualification_evidence, health_state, provider_config, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       kind = excluded.kind,
                       provider = excluded.provider,
                       identity_ref = excluded.identity_ref,
                       credential_env_var = excluded.credential_env_var,
                       provider_name = excluded.provider_name,
                       allowed_uses = excluded.allowed_uses,
                       provider_config = excluded.provider_config,
                       updated_at = excluded.updated_at""",
                (
                    collector_id,
                    kind,
                    provider,
                    identity_ref,
                    credential_env_var,
                    provider_name,
                    json.dumps(list(allowed_uses)),
                    default_health_state,
                    json.dumps(provider_config),
                    created_at,
                    now,
                ),
            )
        return self.get_collector(collector_id)  # type: ignore[return-value]

    def get_collector(self, collector_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._COLLECTOR_COLUMNS} FROM collectors WHERE id = ?", (collector_id,)
            ).fetchone()
        return self._collector_row_to_dict(row) if row else None

    def list_collectors(self, *, kind: str | None = None, provider: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if kind is not None and provider is not None:
                rows = conn.execute(
                    f"SELECT {self._COLLECTOR_COLUMNS} FROM collectors WHERE kind = ? AND provider = ? ORDER BY id",
                    (kind, provider),
                ).fetchall()
            elif kind is not None:
                rows = conn.execute(
                    f"SELECT {self._COLLECTOR_COLUMNS} FROM collectors WHERE kind = ? ORDER BY id", (kind,)
                ).fetchall()
            else:
                rows = conn.execute(f"SELECT {self._COLLECTOR_COLUMNS} FROM collectors ORDER BY id").fetchall()
        return [self._collector_row_to_dict(r) for r in rows]

    def update_collector_health(self, collector_id: str, health_state: str, *, detail: str | None = None) -> None:
        """The ONE place any unified-table collector's incident/health
        state is written -- the caller (each kind's own `SignalStore.
        update_*_health` wrapper) is responsible for validating
        `health_state` against that kind's own `CollectorHealth` enum
        BEFORE calling this, exactly like `register_collector` delegates
        field validation to its caller."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE collectors SET health_state = ?, health_detail = ?, updated_at = ? WHERE id = ?",
                (health_state, detail, now_utc().isoformat(), collector_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no collector registered with id={collector_id!r}")

    def record_collector_qualification_evidence(
        self,
        collector_id: str,
        *,
        evidence: dict,
        health_state: str,
        qualified_at: datetime | None = None,
    ) -> None:
        """Real evidence that authorized real-message receipt was
        confirmed for this collector. The caller supplies the resulting
        `health_state` (usually `healthy_qualified`, but e.g. Telegram's
        wrapper passes `protected_content_restricted` instead when
        `noforwards` is set -- see `SignalStore.
        record_telegram_collector_qualification_evidence`) since which
        states are even valid, and which one evidence implies, is a
        per-kind decision this generic method never makes itself."""
        when = (qualified_at or now_utc()).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE collectors
                   SET qualification_evidence = ?, last_qualified_at = ?,
                       health_state = ?, health_detail = NULL, updated_at = ?
                   WHERE id = ?""",
                (json.dumps(evidence), when, health_state, when, collector_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no collector registered with id={collector_id!r}")

    def get_collector_checkpoint(self, collector_id: str) -> Any:
        """The last live-admitted checkpoint value for this collector
        (JSON-decoded -- an int, a string, or `None`) -- `None` for a
        collector that has never processed a live message (including one
        that has only ever gone through a historical import, which never
        touches this column)."""
        with self._connect() as conn:
            row = conn.execute("SELECT checkpoint FROM collectors WHERE id = ?", (collector_id,)).fetchone()
        if row is None:
            raise KeyError(f"no collector registered with id={collector_id!r}")
        return json.loads(row[0]) if row[0] is not None else None

    def advance_collector_checkpoint(self, collector_id: str, checkpoint: Any) -> None:
        """Called ONLY after a message/article has genuinely been
        admitted to live routing. This generic method does not itself
        enforce monotonicity (an opaque JSON scalar can't be compared
        generically across every kind) -- a kind whose checkpoint must be
        monotonic (telegram/email's integer message id/UID) enforces that
        in its own `SignalStore.advance_*_collector_checkpoint` wrapper
        BEFORE calling this, exactly like the pre-existing per-table
        `MAX(...)` SQL guard it replaces."""
        now = now_utc().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE collectors SET checkpoint = ?, checkpoint_updated_at = ?, updated_at = ? WHERE id = ?",
                (json.dumps(checkpoint), now, now, collector_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no collector registered with id={collector_id!r}")

    def merge_collector_provider_config(self, collector_id: str, patch: dict) -> dict:
        """Shallow-merge `patch` into this row's existing `provider_config`
        (used for a provider-specific field a generic method has no
        vocabulary for -- e.g. Telegram's `noforwards`, website's
        `checkpoint_seen_urls` -- that still needs updating outside of a
        full `register_collector` re-describe). Returns the updated row."""
        existing = self.get_collector(collector_id)
        if existing is None:
            raise KeyError(f"no collector registered with id={collector_id!r}")
        merged = {**existing["provider_config"], **patch}
        with self._connect() as conn:
            conn.execute(
                "UPDATE collectors SET provider_config = ?, updated_at = ? WHERE id = ?",
                (json.dumps(merged), now_utc().isoformat(), collector_id),
            )
        return self.get_collector(collector_id)  # type: ignore[return-value]

    # -- Track 14: Provider/Source/Connection catalog (app/provider_catalog.py,
    # app/connections.py) -- see those modules' docstrings for the full data
    # model and how it relates to the collector/notification-bridge/phone-
    # escalation registries above. Minimal CRUD, no full UI, per that
    # track's own build-order decision.

    _PROVIDER_CATALOG_COLUMNS = (
        "id, display_name, aliases, logo_url, website, description, status, account_ownership, "
        "subscription_status, classification, asset_classes, strategy_types, provider_timezone, "
        "execution_eligibility, default_parser_profile, max_entry_age_seconds, stale_exit_policy, "
        "min_parse_confidence, correlation_window_seconds, risk_policy_ref, certification_state, "
        "certification_version, certified_at, operator_notes, created_at, updated_at, "
        # Track 16 -- appended at the end so every existing positional
        # index above stays correct; see app/signal_freshness.py's/
        # app/signal_correlation.py's own docstrings for what each means.
        "max_add_age_seconds, adjustment_stale_behavior, timestamp_source_preference, "
        "clock_skew_tolerance_seconds, recovered_event_behavior, conflict_resolution_policy, "
        "deterministic_primary_source_id, correlation_price_tolerance_pct"
    )

    def _provider_catalog_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "display_name": row[1],
            "aliases": json.loads(row[2]) if row[2] else [],
            "logo_url": row[3],
            "website": row[4],
            "description": row[5],
            "status": row[6],
            "account_ownership": row[7],
            "subscription_status": row[8],
            "classification": row[9],
            "asset_classes": json.loads(row[10]) if row[10] else [],
            "strategy_types": json.loads(row[11]) if row[11] else [],
            "provider_timezone": row[12],
            "execution_eligibility": row[13],
            "default_parser_profile": row[14],
            "max_entry_age_seconds": row[15],
            "stale_exit_policy": row[16],
            "min_parse_confidence": row[17],
            "correlation_window_seconds": row[18],
            "risk_policy_ref": row[19],
            "certification_state": row[20],
            "certification_version": row[21],
            "certified_at": row[22],
            "operator_notes": row[23],
            "created_at": row[24],
            "updated_at": row[25],
            "max_add_age_seconds": row[26],
            "adjustment_stale_behavior": row[27],
            "timestamp_source_preference": row[28],
            "clock_skew_tolerance_seconds": row[29],
            "recovered_event_behavior": row[30],
            "conflict_resolution_policy": row[31],
            "deterministic_primary_source_id": row[32],
            "correlation_price_tolerance_pct": row[33],
        }

    def register_provider(
        self,
        *,
        provider_id: str,
        display_name: str,
        aliases: list[str] | None = None,
        logo_url: str | None = None,
        website: str | None = None,
        description: str | None = None,
        status: str = "onboarding",
        account_ownership: str | None = None,
        subscription_status: str | None = None,
        classification: str | None = None,
        asset_classes: list[str] | None = None,
        strategy_types: list[str] | None = None,
        provider_timezone: str | None = None,
        execution_eligibility: str = "disabled",
        default_parser_profile: str | None = None,
        max_entry_age_seconds: int | None = None,
        stale_exit_policy: str | None = None,
        min_parse_confidence: float | None = None,
        correlation_window_seconds: int | None = None,
        risk_policy_ref: str | None = None,
        certification_state: str = "uncertified",
        certification_version: str | None = None,
        certified_at: datetime | None = None,
        operator_notes: str | None = None,
        max_add_age_seconds: int | None = None,
        adjustment_stale_behavior: str | None = None,
        timestamp_source_preference: str | None = None,
        clock_skew_tolerance_seconds: int | None = None,
        recovered_event_behavior: str | None = None,
        conflict_resolution_policy: str = "HOLD",
        deterministic_primary_source_id: str | None = None,
        correlation_price_tolerance_pct: float | None = None,
    ) -> dict:
        """Insert (or idempotently re-describe) one `providers` row.
        Re-registering the SAME `provider_id` replaces every field given
        but preserves `created_at` -- same convention as `register_
        collector`. Field vocabulary (status/classification/account_
        ownership/execution_eligibility/certification_state) is
        validated by `app.provider_catalog.validate_provider_
        registration` before anything is persisted."""
        validate_provider_registration(
            provider_id=provider_id,
            display_name=display_name,
            status=status,
            classification=classification,
            account_ownership=account_ownership,
            execution_eligibility=execution_eligibility,
            certification_state=certification_state,
        )
        now = now_utc().isoformat()
        certified_at_s = certified_at.isoformat() if certified_at else None
        with self._connect() as conn:
            existing = conn.execute("SELECT created_at FROM providers WHERE id = ?", (provider_id,)).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """INSERT INTO providers
                       (id, display_name, aliases, logo_url, website, description, status, account_ownership,
                        subscription_status, classification, asset_classes, strategy_types, provider_timezone,
                        execution_eligibility, default_parser_profile, max_entry_age_seconds, stale_exit_policy,
                        min_parse_confidence, correlation_window_seconds, risk_policy_ref, certification_state,
                        certification_version, certified_at, operator_notes, created_at, updated_at,
                        max_add_age_seconds, adjustment_stale_behavior, timestamp_source_preference,
                        clock_skew_tolerance_seconds, recovered_event_behavior, conflict_resolution_policy,
                        deterministic_primary_source_id, correlation_price_tolerance_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       display_name = excluded.display_name,
                       aliases = excluded.aliases,
                       logo_url = excluded.logo_url,
                       website = excluded.website,
                       description = excluded.description,
                       status = excluded.status,
                       account_ownership = excluded.account_ownership,
                       subscription_status = excluded.subscription_status,
                       classification = excluded.classification,
                       asset_classes = excluded.asset_classes,
                       strategy_types = excluded.strategy_types,
                       provider_timezone = excluded.provider_timezone,
                       execution_eligibility = excluded.execution_eligibility,
                       default_parser_profile = excluded.default_parser_profile,
                       max_entry_age_seconds = excluded.max_entry_age_seconds,
                       stale_exit_policy = excluded.stale_exit_policy,
                       min_parse_confidence = excluded.min_parse_confidence,
                       correlation_window_seconds = excluded.correlation_window_seconds,
                       risk_policy_ref = excluded.risk_policy_ref,
                       certification_state = excluded.certification_state,
                       certification_version = excluded.certification_version,
                       certified_at = excluded.certified_at,
                       operator_notes = excluded.operator_notes,
                       max_add_age_seconds = excluded.max_add_age_seconds,
                       adjustment_stale_behavior = excluded.adjustment_stale_behavior,
                       timestamp_source_preference = excluded.timestamp_source_preference,
                       clock_skew_tolerance_seconds = excluded.clock_skew_tolerance_seconds,
                       recovered_event_behavior = excluded.recovered_event_behavior,
                       conflict_resolution_policy = excluded.conflict_resolution_policy,
                       deterministic_primary_source_id = excluded.deterministic_primary_source_id,
                       correlation_price_tolerance_pct = excluded.correlation_price_tolerance_pct,
                       updated_at = excluded.updated_at""",
                (
                    provider_id,
                    display_name,
                    json.dumps(list(aliases or [])),
                    logo_url,
                    website,
                    description,
                    status,
                    account_ownership,
                    subscription_status,
                    classification,
                    json.dumps(list(asset_classes or [])),
                    json.dumps(list(strategy_types or [])),
                    provider_timezone,
                    execution_eligibility,
                    default_parser_profile,
                    max_entry_age_seconds,
                    stale_exit_policy,
                    min_parse_confidence,
                    correlation_window_seconds,
                    risk_policy_ref,
                    certification_state,
                    certification_version,
                    certified_at_s,
                    operator_notes,
                    created_at,
                    now,
                    max_add_age_seconds,
                    adjustment_stale_behavior,
                    timestamp_source_preference,
                    clock_skew_tolerance_seconds,
                    recovered_event_behavior,
                    conflict_resolution_policy,
                    deterministic_primary_source_id,
                    correlation_price_tolerance_pct,
                ),
            )
        return self.get_provider_catalog_entry(provider_id)  # type: ignore[return-value]

    def get_provider_catalog_entry(self, provider_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._PROVIDER_CATALOG_COLUMNS} FROM providers WHERE id = ?", (provider_id,)
            ).fetchone()
        return self._provider_catalog_row_to_dict(row) if row else None

    def list_provider_catalog_entries(self, *, status: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if status is not None:
                rows = conn.execute(
                    f"SELECT {self._PROVIDER_CATALOG_COLUMNS} FROM providers WHERE status = ? ORDER BY id",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(f"SELECT {self._PROVIDER_CATALOG_COLUMNS} FROM providers ORDER BY id").fetchall()
        return [self._provider_catalog_row_to_dict(r) for r in rows]

    def update_provider_catalog_entry(self, provider_id: str, patch: dict) -> dict:
        """Shallow-updates only the columns present in `patch` (a dict
        of column-name -> new value, JSON-list columns passed as plain
        Python lists). Raises `KeyError` for an unregistered provider,
        same convention as `merge_collector_provider_config`."""
        existing = self.get_provider_catalog_entry(provider_id)
        if existing is None:
            raise KeyError(f"no provider registered with id={provider_id!r}")
        allowed = set(self._PROVIDER_CATALOG_COLUMNS.replace(" ", "").split(",")) - {
            "id",
            "created_at",
            "updated_at",
        }
        json_columns = {"aliases", "asset_classes", "strategy_types"}
        set_clauses = []
        values: list[Any] = []
        for key, value in patch.items():
            if key not in allowed:
                raise KeyError(f"unknown providers column: {key!r}")
            set_clauses.append(f"{key} = ?")
            values.append(json.dumps(value) if key in json_columns else value)
        if not set_clauses:
            return existing
        set_clauses.append("updated_at = ?")
        now = now_utc().isoformat()
        values.append(now)
        values.append(provider_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE providers SET {', '.join(set_clauses)} WHERE id = ?", values)
        return self.get_provider_catalog_entry(provider_id)  # type: ignore[return-value]

    _SOURCE_COLUMNS = (
        "id, provider_id, platform, source_type, source_native_id, display_name, url_or_reference, enabled, "
        "priority, role, capture_method, connection_id, parser_profile, asset_classes, strategy_types, "
        "freshness_policy, dedup_policy, execution_eligibility, health_state, last_event_at, last_success_at, "
        "last_error_at, created_at, updated_at, acquisition_checkpoint"
    )

    def _source_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "provider_id": row[1],
            "platform": row[2],
            "source_type": row[3],
            "source_native_id": row[4],
            "display_name": row[5],
            "url_or_reference": row[6],
            "enabled": bool(row[7]),
            "priority": row[8],
            "role": row[9],
            "capture_method": row[10],
            "connection_id": row[11],
            "parser_profile": row[12],
            "asset_classes": json.loads(row[13]) if row[13] else [],
            "strategy_types": json.loads(row[14]) if row[14] else [],
            "freshness_policy": json.loads(row[15]) if row[15] else {},
            "dedup_policy": json.loads(row[16]) if row[16] else {},
            "execution_eligibility": row[17],
            "health_state": row[18],
            "last_event_at": row[19],
            "last_success_at": row[20],
            "last_error_at": row[21],
            "created_at": row[22],
            "updated_at": row[23],
            "acquisition_checkpoint": json.loads(row[24]) if row[24] else None,
        }

    def register_source(
        self,
        *,
        source_id: str,
        provider_id: str,
        platform: str,
        source_type: str | None = None,
        source_native_id: str | None = None,
        display_name: str | None = None,
        url_or_reference: str | None = None,
        enabled: bool = True,
        priority: int = 100,
        role: str = "PRIMARY",
        capture_method: str | None = None,
        connection_id: str | None = None,
        parser_profile: str | None = None,
        asset_classes: list[str] | None = None,
        strategy_types: list[str] | None = None,
        freshness_policy: dict | None = None,
        dedup_policy: dict | None = None,
        execution_eligibility: str = "disabled",
        health_state: str = "unqualified",
    ) -> dict:
        """Insert (or idempotently re-describe) one `sources` row. The
        caller is responsible for `provider_id` referring to an already-
        registered `providers` row (application-enforced -- see this
        method's own `KeyError` below -- SQLite's own `CREATE TABLE IF
        NOT EXISTS` bootstrap here does not declare a real `FOREIGN KEY`
        constraint, matching every other table in this schema's existing
        convention). `connection_id`, if given, must likewise refer to an
        already-registered `connections` row. If `url_or_reference` matches
        another already-registered, enabled `sources` row's, the returned
        dict carries a `duplicate_url_warning` key (no hard rejection --
        duplicate feed registration is sometimes legitimate, e.g. a
        `research`-purpose route and a `signal_candidate`-purpose route for
        the same feed; see this method's own body for the full rationale)."""
        validate_source_registration(
            source_id=source_id,
            provider_id=provider_id,
            platform=platform,
            role=role,
            execution_eligibility=execution_eligibility,
            health_state=health_state,
        )
        now = now_utc().isoformat()
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM providers WHERE id = ?", (provider_id,)).fetchone() is None:
                raise KeyError(f"no provider registered with id={provider_id!r}")
            if connection_id is not None:
                if conn.execute("SELECT 1 FROM connections WHERE id = ?", (connection_id,)).fetchone() is None:
                    raise KeyError(f"no connection registered with id={connection_id!r}")
            existing = conn.execute("SELECT created_at FROM sources WHERE id = ?", (source_id,)).fetchone()
            created_at = existing[0] if existing else now
            duplicate_ids: list[str] = []
            if url_or_reference:
                duplicate_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM sources WHERE url_or_reference = ? AND enabled = 1 AND id != ?",
                        (url_or_reference, source_id),
                    ).fetchall()
                ]
            conn.execute(
                """INSERT INTO sources
                       (id, provider_id, platform, source_type, source_native_id, display_name, url_or_reference,
                        enabled, priority, role, capture_method, connection_id, parser_profile, asset_classes,
                        strategy_types, freshness_policy, dedup_policy, execution_eligibility, health_state,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       provider_id = excluded.provider_id,
                       platform = excluded.platform,
                       source_type = excluded.source_type,
                       source_native_id = excluded.source_native_id,
                       display_name = excluded.display_name,
                       url_or_reference = excluded.url_or_reference,
                       enabled = excluded.enabled,
                       priority = excluded.priority,
                       role = excluded.role,
                       capture_method = excluded.capture_method,
                       connection_id = excluded.connection_id,
                       parser_profile = excluded.parser_profile,
                       asset_classes = excluded.asset_classes,
                       strategy_types = excluded.strategy_types,
                       freshness_policy = excluded.freshness_policy,
                       dedup_policy = excluded.dedup_policy,
                       execution_eligibility = excluded.execution_eligibility,
                       updated_at = excluded.updated_at""",
                (
                    source_id,
                    provider_id,
                    platform,
                    source_type,
                    source_native_id,
                    display_name,
                    url_or_reference,
                    1 if enabled else 0,
                    priority,
                    role,
                    capture_method,
                    connection_id,
                    parser_profile,
                    json.dumps(list(asset_classes or [])),
                    json.dumps(list(strategy_types or [])),
                    json.dumps(freshness_policy or {}),
                    json.dumps(dedup_policy or {}),
                    execution_eligibility,
                    health_state,
                    created_at,
                    now,
                ),
            )
        result = self.get_source(source_id)
        assert result is not None
        if duplicate_ids:
            # Two (or more) enabled `sources` rows can legitimately point at
            # the identical `url_or_reference` -- e.g. one `research`-purpose
            # RSS route and one `signal_candidate`-purpose route for the same
            # feed, or a PRIMARY/RECONCILIATION pair (see `SourceRole`'s own
            # docstring in app/provider_catalog.py). A hard uniqueness
            # constraint would reject that legitimate case, so this is a
            # non-blocking, surfaced warning -- same "detect and report,
            # don't silently allow nor silently reject" convention as
            # `looks_like_raw_credential` (app/connections.py), applied here
            # as a warning rather than that helper's hard rejection because,
            # unlike a pasted raw credential, a duplicate feed_url sometimes
            # IS the operator's deliberate intent.
            result["duplicate_url_warning"] = (
                f"url_or_reference {url_or_reference!r} is already registered on enabled source(s) "
                f"{sorted(duplicate_ids)!r} -- if unintentional, two sources polling the same feed can "
                "each independently emit a signal for the same real-world item."
            )
        return result

    def get_source(self, source_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {self._SOURCE_COLUMNS} FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._source_row_to_dict(row) if row else None

    def list_sources(self, *, provider_id: str | None = None, connection_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if provider_id is not None:
                rows = conn.execute(
                    f"SELECT {self._SOURCE_COLUMNS} FROM sources WHERE provider_id = ? ORDER BY priority, id",
                    (provider_id,),
                ).fetchall()
            elif connection_id is not None:
                rows = conn.execute(
                    f"SELECT {self._SOURCE_COLUMNS} FROM sources WHERE connection_id = ? ORDER BY priority, id",
                    (connection_id,),
                ).fetchall()
            else:
                rows = conn.execute(f"SELECT {self._SOURCE_COLUMNS} FROM sources ORDER BY provider_id, priority, id").fetchall()
        return [self._source_row_to_dict(r) for r in rows]

    def update_source_health(
        self,
        source_id: str,
        health_state: str,
        *,
        last_event_at: datetime | None = None,
        last_success_at: datetime | None = None,
        last_error_at: datetime | None = None,
    ) -> None:
        """The ONE place a `sources` row's health/observation timestamps
        are written -- same "caller validates its own kind's health
        vocabulary first" convention as `update_collector_health`."""
        now = now_utc().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE sources
                   SET health_state = ?,
                       last_event_at = COALESCE(?, last_event_at),
                       last_success_at = COALESCE(?, last_success_at),
                       last_error_at = COALESCE(?, last_error_at),
                       updated_at = ?
                   WHERE id = ?""",
                (
                    health_state,
                    last_event_at.isoformat() if last_event_at else None,
                    last_success_at.isoformat() if last_success_at else None,
                    last_error_at.isoformat() if last_error_at else None,
                    now,
                    source_id,
                ),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no source registered with id={source_id!r}")

    def update_source_checkpoint(self, source_id: str, checkpoint: Any) -> None:
        """Track 24: persists a `sources` row's own poll checkpoint --
        see that table's own CREATE TABLE comment for why this lives
        here rather than on `collectors`. `checkpoint` is an opaque,
        adapter-owned JSON-serializable value (this method does not
        interpret or enforce monotonicity on it -- the calling adapter,
        e.g. `app.sources.rss_source.RssSourceAdapter.poll`, is solely
        responsible for only ever proposing a genuinely newer value)."""
        now = now_utc().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sources SET acquisition_checkpoint = ?, updated_at = ? WHERE id = ?",
                (json.dumps(checkpoint), now, source_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no source registered with id={source_id!r}")

    # -- Track 24: `source_observations` -- see that table's own CREATE
    # TABLE comment and app/sources/adapter_contract.py's module
    # docstring for the full design this generalizes from
    # `notification_bridge_events`.

    _SOURCE_OBSERVATION_COLUMNS = (
        "id, connection_id, provider_id, source_id, platform, source_namespace, original_item_id, canonical_url, "
        "revision_identifier, observation_kind, content_hash, revision_seq, source_authored_at, source_updated_at, "
        "first_observed_at, retrieved_at, timestamp_origin, timestamp_uncertain, completeness, extracted_text, "
        "attachment_refs, adapter_name, backend, parser_version, retrieval_method, correlation_id, "
        "acquisition_run_id, purpose, eligibility_state, rejection_reason, created_at"
    )

    def _source_observation_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "connection_id": row[1],
            "provider_id": row[2],
            "source_id": row[3],
            "platform": row[4],
            "source_namespace": row[5],
            "original_item_id": row[6],
            "canonical_url": row[7],
            "revision_identifier": row[8],
            "observation_kind": row[9],
            "content_hash": row[10],
            "revision_seq": row[11],
            "source_authored_at": row[12],
            "source_updated_at": row[13],
            "first_observed_at": row[14],
            "retrieved_at": row[15],
            "timestamp_origin": row[16],
            "timestamp_uncertain": bool(row[17]),
            "completeness": row[18],
            "extracted_text": row[19],
            "attachment_refs": json.loads(row[20]) if row[20] else [],
            "adapter_name": row[21],
            "backend": row[22],
            "parser_version": row[23],
            "retrieval_method": row[24],
            "correlation_id": row[25],
            "acquisition_run_id": row[26],
            "purpose": row[27],
            "eligibility_state": row[28],
            "rejection_reason": row[29],
            "created_at": row[30],
        }

    def record_source_observation(
        self,
        *,
        platform: str,
        original_item_id: str,
        observation_kind: str,
        completeness: str,
        adapter_name: str,
        first_observed_at: datetime,
        retrieved_at: datetime,
        connection_id: str | None = None,
        provider_id: str | None = None,
        source_id: str | None = None,
        source_namespace: str | None = None,
        canonical_url: str | None = None,
        revision_identifier: str | None = None,
        content_hash: str | None = None,
        revision_seq: int = 1,
        source_authored_at: datetime | None = None,
        source_updated_at: datetime | None = None,
        timestamp_origin: str | None = None,
        timestamp_uncertain: bool = False,
        extracted_text: str | None = None,
        attachment_refs: list[dict] | None = None,
        backend: str | None = None,
        parser_version: str | None = None,
        retrieval_method: str | None = None,
        correlation_id: str | None = None,
        acquisition_run_id: str | None = None,
        purpose: str = "research",
        eligibility_state: str = "not_eligible",
        rejection_reason: str | None = None,
    ) -> dict:
        """Appends one row to the append-only `source_observations`
        ledger (same "audit ledger, only ever appended to" convention as
        `connection_cost_events`/`notification_bridge_events`). Validates
        `observation_kind`/`completeness` against this codebase's own
        closed vocabularies (the latter reusing
        `app.notification_bridge.ContentCompleteness`'s five states
        verbatim) -- an unrecognized value raises rather than being
        silently persisted, same "never a guessed/invented state" rule
        as every other registry in this file. `connection_id`/
        `provider_id`/`source_id`, when given, must refer to already-
        registered rows (application-enforced, same convention as
        `register_source`'s own FK-style checks) -- `None` is honestly
        allowed for any of the three (see this table's own CREATE TABLE
        comment)."""
        from app.notification_bridge import ContentCompleteness

        if observation_kind not in _SOURCE_OBSERVATION_KINDS:
            raise ValueError(f"observation_kind must be one of {_SOURCE_OBSERVATION_KINDS}, got {observation_kind!r}")
        try:
            ContentCompleteness(completeness)
        except ValueError as exc:
            raise ValueError(
                f"completeness must be one of {[c.value for c in ContentCompleteness]}, got {completeness!r}"
            ) from exc
        if purpose not in _SOURCE_OBSERVATION_PURPOSES:
            raise ValueError(f"purpose must be one of {_SOURCE_OBSERVATION_PURPOSES}, got {purpose!r}")
        if not original_item_id or not original_item_id.strip():
            raise ValueError("original_item_id is required")
        if not platform or not platform.strip():
            raise ValueError("platform is required")
        if not adapter_name or not adapter_name.strip():
            raise ValueError("adapter_name is required")

        with self._connect() as conn:
            if connection_id is not None:
                if conn.execute("SELECT 1 FROM connections WHERE id = ?", (connection_id,)).fetchone() is None:
                    raise KeyError(f"no connection registered with id={connection_id!r}")
            if provider_id is not None:
                if conn.execute("SELECT 1 FROM providers WHERE id = ?", (provider_id,)).fetchone() is None:
                    raise KeyError(f"no provider registered with id={provider_id!r}")
            if source_id is not None:
                if conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone() is None:
                    raise KeyError(f"no source registered with id={source_id!r}")

            observation_id = f"sobs_{uuid.uuid4().hex}"
            now = now_utc().isoformat()
            conn.execute(
                f"""INSERT INTO source_observations ({self._SOURCE_OBSERVATION_COLUMNS})
                   VALUES ({", ".join(["?"] * 31)})""",
                (
                    observation_id,
                    connection_id,
                    provider_id,
                    source_id,
                    platform,
                    source_namespace,
                    original_item_id,
                    canonical_url,
                    revision_identifier,
                    observation_kind,
                    content_hash,
                    revision_seq,
                    source_authored_at.isoformat() if source_authored_at else None,
                    source_updated_at.isoformat() if source_updated_at else None,
                    first_observed_at.isoformat(),
                    retrieved_at.isoformat(),
                    timestamp_origin,
                    1 if timestamp_uncertain else 0,
                    completeness,
                    extracted_text,
                    json.dumps(attachment_refs or []),
                    adapter_name,
                    backend,
                    parser_version,
                    retrieval_method,
                    correlation_id,
                    acquisition_run_id,
                    purpose,
                    eligibility_state,
                    rejection_reason,
                    now,
                ),
            )
            row = conn.execute(
                f"SELECT {self._SOURCE_OBSERVATION_COLUMNS} FROM source_observations WHERE id = ?",
                (observation_id,),
            ).fetchone()
        return self._source_observation_row_to_dict(row)  # type: ignore[arg-type]

    def get_source_observations_for_source(self, source_id: str, *, limit: int | None = None) -> list[dict]:
        """Newest-first read of every `source_observations` row recorded
        for one `sources` row -- the real, persisted audit trail a
        dashboard or the next phase's dedup/revision logic reads from."""
        with self._connect() as conn:
            if limit is not None:
                rows = conn.execute(
                    f"SELECT {self._SOURCE_OBSERVATION_COLUMNS} FROM source_observations "
                    "WHERE source_id = ? ORDER BY retrieved_at DESC LIMIT ?",
                    (source_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {self._SOURCE_OBSERVATION_COLUMNS} FROM source_observations "
                    "WHERE source_id = ? ORDER BY retrieved_at DESC",
                    (source_id,),
                ).fetchall()
        return [self._source_observation_row_to_dict(r) for r in rows]

    _CONNECTION_COLUMNS = (
        "id, connection_type, display_name, credential_reference, authentication_type, account_identity, "
        "connection_state, authorization_state, scopes, capabilities, rate_limits, cost_info, "
        "last_authenticated_at, token_expires_at, last_heartbeat_at, last_successful_event_at, last_error_at, "
        "last_error_detail, retry_state, health_score, created_at, updated_at"
    )

    def _connection_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "connection_type": row[1],
            "display_name": row[2],
            "credential_reference": row[3],
            "authentication_type": row[4],
            "account_identity": row[5],
            "connection_state": row[6],
            "authorization_state": row[7],
            "scopes": json.loads(row[8]) if row[8] else [],
            "capabilities": json.loads(row[9]) if row[9] else {},
            "rate_limits": json.loads(row[10]) if row[10] else {},
            "cost_info": json.loads(row[11]) if row[11] else {},
            "last_authenticated_at": row[12],
            "token_expires_at": row[13],
            "last_heartbeat_at": row[14],
            "last_successful_event_at": row[15],
            "last_error_at": row[16],
            "last_error_detail": row[17],
            "retry_state": json.loads(row[18]) if row[18] else {},
            "health_score": row[19],
            "created_at": row[20],
            "updated_at": row[21],
        }

    def register_connection(
        self,
        *,
        connection_id: str,
        connection_type: str,
        display_name: str | None = None,
        credential_reference: str | None = None,
        authentication_type: str | None = None,
        account_identity: str | None = None,
        connection_state: str = "unconfigured",
        authorization_state: str = "unauthorized",
        scopes: list[str] | None = None,
        capabilities: dict | None = None,
        rate_limits: dict | None = None,
        cost_info: dict | None = None,
    ) -> dict:
        """Insert (or idempotently re-describe) one `connections` row.
        Field vocabulary/credential-shape is validated by
        `app.connections.validate_connection_registration` before
        anything is persisted -- see that function's own docstring for
        the raw-credential heuristic guard it applies."""
        validate_connection_registration(
            connection_id=connection_id,
            connection_type=connection_type,
            credential_reference=credential_reference,
            connection_state=connection_state,
            authorization_state=authorization_state,
        )
        # Track 19: an explicit `capabilities={}` (falsy) is NOT the same
        # as "no override given" -- only `capabilities is None` (the
        # caller never mentioned it at all) falls back to this
        # connection_type's real, verified default shape. A caller that
        # explicitly passes `{}` gets exactly that stored (honest "this
        # caller asserted nothing"), matching `capabilities or {}`'s own
        # pre-existing behavior for any other explicit value.
        if capabilities is None:
            capabilities = default_capabilities_for_connection_type(connection_type)
        now = now_utc().isoformat()
        with self._connect() as conn:
            existing = conn.execute("SELECT created_at FROM connections WHERE id = ?", (connection_id,)).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """INSERT INTO connections
                       (id, connection_type, display_name, credential_reference, authentication_type,
                        account_identity, connection_state, authorization_state, scopes, capabilities,
                        rate_limits, cost_info, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       connection_type = excluded.connection_type,
                       display_name = excluded.display_name,
                       credential_reference = excluded.credential_reference,
                       authentication_type = excluded.authentication_type,
                       account_identity = excluded.account_identity,
                       connection_state = excluded.connection_state,
                       authorization_state = excluded.authorization_state,
                       scopes = excluded.scopes,
                       capabilities = excluded.capabilities,
                       rate_limits = excluded.rate_limits,
                       cost_info = excluded.cost_info,
                       updated_at = excluded.updated_at""",
                (
                    connection_id,
                    connection_type,
                    display_name,
                    credential_reference,
                    authentication_type,
                    account_identity,
                    connection_state,
                    authorization_state,
                    json.dumps(list(scopes or [])),
                    json.dumps(capabilities or {}),
                    json.dumps(rate_limits or {}),
                    json.dumps(cost_info or {}),
                    created_at,
                    now,
                ),
            )
        return self.get_connection(connection_id)  # type: ignore[return-value]

    def get_connection(self, connection_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._CONNECTION_COLUMNS} FROM connections WHERE id = ?", (connection_id,)
            ).fetchone()
        return self._connection_row_to_dict(row) if row else None

    def list_connections(self, *, connection_type: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if connection_type is not None:
                rows = conn.execute(
                    f"SELECT {self._CONNECTION_COLUMNS} FROM connections WHERE connection_type = ? ORDER BY id",
                    (connection_type,),
                ).fetchall()
            else:
                rows = conn.execute(f"SELECT {self._CONNECTION_COLUMNS} FROM connections ORDER BY id").fetchall()
        return [self._connection_row_to_dict(r) for r in rows]

    def update_connection_health(
        self,
        connection_id: str,
        *,
        connection_state: str | None = None,
        authorization_state: str | None = None,
        last_heartbeat_at: datetime | None = None,
        last_successful_event_at: datetime | None = None,
        last_error_at: datetime | None = None,
        last_error_detail: str | None = None,
        health_score: float | None = None,
    ) -> None:
        """The ONE place a `connections` row's live health/heartbeat
        state is written. Only the fields explicitly given (non-`None`)
        are changed -- a caller updating just `last_heartbeat_at` (a
        routine heartbeat) never has to know or resupply the row's
        current `connection_state`."""
        existing = self.get_connection(connection_id)
        if existing is None:
            raise KeyError(f"no connection registered with id={connection_id!r}")
        now = now_utc().isoformat()
        with self._connect() as conn:
            conn.execute(
                """UPDATE connections
                   SET connection_state = COALESCE(?, connection_state),
                       authorization_state = COALESCE(?, authorization_state),
                       last_heartbeat_at = COALESCE(?, last_heartbeat_at),
                       last_successful_event_at = COALESCE(?, last_successful_event_at),
                       last_error_at = COALESCE(?, last_error_at),
                       last_error_detail = COALESCE(?, last_error_detail),
                       health_score = COALESCE(?, health_score),
                       updated_at = ?
                   WHERE id = ?""",
                (
                    connection_state,
                    authorization_state,
                    last_heartbeat_at.isoformat() if last_heartbeat_at else None,
                    last_successful_event_at.isoformat() if last_successful_event_at else None,
                    last_error_at.isoformat() if last_error_at else None,
                    last_error_detail,
                    health_score,
                    now,
                    connection_id,
                ),
            )

    # -- Track 19: connection health / checkpoint diagnostics / cost -------
    #
    # See app/connections.py's own docstrings for `compute_connection_health`
    # (the real HEALTHY/DEGRADED/OFFLINE/NEVER_CONNECTED/INSUFFICIENT_DATA/
    # UNKNOWN computation) and `default_capabilities_for_connection_type`.
    # Everything below is READ-ONLY diagnostics plus a cost-recording
    # ledger -- no recovery/remediation logic, no live-routing impact.

    def get_connection_health(self, connection_id: str) -> dict | None:
        """One connection's real, freshly computed health -- never a
        stored/cached verdict (`health_score` on the row is a separate,
        caller-supplied field this does not read or write). `None` when
        no such connection is registered."""
        connection = self.get_connection(connection_id)
        if connection is None:
            return None
        health = compute_connection_health(connection)
        return {"connection_id": connection_id, "connection_type": connection["connection_type"], **health}

    def get_connection_health_summary(self) -> dict:
        """Dashboard summary (the user's own spec: "N Healthy / N Degraded
        / N Offline") across every registered `connections` row -- real
        counts from `compute_connection_health`, computed fresh each call,
        never a cached/fabricated number. Every `ConnectionHealthState`
        member gets a key (including `never_connected`/
        `insufficient_data`/`unknown`), even when its count is 0, so a
        dashboard never has to guess whether a missing key means zero or
        "not computed"."""
        from app.connections import ConnectionHealthState

        counts: dict[str, int] = {state.value: 0 for state in ConnectionHealthState}
        connections = []
        for connection in self.list_connections():
            health = compute_connection_health(connection)
            counts[health["state"]] += 1
            connections.append(
                {
                    "connection_id": connection["id"],
                    "connection_type": connection["connection_type"],
                    "display_name": connection["display_name"],
                    **health,
                }
            )
        return {"counts": counts, "total": len(connections), "connections": connections}

    def get_connection_checkpoint_status(self, connection_id: str) -> dict:
        """Honest, read-only checkpoint/reconciliation diagnostics for one
        connection (the user's own spec: "expected checkpoint, current
        checkpoint, gaps, recovery attempts, unrecoverable gaps, backlog,
        last successful reconciliation").

        This codebase's real checkpoint concept (`collectors.checkpoint`,
        Track 8) lives on the UNIFIED COLLECTOR registry, which has NO
        foreign-key relationship to `connections`/`sources` at all (see
        this module's own `collectors` table comment -- collectors predate
        Track 14's provider/source/connection layer and were never
        migrated onto it beyond the one-time `legacy_{kind}`/
        `android_notification` backfill rows Track 14's own migration
        created). There is therefore no real mechanism in this codebase
        today that can compute a `connections` row's "expected checkpoint
        vs. current checkpoint" gap, a recovery-attempt count, or an
        unrecoverable-gap count -- reporting any of those as a number
        would be fabrication (CLAUDE.md #11), so each is reported as the
        literal string `"not_tracked"`, never a fake `0`.

        What IS real and available: every `sources` row this connection
        serves (`sources.connection_id`) carries its own
        `last_event_at`/`last_success_at`/`last_error_at` (`app/db.py`'s
        `update_source_health`) -- a genuine, if coarse, "is this source's
        traffic caught up or stale" signal. This reuses the same
        `CONNECTION_HEARTBEAT_STALE_SECONDS` threshold concept
        `compute_connection_health` uses (same "don't invent a new
        arbitrary number" instruction) to label each source
        `caught_up`/`stale`/`insufficient_data`."""
        from app.connections import CONNECTION_HEARTBEAT_STALE_SECONDS

        connection = self.get_connection(connection_id)
        if connection is None:
            raise KeyError(f"no connection registered with id={connection_id!r}")

        now = now_utc()
        sources_status = []
        for source in self.list_sources(connection_id=connection_id):
            last_activity_raw = source.get("last_event_at") or source.get("last_success_at")
            if last_activity_raw is None:
                freshness = "insufficient_data"
                age_seconds = None
            else:
                last_activity = datetime.fromisoformat(last_activity_raw)
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (now - last_activity).total_seconds())
                freshness = "stale" if age_seconds >= CONNECTION_HEARTBEAT_STALE_SECONDS else "caught_up"
            sources_status.append(
                {
                    "source_id": source["id"],
                    "provider_id": source["provider_id"],
                    "last_event_at": source.get("last_event_at"),
                    "last_success_at": source.get("last_success_at"),
                    "last_error_at": source.get("last_error_at"),
                    "age_seconds": age_seconds,
                    "freshness": freshness,
                }
            )

        return {
            "connection_id": connection_id,
            "connection_type": connection["connection_type"],
            "checkpoint_tracking": "not_tracked",
            "checkpoint_tracking_detail": (
                "app/db.py's unified `collectors` table (Track 8) is this codebase's only real "
                "checkpoint concept, and it has no foreign-key link to `connections`/`sources` "
                "(Track 14) -- expected/current checkpoint, gaps, recovery attempts, and "
                "unrecoverable gaps cannot be honestly computed for a connection today; see this "
                "method's own docstring."
            ),
            "expected_checkpoint": "not_tracked",
            "current_checkpoint": "not_tracked",
            "gaps": "not_tracked",
            "recovery_attempts": "not_tracked",
            "unrecoverable_gaps": "not_tracked",
            "backlog": "not_tracked",
            "last_successful_reconciliation": "not_tracked",
            "stale_after_seconds": CONNECTION_HEARTBEAT_STALE_SECONDS,
            "sources": sources_status,
        }

    _CONNECTION_COST_EVENT_COLUMNS = (
        "id, connection_id, amount, currency, category, event_count, ai_calls, tokens, "
        "browser_minutes, mobile_agent_calls, occurred_at, recorded_at, note"
    )

    def _connection_cost_event_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "connection_id": row[1],
            "amount": row[2],
            "currency": row[3],
            "category": row[4],
            "event_count": row[5],
            "ai_calls": row[6],
            "tokens": row[7],
            "browser_minutes": row[8],
            "mobile_agent_calls": row[9],
            "occurred_at": row[10],
            "recorded_at": row[11],
            "note": row[12],
        }

    def record_connection_cost_event(
        self,
        connection_id: str,
        *,
        amount: float,
        currency: str = "USD",
        category: str,
        event_count: int = 1,
        occurred_at: datetime | None = None,
        ai_calls: int | None = None,
        tokens: int | None = None,
        browser_minutes: float | None = None,
        mobile_agent_calls: int | None = None,
        note: str | None = None,
    ) -> dict:
        """Appends one row to the append-only `connection_cost_events`
        ledger (same "audit ledger, never mutated/deleted, only appended
        to" convention as `signal_correlation_evidence`/
        `phone_escalation_attempts`). This is the RECORDING mechanism
        only -- see this module's own module-level docstring section on
        cost tracking for why no live-provider-billing auto-detection is
        wired here (out of scope for this track, a real follow-up).
        `amount`/`category` are required (never a bare unattributed
        number); `ai_calls`/`tokens`/`browser_minutes`/`mobile_agent_calls`
        are optional and left `NULL` unless a caller actually recorded
        one -- never fabricated placeholders for a capability this
        codebase doesn't really meter yet (e.g. `app/phone_escalation.py`'s
        `SignalExtractor` is a stub with no real LLM calls)."""
        if self.get_connection(connection_id) is None:
            raise KeyError(f"no connection registered with id={connection_id!r}")
        if amount < 0:
            raise ValueError("amount must be >= 0")
        if event_count < 0:
            raise ValueError("event_count must be >= 0")
        if not category or not category.strip():
            raise ValueError("category is required")
        occurred_at = occurred_at or now_utc()
        now = now_utc().isoformat()
        event_id = f"cce_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO connection_cost_events
                       (id, connection_id, amount, currency, category, event_count, ai_calls, tokens,
                        browser_minutes, mobile_agent_calls, occurred_at, recorded_at, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    connection_id,
                    amount,
                    currency,
                    category,
                    event_count,
                    ai_calls,
                    tokens,
                    browser_minutes,
                    mobile_agent_calls,
                    occurred_at.isoformat(),
                    now,
                    note,
                ),
            )
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._CONNECTION_COST_EVENT_COLUMNS} FROM connection_cost_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        return self._connection_cost_event_row_to_dict(row)  # type: ignore[arg-type]

    def list_connection_cost_events(self, connection_id: str, *, since: datetime | None = None) -> list[dict]:
        with self._connect() as conn:
            if since is not None:
                rows = conn.execute(
                    f"SELECT {self._CONNECTION_COST_EVENT_COLUMNS} FROM connection_cost_events "
                    "WHERE connection_id = ? AND occurred_at >= ? ORDER BY occurred_at",
                    (connection_id, since.isoformat()),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {self._CONNECTION_COST_EVENT_COLUMNS} FROM connection_cost_events "
                    "WHERE connection_id = ? ORDER BY occurred_at",
                    (connection_id,),
                ).fetchall()
        return [self._connection_cost_event_row_to_dict(r) for r in rows]

    def get_connection_cost_summary(self, connection_id: str, *, since: datetime | None = None) -> dict:
        """Real totals computed from `connection_cost_events` rows only --
        the user's own spec ("$ spent, events received, cost/event, AI
        calls, tokens, browser minutes, mobile-agent calls") answered
        honestly: `insufficient_data` (never a fabricated `0`/`0.0`) when
        no cost event has ever been recorded for this connection in the
        window. `since` defaults to the start of the current UTC month
        ("this month", per the spec) -- pass `since=None` explicitly and
        this is what you get; an all-time summary needs a caller-supplied
        `since` far enough in the past (or a dedicated all-time call is a
        follow-up, not built here)."""
        if self.get_connection(connection_id) is None:
            raise KeyError(f"no connection registered with id={connection_id!r}")
        if since is None:
            today = now_utc()
            since = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        events = self.list_connection_cost_events(connection_id, since=since)
        if not events:
            return {
                "connection_id": connection_id,
                "since": since.isoformat(),
                "status": "insufficient_data",
                "total_amount": None,
                "currency": None,
                "total_events": 0,
                "cost_per_event": None,
                "ai_calls": None,
                "tokens": None,
                "browser_minutes": None,
                "mobile_agent_calls": None,
                "event_count_recorded": 0,
            }
        currencies = {e["currency"] for e in events}
        total_amount = sum(e["amount"] for e in events)
        total_events = sum(e["event_count"] for e in events)
        ai_calls = sum(e["ai_calls"] for e in events if e["ai_calls"] is not None) or None
        tokens = sum(e["tokens"] for e in events if e["tokens"] is not None) or None
        browser_minutes = sum(e["browser_minutes"] for e in events if e["browser_minutes"] is not None) or None
        mobile_agent_calls = sum(e["mobile_agent_calls"] for e in events if e["mobile_agent_calls"] is not None) or None
        return {
            "connection_id": connection_id,
            "since": since.isoformat(),
            "status": "ok" if len(currencies) == 1 else "mixed_currencies",
            "total_amount": total_amount,
            "currency": next(iter(currencies)) if len(currencies) == 1 else sorted(currencies),
            "total_events": total_events,
            "cost_per_event": (total_amount / total_events) if total_events > 0 else None,
            "ai_calls": ai_calls,
            "tokens": tokens,
            "browser_minutes": browser_minutes,
            "mobile_agent_calls": mobile_agent_calls,
            "event_count_recorded": len(events),
        }

    # -- Track 17: provider certification checklist (app/certification.py) --
    # -- and shadow mode results (app/shadow_mode.py) --

    _CERTIFICATION_CHECK_COLUMNS = (
        "id, provider_id, source_id, asset_class, account_route, check_name, status, evidence, "
        "checked_at, checked_by, created_at, updated_at"
    )

    def _certification_check_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "provider_id": row[1],
            "source_id": row[2],
            "asset_class": row[3],
            "account_route": row[4],
            "check_name": row[5],
            "status": row[6],
            "evidence": json.loads(row[7]) if row[7] else {},
            "checked_at": row[8],
            "checked_by": row[9],
            "created_at": row[10],
            "updated_at": row[11],
        }

    def ensure_certification_checks(
        self, *, provider_id: str, source_id: str, asset_class: str, account_route: str
    ) -> list[dict]:
        """Idempotently creates (never overwrites) one `NOT_RUN` row per
        `app.certification.ALL_CHECKS` for this exact scope, then returns
        every row for it -- the lazy-bootstrap this module's own REST
        routes call before reading/computing a scope's checklist, so a
        scope's 14 rows always exist by the time anything reads them,
        without a separate migration/backfill step per scope. Existing
        rows (already PASS/FAIL/SKIPPED, or a prior NOT_RUN) are left
        completely untouched -- `INSERT OR IGNORE`, never `OR REPLACE`."""
        validate_scope(
            provider_id=provider_id, source_id=source_id, asset_class=asset_class, account_route=account_route
        )
        now = now_utc().isoformat()
        with self._connect() as conn:
            for check in ALL_CHECKS:
                row_id = f"{provider_id}:{source_id}:{asset_class}:{account_route}:{check.value}"
                conn.execute(
                    """INSERT OR IGNORE INTO certification_checks
                           (id, provider_id, source_id, asset_class, account_route, check_name, status,
                            evidence, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'NOT_RUN', '{}', ?, ?)""",
                    (row_id, provider_id, source_id, asset_class, account_route, check.value, now, now),
                )
        return self.list_certification_checks(
            provider_id=provider_id, source_id=source_id, asset_class=asset_class, account_route=account_route
        )

    def list_certification_checks(
        self,
        *,
        provider_id: str,
        source_id: str | None = None,
        asset_class: str | None = None,
        account_route: str | None = None,
    ) -> list[dict]:
        """Lists the real, CURRENT `certification_checks` rows for a
        scope, with every `app.certification.CHECK_KIND.AUTOMATED` check
        recomputed fresh from real underlying data before being
        returned -- see `compute_certification_check`'s own docstring
        for why this is never a stale cached read for those six checks.
        `source_id`/`asset_class`/`account_route` narrow the scope
        further; provider_id alone lists every scope this provider has
        any recorded/bootstrapped checks for."""
        clauses = ["provider_id = ?"]
        params: list[Any] = [provider_id]
        for column, value in (
            ("source_id", source_id),
            ("asset_class", asset_class),
            ("account_route", account_route),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._CERTIFICATION_CHECK_COLUMNS} FROM certification_checks "
                f"WHERE {' AND '.join(clauses)} ORDER BY source_id, asset_class, account_route, check_name",
                params,
            ).fetchall()
        checks = [self._certification_check_row_to_dict(r) for r in rows]
        for row in checks:
            if CHECK_KIND.get(parse_check_name(row["check_name"])) == CheckKind.AUTOMATED:
                self._merge_automated_evidence(row)
        return checks

    def _merge_automated_evidence(self, row: dict) -> None:
        """Recomputes ONE automated check's real status/evidence
        in-place on `row` (a dict already shaped like a
        `certification_checks` row) -- see
        `app/certification_evidence.py` for exactly what each check
        reads. Never persists the recomputed value back to the table:
        an AUTOMATED check has no durable "last known status" at all,
        by design (see this module's own SCHEMA comment on
        `certification_checks` -- there is nothing to persist that
        could go stale)."""
        import app.certification_evidence as evidence_mod

        name = row["check_name"]
        provider_id = row["provider_id"]
        source_id = row["source_id"]
        account_route = row["account_route"]
        result = None
        if name == "connection":
            result = evidence_mod.connection_check(self, source_id=source_id)
        elif name == "historical_retrieval":
            result = evidence_mod.historical_retrieval_check(self, provider_id=provider_id)
        elif name == "parser":
            result = evidence_mod.parser_check(self, provider_id=provider_id)
        elif name == "duplicate_handling":
            result = evidence_mod.duplicate_handling_check(self, provider_id=provider_id)
        elif name == "cross_channel_correlation":
            result = evidence_mod.cross_channel_correlation_check(self, provider_id=provider_id)
        elif name == "paper_execution":
            result = evidence_mod.paper_execution_check(self, provider_id=provider_id, account_route=account_route)
        if result is not None:
            row["status"] = result.status.value
            row["evidence"] = result.evidence
            row["computed"] = True  # marks this value as freshly computed, not a stored attestation
            if result.detail:
                row["detail"] = result.detail

    def record_certification_check(
        self,
        *,
        provider_id: str,
        source_id: str,
        asset_class: str,
        account_route: str,
        check_name: str,
        status: str,
        evidence: dict | None,
        checked_by: str,
    ) -> dict:
        """The ONE write path for a manual/attestation check record --
        owner-gated at the REST layer (`POST /provider-certification/
        checks/{check_id}/record`, `Depends(require_owner)`). Refuses
        (raises `app.certification.CertificationError`) to record an
        AUTOMATED check this way -- those are computed fresh at read
        time and can never be hand-set, which is what makes "never
        auto-pass" AND "never let a human override real evidence with a
        rubber stamp" both true at once. See `app.certification.
        validate_check_record` for the evidence/checked_by requirement
        this enforces before anything is persisted."""
        name = parse_check_name(check_name)
        if CHECK_KIND.get(name) == CheckKind.AUTOMATED:
            raise CertificationError(
                f"{name.value!r} is an AUTOMATED check (computed fresh from real evidence at read time) -- "
                "it cannot be manually recorded; see app/certification_evidence.py for what it reads"
            )
        validate_scope(
            provider_id=provider_id, source_id=source_id, asset_class=asset_class, account_route=account_route
        )
        validate_check_record(check_name=check_name, status=status, evidence=evidence, checked_by=checked_by)
        self.ensure_certification_checks(
            provider_id=provider_id, source_id=source_id, asset_class=asset_class, account_route=account_route
        )
        row_id = f"{provider_id}:{source_id}:{asset_class}:{account_route}:{name.value}"
        now = now_utc().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE certification_checks
                   SET status = ?, evidence = ?, checked_at = ?, checked_by = ?, updated_at = ?
                   WHERE id = ?""",
                (status, json.dumps(evidence or {}), now, checked_by, now, row_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no certification check row for id={row_id!r}")
            row = conn.execute(
                f"SELECT {self._CERTIFICATION_CHECK_COLUMNS} FROM certification_checks WHERE id = ?", (row_id,)
            ).fetchone()
        return self._certification_check_row_to_dict(row)

    def is_scope_live_eligible(
        self, *, provider_id: str, source_id: str, asset_class: str, account_route: str
    ) -> dict:
        """The derived LIVE_ELIGIBLE view for one scope -- always freshly
        computed from `list_certification_checks`' own current rows
        (which itself recomputes every automated check fresh -- see that
        method's docstring), never read from a stored flag (there is
        none -- see `app.certification.is_live_eligible`'s own
        docstring)."""
        checks = self.ensure_certification_checks(
            provider_id=provider_id, source_id=source_id, asset_class=asset_class, account_route=account_route
        )
        eligible, missing = is_live_eligible(checks)
        return {
            "provider_id": provider_id,
            "source_id": source_id,
            "asset_class": asset_class,
            "account_route": account_route,
            "live_eligible": eligible,
            "missing_checks": [c.value for c in missing],
            "checks": checks,
        }

    def record_shadow_mode_result(self, row: dict) -> dict:
        """Persists ONE `app.shadow_mode.ShadowOrderIntent` (already
        shaped by `app.shadow_mode.to_result_row`) -- append-only audit
        trail, never updated/replaced in place, matching
        `phone_escalation_attempts`'/`signal_correlation_evidence`'s own
        "always record, never silently skip" convention for a decision
        ledger."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO shadow_mode_results
                       (id, signal_id, provider_id, account_id, symbol, side, quantity, expected_entry,
                        stop_price, targets, policy_reference, reasoning, computed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["signal_id"],
                    row["provider_id"],
                    row["account_id"],
                    row["symbol"],
                    row["side"],
                    row.get("quantity"),
                    row.get("expected_entry"),
                    row.get("stop_price"),
                    json.dumps(row.get("targets") or []),
                    row["policy_reference"],
                    row.get("reasoning", ""),
                    row["computed_at"],
                ),
            )
        return row

    def list_shadow_mode_results(self, *, provider_id: str | None = None, signal_id: str | None = None) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if provider_id is not None:
            clauses.append("provider_id = ?")
            params.append(provider_id)
        if signal_id is not None:
            clauses.append("signal_id = ?")
            params.append(signal_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, signal_id, provider_id, account_id, symbol, side, quantity, expected_entry, "
                f"stop_price, targets, policy_reference, reasoning, computed_at FROM shadow_mode_results "
                f"{where} ORDER BY computed_at DESC",
                params,
            ).fetchall()
        return [
            {
                "id": r[0],
                "signal_id": r[1],
                "provider_id": r[2],
                "account_id": r[3],
                "symbol": r[4],
                "side": r[5],
                "quantity": r[6],
                "expected_entry": r[7],
                "stop_price": r[8],
                "targets": json.loads(r[9]) if r[9] else [],
                "policy_reference": r[10],
                "reasoning": r[11],
                "computed_at": r[12],
            }
            for r in rows
        ]

    # -- Track 15: sample-driven parser tooling (app/parser_tooling.py) --
    # `parser_samples` / `parser_profiles` CRUD. See that module's own
    # docstring for the full data model and lifecycle; this is minimal
    # persistence for it, same "no full UI, REST/store CRUD only" build-
    # order decision Track 14 made for its own catalog tables.

    _PARSER_SAMPLE_COLUMNS = (
        "id, source_id, provider_id, raw_text, message_type, extracted_fields, disposition_outcome, "
        "is_corrected, corrected_message_type, corrected_fields, correction_note, corrected_at, "
        "created_at, updated_at"
    )

    def _parser_sample_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "source_id": row[1],
            "provider_id": row[2],
            "raw_text": row[3],
            "message_type": row[4],
            "extracted_fields": json.loads(row[5]) if row[5] else {},
            "disposition_outcome": row[6],
            "is_corrected": bool(row[7]),
            "corrected_message_type": row[8],
            "corrected_fields": json.loads(row[9]) if row[9] else None,
            "correction_note": row[10],
            "corrected_at": row[11],
            "created_at": row[12],
            "updated_at": row[13],
        }

    def save_parser_sample(
        self,
        *,
        sample_id: str,
        source_id: str,
        provider_id: str | None,
        raw_text: str,
        message_type: str | None,
        extracted_fields: dict,
        disposition_outcome: str | None,
    ) -> dict:
        """Insert (or idempotently re-describe) one `parser_samples` row
        -- the persisted record of one historical message's automatic
        classification/extraction. Re-saving the same `sample_id`
        replaces the automatic classification fields but NEVER touches
        an existing correction (`is_corrected`/`corrected_*`) -- once an
        owner has corrected a sample, re-running batch-classify over the
        same source must not silently discard that ground truth."""
        now = now_utc().isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM parser_samples WHERE id = ?", (sample_id,)
            ).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """INSERT INTO parser_samples
                       (id, source_id, provider_id, raw_text, message_type, extracted_fields,
                        disposition_outcome, is_corrected, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       source_id = excluded.source_id,
                       provider_id = excluded.provider_id,
                       raw_text = excluded.raw_text,
                       message_type = excluded.message_type,
                       extracted_fields = excluded.extracted_fields,
                       disposition_outcome = excluded.disposition_outcome,
                       updated_at = excluded.updated_at""",
                (
                    sample_id,
                    source_id,
                    provider_id,
                    raw_text,
                    message_type,
                    json.dumps(extracted_fields),
                    disposition_outcome,
                    created_at,
                    now,
                ),
            )
        return self.get_parser_sample(sample_id)  # type: ignore[return-value]

    def get_parser_sample(self, sample_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._PARSER_SAMPLE_COLUMNS} FROM parser_samples WHERE id = ?", (sample_id,)
            ).fetchone()
        return self._parser_sample_row_to_dict(row) if row else None

    def list_parser_samples(self, *, source_id: str | None = None, is_corrected: bool | None = None) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if is_corrected is not None:
            clauses.append("is_corrected = ?")
            params.append(1 if is_corrected else 0)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._PARSER_SAMPLE_COLUMNS} FROM parser_samples {where} ORDER BY created_at", params
            ).fetchall()
        return [self._parser_sample_row_to_dict(r) for r in rows]

    def correct_parser_sample(
        self,
        sample_id: str,
        *,
        corrected_message_type: str | None,
        corrected_fields: dict,
        correction_note: str | None = None,
    ) -> dict:
        """Records an owner correction for one sample -- per the user's
        own spec, this correction becomes a TEST CASE for that provider's
        parser (a corrected `parser_samples` row IS the test case; a
        future accuracy computation reads every `is_corrected=1` row for
        a provider's sources as its ground-truth set), not merely an edit
        of the one message. The ORIGINAL automatic classification/
        extraction (`message_type`/`extracted_fields`) is left untouched
        so the two can always be compared -- what the parser guessed vs.
        what the owner confirmed was actually true."""
        existing = self.get_parser_sample(sample_id)
        if existing is None:
            raise KeyError(f"no parser sample registered with id={sample_id!r}")
        now = now_utc().isoformat()
        with self._connect() as conn:
            conn.execute(
                """UPDATE parser_samples
                   SET is_corrected = 1,
                       corrected_message_type = ?,
                       corrected_fields = ?,
                       correction_note = ?,
                       corrected_at = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    corrected_message_type,
                    json.dumps(corrected_fields),
                    correction_note,
                    now,
                    now,
                    sample_id,
                ),
            )
        return self.get_parser_sample(sample_id)  # type: ignore[return-value]

    _PARSER_PROFILE_COLUMNS = (
        "id, provider_id, version, status, sample_count, test_count, accuracy_metrics, "
        "supported_message_types, fallback_model, prompt_version, schema_version, notes, "
        "created_at, updated_at, activated_at, retired_at"
    )

    def _parser_profile_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "provider_id": row[1],
            "version": row[2],
            "status": row[3],
            "sample_count": row[4],
            "test_count": row[5],
            "accuracy_metrics": json.loads(row[6]) if row[6] else {},
            "supported_message_types": json.loads(row[7]) if row[7] else [],
            "fallback_model": row[8],
            "prompt_version": row[9],
            "schema_version": row[10],
            "notes": row[11],
            "created_at": row[12],
            "updated_at": row[13],
            "activated_at": row[14],
            "retired_at": row[15],
        }

    def register_parser_profile(
        self,
        *,
        parser_id: str,
        provider_id: str,
        version: str,
        supported_message_types: list[str] | None = None,
        fallback_model: str = "none",
        prompt_version: str | None = None,
        schema_version: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Registers a brand-new parser profile, ALWAYS starting at
        `ParserProfileStatus.DRAFT` -- never left to the caller to
        choose a starting status (see app.parser_tooling.Parser
        ProfileStatus's own docstring). `provider_id` must already be a
        registered `providers` row (application-enforced, same
        convention as `register_source`)."""
        validate_profile_registration(parser_id=parser_id, provider_id=provider_id, version=version)
        message_types = validate_supported_message_types(supported_message_types)
        now = now_utc().isoformat()
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM providers WHERE id = ?", (provider_id,)).fetchone() is None:
                raise KeyError(f"no provider registered with id={provider_id!r}")
            if conn.execute("SELECT 1 FROM parser_profiles WHERE id = ?", (parser_id,)).fetchone() is not None:
                raise ParserToolingError(f"a parser profile already exists with id={parser_id!r}")
            conn.execute(
                """INSERT INTO parser_profiles
                       (id, provider_id, version, status, sample_count, test_count, accuracy_metrics,
                        supported_message_types, fallback_model, prompt_version, schema_version, notes,
                        created_at, updated_at)
                   VALUES (?, ?, ?, 'draft', 0, 0, '{}', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    parser_id,
                    provider_id,
                    version,
                    json.dumps(message_types),
                    fallback_model,
                    prompt_version,
                    schema_version,
                    notes,
                    now,
                    now,
                ),
            )
        return self.get_parser_profile(parser_id)  # type: ignore[return-value]

    def get_parser_profile(self, parser_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._PARSER_PROFILE_COLUMNS} FROM parser_profiles WHERE id = ?", (parser_id,)
            ).fetchone()
        return self._parser_profile_row_to_dict(row) if row else None

    def list_parser_profiles(self, *, provider_id: str | None = None, status: str | None = None) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if provider_id is not None:
            clauses.append("provider_id = ?")
            params.append(provider_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._PARSER_PROFILE_COLUMNS} FROM parser_profiles {where} "
                "ORDER BY provider_id, created_at",
                params,
            ).fetchall()
        return [self._parser_profile_row_to_dict(r) for r in rows]

    def get_active_parser_profile_for_source(self, source_id: str) -> dict | None:
        """Resolves `sources.parser_profile` (Track 14's column) to its
        `parser_profiles` row, when set and that profile is genuinely
        ACTIVE -- `None` for a source with no assignment, an assignment
        pointing at a profile that isn't (or is no longer) ACTIVE, or a
        source that doesn't exist. NOT consulted by the live signal-
        handling path -- see app/parser_tooling.py's own docstring for
        why that wiring is deliberately left as follow-up work."""
        source = self.get_source(source_id)
        if source is None or not source.get("parser_profile"):
            return None
        profile = self.get_parser_profile(source["parser_profile"])
        if profile is None or profile["status"] != ParserProfileStatus.ACTIVE.value:
            return None
        return profile

    def update_parser_profile_metrics(
        self,
        parser_id: str,
        *,
        sample_count: int | None = None,
        test_count: int | None = None,
        accuracy_metrics: dict | None = None,
    ) -> dict:
        """Updates only the metrics fields given -- called after a batch
        of samples is persisted/corrected for this profile's provider,
        never guessed or fabricated (see app/parser_tooling.py's own
        docstring's hard rule on accuracy numbers)."""
        existing = self.get_parser_profile(parser_id)
        if existing is None:
            raise KeyError(f"no parser profile registered with id={parser_id!r}")
        now = now_utc().isoformat()
        with self._connect() as conn:
            conn.execute(
                """UPDATE parser_profiles
                   SET sample_count = COALESCE(?, sample_count),
                       test_count = COALESCE(?, test_count),
                       accuracy_metrics = COALESCE(?, accuracy_metrics),
                       updated_at = ?
                   WHERE id = ?""",
                (
                    sample_count,
                    test_count,
                    json.dumps(accuracy_metrics) if accuracy_metrics is not None else None,
                    now,
                    parser_id,
                ),
            )
        return self.get_parser_profile(parser_id)  # type: ignore[return-value]

    def promote_parser_profile(self, parser_id: str, target_status: str) -> dict:
        """The ONE place a `parser_profiles` row's `status` changes --
        always an explicit, owner-gated call (`app/main.py`'s `POST
        /parser-tooling/parser-profiles/{parser_id}/promote`, behind
        `Depends(require_owner)`), never automatic. Validates the
        transition with `app.parser_tooling.validate_profile_transition`
        (raises `ParserToolingError` for a skipped state) BEFORE touching
        the database. Promoting to ACTIVE atomically demotes this
        provider's previous ACTIVE profile (if any, and if it isn't this
        same row) to RETIRED in the SAME transaction -- at most one
        ACTIVE profile per provider at a time, enforced here AND by the
        partial-unique index on `parser_profiles(provider_id) WHERE
        status = 'active'` as defense in depth. The previous ACTIVE
        profile's `sample_count`/`test_count`/`accuracy_metrics` are left
        exactly as they were -- only `status`/`retired_at` change -- so
        its past behavior stays fully inspectable (see app/parser_
        tooling.py's own docstring)."""
        existing = self.get_parser_profile(parser_id)
        if existing is None:
            raise KeyError(f"no parser profile registered with id={parser_id!r}")
        current = ParserProfileStatus(existing["status"])
        target = ParserProfileStatus(target_status)
        validate_profile_transition(current, target)
        now = now_utc().isoformat()
        with self._connect() as conn:
            if target is ParserProfileStatus.ACTIVE:
                previous_active = conn.execute(
                    "SELECT id FROM parser_profiles WHERE provider_id = ? AND status = 'active' AND id != ?",
                    (existing["provider_id"], parser_id),
                ).fetchall()
                for (previous_id,) in previous_active:
                    conn.execute(
                        "UPDATE parser_profiles SET status = 'retired', retired_at = ?, updated_at = ? WHERE id = ?",
                        (now, now, previous_id),
                    )
                conn.execute(
                    "UPDATE parser_profiles SET status = ?, activated_at = ?, updated_at = ? WHERE id = ?",
                    (target.value, now, now, parser_id),
                )
            elif target is ParserProfileStatus.RETIRED:
                conn.execute(
                    "UPDATE parser_profiles SET status = ?, retired_at = ?, updated_at = ? WHERE id = ?",
                    (target.value, now, now, parser_id),
                )
            else:
                conn.execute(
                    "UPDATE parser_profiles SET status = ?, updated_at = ? WHERE id = ?",
                    (target.value, now, parser_id),
                )
        return self.get_parser_profile(parser_id)  # type: ignore[return-value]

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
        """Insert (or, idempotently, re-describe) one collector row --
        now a thin wrapper over the unified `collectors` table's generic
        `register_collector` (see `app/unified_collectors.py`). Field
        validation (bad `connection_mode`/`allowed_uses`, a
        `credential_env_var` that looks like a secret value rather than a
        name) is still enforced by
        `app.telegram_collectors.validate_registration` BEFORE anything
        is written -- unchanged.

        Re-registering the SAME `collector_id` replaces its identity/
        connection fields but preserves its qualification evidence,
        checkpoint, health state, AND `noforwards` (a provider_config
        sub-field, explicitly re-merged in below since
        `register_collector` always replaces `provider_config` as a
        whole) -- an operator re-describing which env var/chat a
        collector reads from must not silently reset "this collector was
        already confirmed receiving real messages"."""
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
        existing = self.get_collector(collector_id)
        preserved_noforwards = (existing or {}).get("provider_config", {}).get("noforwards")
        self.register_collector(
            collector_id=collector_id,
            kind="telegram",
            provider=mode.value,
            identity_ref=identity_ref,
            credential_env_var=credential_env_var,
            provider_name=provider_name,
            allowed_uses=uses,
            provider_config={
                "chat_id": str(chat_id),
                "topic_id": str(topic_id) if topic_id is not None else None,
                "noforwards": preserved_noforwards,
            },
        )
        return self.get_telegram_collector(collector_id)  # type: ignore[return-value]

    def _telegram_collector_row_to_dict(self, row: dict) -> dict:
        cfg = row["provider_config"]
        noforwards = cfg.get("noforwards")
        return {
            "id": row["id"],
            "connection_mode": row["provider"],
            "identity_ref": row["identity_ref"],
            "credential_env_var": row["credential_env_var"],
            "chat_id": cfg.get("chat_id"),
            "topic_id": cfg.get("topic_id"),
            "provider_name": row["provider_name"],
            "allowed_uses": row["allowed_uses"],
            "noforwards": bool(noforwards) if noforwards is not None else None,
            "last_qualified_at": row["last_qualified_at"],
            "qualification_evidence": row["qualification_evidence"],
            "checkpoint_message_id": row["checkpoint"],
            "checkpoint_updated_at": row["checkpoint_updated_at"],
            "health_state": row["health_state"],
            "health_detail": row["health_detail"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_telegram_collector(self, collector_id: str) -> dict | None:
        row = self.get_collector(collector_id)
        if row is None or row["kind"] != "telegram":
            return None
        return self._telegram_collector_row_to_dict(row)

    def list_telegram_collectors(self) -> list[dict]:
        return [self._telegram_collector_row_to_dict(r) for r in self.list_collectors(kind="telegram")]

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
        try:
            self.update_collector_health(collector_id, state.value, detail=detail)
        except KeyError:
            raise KeyError(f"no telegram collector registered with id={collector_id!r}") from None

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

        health = CollectorHealth.PROTECTED_CONTENT_RESTRICTED if noforwards else CollectorHealth.HEALTHY_QUALIFIED
        if noforwards is not None:
            self.merge_collector_provider_config(collector_id, {"noforwards": bool(noforwards)})
        try:
            self.record_collector_qualification_evidence(
                collector_id, evidence=evidence, health_state=health.value, qualified_at=qualified_at
            )
        except KeyError:
            raise KeyError(f"no telegram collector registered with id={collector_id!r}") from None

    def get_telegram_collector_checkpoint(self, collector_id: str) -> int | None:
        """Point 7: the last message id this collector has admitted to
        LIVE routing -- `None` for a collector that has never processed a
        live message (including one that has only ever gone through a
        historical import, which never touches this column -- see
        `advance_telegram_collector_checkpoint`)."""
        try:
            return self.get_collector_checkpoint(collector_id)  # type: ignore[return-value]
        except KeyError:
            raise KeyError(f"no telegram collector registered with id={collector_id!r}") from None

    def advance_telegram_collector_checkpoint(self, collector_id: str, message_id: int) -> None:
        """Point 7: called ONLY after a message has been genuinely
        admitted to live routing (never for a historical-import row, and
        never for a message this collector is merely re-observing at or
        below its current checkpoint). Monotonic -- never moves the
        checkpoint backward, so an out-of-order redelivery can't un-admit
        messages that were already caught up to (enforced here, in Python,
        since the unified table's checkpoint is an opaque JSON scalar --
        see `SignalStore.advance_collector_checkpoint`'s own docstring)."""
        try:
            current = self.get_collector_checkpoint(collector_id)
        except KeyError:
            raise KeyError(f"no telegram collector registered with id={collector_id!r}") from None
        new_value = max(current, message_id) if current is not None else message_id
        self.advance_collector_checkpoint(collector_id, new_value)

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
        """Insert (or, idempotently, re-describe) one collector row --
        now a thin wrapper over the unified `collectors` table. Mirrors
        `register_telegram_collector`'s own contract exactly: validation
        happens BEFORE anything is written
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
        self.register_collector(
            collector_id=collector_id,
            kind="pull",
            provider=provider_enum.value,
            identity_ref=identity_ref,
            credential_env_var=credential_env_var,
            provider_name=provider_name,
            allowed_uses=uses,
            provider_config={
                "auth_mode": auth_mode,
                "target_id": str(target_id),
                "target_label": target_label,
            },
        )
        return self.get_pull_collector(collector_id)  # type: ignore[return-value]

    def _pull_collector_row_to_dict(self, row: dict) -> dict:
        cfg = row["provider_config"]
        return {
            "id": row["id"],
            "provider": row["provider"],
            "auth_mode": cfg.get("auth_mode"),
            "identity_ref": row["identity_ref"],
            "credential_env_var": row["credential_env_var"],
            "target_id": cfg.get("target_id"),
            "target_label": cfg.get("target_label"),
            "provider_name": row["provider_name"],
            "allowed_uses": row["allowed_uses"],
            "last_qualified_at": row["last_qualified_at"],
            "qualification_evidence": row["qualification_evidence"],
            "checkpoint": row["checkpoint"],
            "checkpoint_updated_at": row["checkpoint_updated_at"],
            "health_state": row["health_state"],
            "health_detail": row["health_detail"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_pull_collector(self, collector_id: str) -> dict | None:
        row = self.get_collector(collector_id)
        if row is None or row["kind"] != "pull":
            return None
        return self._pull_collector_row_to_dict(row)

    def list_pull_collectors(self, provider: str | None = None) -> list[dict]:
        rows = self.list_collectors(kind="pull", provider=provider)
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
        try:
            self.update_collector_health(collector_id, state.value, detail=detail)
        except KeyError:
            raise KeyError(f"no pull collector registered with id={collector_id!r}") from None

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

        try:
            self.record_collector_qualification_evidence(
                collector_id,
                evidence=evidence,
                health_state=CollectorHealth.HEALTHY_QUALIFIED.value,
                qualified_at=qualified_at,
            )
        except KeyError:
            raise KeyError(f"no pull collector registered with id={collector_id!r}") from None

    def get_pull_collector_checkpoint(self, collector_id: str) -> str | None:
        """The last message/tweet id this collector has admitted to LIVE
        routing -- `None` for a collector that has never processed one
        (including one that has only gone through a historical import,
        which never touches this column)."""
        try:
            return self.get_collector_checkpoint(collector_id)  # type: ignore[return-value]
        except KeyError:
            raise KeyError(f"no pull collector registered with id={collector_id!r}") from None

    def advance_pull_collector_checkpoint(self, collector_id: str, checkpoint: str) -> None:
        """Called ONLY after a message/tweet has genuinely been admitted
        to live routing. Unlike `advance_telegram_collector_checkpoint`,
        this does not enforce monotonicity itself (`checkpoint` is an
        opaque string value here -- see `app/collector_registry.py`'s
        module docstring) -- each adapter's own `_admits_live`-style check
        is the actual source of truth for "is this genuinely newer,"
        exactly like the value it's about to pass in was already
        compared before this is called."""
        try:
            self.advance_collector_checkpoint(collector_id, str(checkpoint))
        except KeyError:
            raise KeyError(f"no pull collector registered with id={collector_id!r}") from None

    # -- Track 10: notification-bridge device registry (app/notification_bridge.py) --

    def register_notification_bridge_device(
        self,
        *,
        device_id: str,
        pairing_token_hash: str,
        app_packages: list[str],
        provider_mapping: dict[str, dict] | None = None,
    ) -> dict:
        """Insert (or, idempotently, re-describe) one device row.
        Validation is enforced by `app.notification_bridge.
        validate_device_registration` BEFORE anything is written.

        Re-registering the SAME `device_id` replaces its identity/
        authorization fields (app_packages, provider_mapping) AND its
        `pairing_token_hash` (an operator re-pairing the same device_id
        after losing the original token, or rotating it deliberately),
        but preserves `last_heartbeat_at`, `recent_completeness`, and
        `health_state` -- same "re-describing must not silently reset
        already-observed evidence" precedent as
        `register_telegram_collector`."""
        from app.notification_bridge import validate_device_registration

        validate_device_registration(device_id=device_id, app_packages=app_packages, provider_mapping=provider_mapping)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM notification_bridge_devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """INSERT INTO notification_bridge_devices
                       (device_id, pairing_token_hash, app_packages, provider_mapping,
                        recent_completeness, health_state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, '[]', 'never_paired', ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET
                       pairing_token_hash = excluded.pairing_token_hash,
                       app_packages = excluded.app_packages,
                       provider_mapping = excluded.provider_mapping,
                       updated_at = excluded.updated_at""",
                (
                    device_id,
                    pairing_token_hash,
                    json.dumps(app_packages),
                    json.dumps(provider_mapping or {}),
                    created_at,
                    now,
                ),
            )
        return self.get_notification_bridge_device(device_id)  # type: ignore[return-value]

    def update_notification_bridge_device_metadata(
        self,
        device_id: str,
        *,
        device_name: str | None = None,
        platform: str | None = None,
        model: str | None = None,
        os_version: str | None = None,
        agent_version: str | None = None,
        network_status: str | None = None,
        battery_level: int | None = None,
        is_charging: bool | None = None,
        notification_permission_granted: bool | None = None,
        accessibility_permission_granted: bool | None = None,
        screen_control_capability: bool | None = None,
        ai_agent_capability: bool | None = None,
    ) -> dict:
        """Track 20: merges whatever subset of device-reported metadata
        the caller actually provides (any `None` kwarg here means "this
        caller didn't report this field on THIS call" and is left
        UNTOUCHED, never written as a NULL that would erase a previously
        reported value -- see `app/main.py`'s ingest route, which calls
        this with only the fields the Android app's `device_metadata`
        payload actually included). Raises `KeyError` for an unregistered
        `device_id`, same convention as every other per-device write
        here."""
        fields = {
            "device_name": device_name,
            "platform": platform,
            "model": model,
            "os_version": os_version,
            "agent_version": agent_version,
            "network_status": network_status,
            "battery_level": battery_level,
            "is_charging": None if is_charging is None else int(is_charging),
            "notification_permission_granted": (
                None if notification_permission_granted is None else int(notification_permission_granted)
            ),
            "accessibility_permission_granted": (
                None if accessibility_permission_granted is None else int(accessibility_permission_granted)
            ),
            "screen_control_capability": (
                None if screen_control_capability is None else int(screen_control_capability)
            ),
            "ai_agent_capability": None if ai_agent_capability is None else int(ai_agent_capability),
        }
        provided = {k: v for k, v in fields.items() if v is not None}
        if not provided:
            existing = self.get_notification_bridge_device(device_id)
            if existing is None:
                raise KeyError(f"no notification-bridge device registered with device_id={device_id!r}")
            return existing
        set_clause = ", ".join(f"{k} = ?" for k in provided) + ", updated_at = ?"
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE notification_bridge_devices SET {set_clause} WHERE device_id = ?",
                (*provided.values(), datetime.now(timezone.utc).isoformat(), device_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no notification-bridge device registered with device_id={device_id!r}")
        return self.get_notification_bridge_device(device_id)  # type: ignore[return-value]

    def update_notification_bridge_device_apps(
        self, device_id: str, *, device_name: str | None = None, allowed_apps: list[str] | None = None, blocked_apps: list[str] | None = None
    ) -> dict:
        """Track 20: owner-gated write for `device_name`/`allowed_apps`/
        `blocked_apps` (`PATCH /mobile-devices/{device_id}` in
        app/main.py) -- validated by `app.notification_bridge.
        validate_device_app_lists` BEFORE anything is written (never lets
        a globally-denied package be stored as "allowed" -- see that
        function's own docstring). `None` for `allowed_apps`/
        `blocked_apps` means "leave this list unchanged," matching
        `update_notification_bridge_device_metadata`'s own "untouched,
        never silently cleared" convention; pass `[]` explicitly to clear
        one."""
        from app.notification_bridge import validate_device_app_lists

        existing = self.get_notification_bridge_device(device_id)
        if existing is None:
            raise KeyError(f"no notification-bridge device registered with device_id={device_id!r}")
        new_allowed = existing["allowed_apps"] if allowed_apps is None else allowed_apps
        new_blocked = existing["blocked_apps"] if blocked_apps is None else blocked_apps
        validate_device_app_lists(allowed_apps=new_allowed, blocked_apps=new_blocked)

        fields: dict[str, Any] = {}
        if device_name is not None:
            fields["device_name"] = device_name
        if allowed_apps is not None:
            fields["allowed_apps"] = json.dumps(new_allowed)
        if blocked_apps is not None:
            fields["blocked_apps"] = json.dumps(new_blocked)
        if not fields:
            return existing
        set_clause = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?"
        with self._connect() as conn:
            conn.execute(
                f"UPDATE notification_bridge_devices SET {set_clause} WHERE device_id = ?",
                (*fields.values(), datetime.now(timezone.utc).isoformat(), device_id),
            )
        return self.get_notification_bridge_device(device_id)  # type: ignore[return-value]

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
        row -- now a thin wrapper over the unified `collectors` table.
        Same reasoning as `register_telegram_collector`: validated
        BEFORE anything is written, and re-registering the SAME
        `collector_id` preserves qualification evidence, checkpoints
        (including the seen-URL set, a provider_config sub-field
        explicitly re-merged in below), and health state."""
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
        existing = self.get_collector(collector_id)
        preserved_seen_urls = (existing or {}).get("provider_config", {}).get("checkpoint_seen_urls", [])
        self.register_collector(
            collector_id=collector_id,
            kind="website",
            provider=fmt.value,
            identity_ref=None,
            credential_env_var=None,
            provider_name=provider_name,
            allowed_uses=uses,
            provider_config={
                "site_id": site_id,
                "feed_url": feed_url,
                "article_list_url": article_list_url,
                "analyst": analyst,
                "auth_state_env_var": auth_state_env_var,
                "checkpoint_seen_urls": preserved_seen_urls,
            },
        )
        return self.get_website_collector(collector_id)  # type: ignore[return-value]

    def _website_collector_row_to_dict(self, row: dict) -> dict:
        cfg = row["provider_config"]
        return {
            "id": row["id"],
            "site_format": row["provider"],
            "site_id": cfg.get("site_id"),
            "provider_name": row["provider_name"],
            "feed_url": cfg.get("feed_url"),
            "article_list_url": cfg.get("article_list_url"),
            "analyst": cfg.get("analyst"),
            "auth_state_env_var": cfg.get("auth_state_env_var"),
            "allowed_uses": row["allowed_uses"],
            "last_qualified_at": row["last_qualified_at"],
            "qualification_evidence": row["qualification_evidence"],
            "checkpoint_article_url": row["checkpoint"],
            "checkpoint_seen_urls": cfg.get("checkpoint_seen_urls", []),
            "checkpoint_updated_at": row["checkpoint_updated_at"],
            "health_state": row["health_state"],
            "health_detail": row["health_detail"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_website_collector(self, collector_id: str) -> dict | None:
        row = self.get_collector(collector_id)
        if row is None or row["kind"] != "website":
            return None
        return self._website_collector_row_to_dict(row)

    def list_website_collectors(self) -> list[dict]:
        return [self._website_collector_row_to_dict(r) for r in self.list_collectors(kind="website")]

    def update_website_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None:
        """The ONE place a website collector's incident/health state is
        written -- validated against `app.website_collectors.
        CollectorHealth`, same as `update_telegram_collector_health`."""
        from app.website_collectors import CollectorHealth

        state = CollectorHealth(health_state)  # raises ValueError for an unrecognized state
        try:
            self.update_collector_health(collector_id, state.value, detail=detail)
        except KeyError:
            raise KeyError(f"no website collector registered with id={collector_id!r}") from None

    def record_website_collector_qualification_evidence(
        self, collector_id: str, *, evidence: dict, qualified_at: datetime | None = None
    ) -> None:
        """Real evidence that a real article was genuinely fetched and
        processed for this collector -- advances health_state to
        `healthy_qualified` (the only state a dashboard may render as
        green)."""
        from app.website_collectors import CollectorHealth

        try:
            self.record_collector_qualification_evidence(
                collector_id,
                evidence=evidence,
                health_state=CollectorHealth.HEALTHY_QUALIFIED.value,
                qualified_at=qualified_at,
            )
        except KeyError:
            raise KeyError(f"no website collector registered with id={collector_id!r}") from None

    def get_website_collector_seen_urls(self, collector_id: str) -> set[str]:
        row = self.get_collector(collector_id)
        if row is None:
            raise KeyError(f"no website collector registered with id={collector_id!r}")
        return set(row["provider_config"].get("checkpoint_seen_urls", []))

    def advance_website_collector_checkpoint(self, collector_id: str, *, article_url: str) -> None:
        """Called ONLY after an article has been genuinely admitted to
        live processing. Appends to the seen-URL set (ARTICLE_LIST mode's
        checkpoint, stored in `provider_config`) AND advances the
        unified table's generic `checkpoint` column to the latest one
        seen (FEED mode's checkpoint) -- a collector may switch
        `site_format` later, so both are kept current regardless of which
        mode is currently configured."""
        seen = self.get_website_collector_seen_urls(collector_id)
        seen.add(article_url)
        self.merge_collector_provider_config(collector_id, {"checkpoint_seen_urls": sorted(seen)})
        self.advance_collector_checkpoint(collector_id, article_url)

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

    # -- Track 7: Email collector registry (app/email_collectors.py) ------

    def register_email_collector(
        self,
        *,
        collector_id: str,
        connection_mode: str,
        identity_ref: str,
        credential_env_var: str,
        imap_host: str,
        imap_folder: str,
        sender_allowlist: list[str],
        provider_name: str,
        imap_port: int = 993,
        subject_patterns: list[str] | None = None,
        allowed_uses: list[str] | None = None,
        poll_interval_seconds: int = 60,
    ) -> dict:
        """Insert (or, idempotently, re-describe) one email collector row
        -- now a thin wrapper over the unified `collectors` table.
        Mirrors `register_telegram_collector`'s own contract exactly,
        including preserving qualification evidence/checkpoint/health
        state across a re-registration of the same `collector_id`. A
        freshly registered email collector defaults to
        `no_messages_observed` (not `unqualified`, unlike the other
        three unified kinds) -- same historical default this registry
        has always used."""
        from app.email_collectors import validate_registration

        mode, uses = validate_registration(
            collector_id=collector_id,
            connection_mode=connection_mode,
            identity_ref=identity_ref,
            credential_env_var=credential_env_var,
            imap_host=imap_host,
            imap_folder=imap_folder,
            sender_allowlist=sender_allowlist,
            provider_name=provider_name,
            allowed_uses=allowed_uses,
        )
        self.register_collector(
            collector_id=collector_id,
            kind="email",
            provider=mode.value,
            identity_ref=identity_ref,
            credential_env_var=credential_env_var,
            provider_name=provider_name,
            allowed_uses=uses,
            provider_config={
                "imap_host": imap_host,
                "imap_port": imap_port,
                "imap_folder": imap_folder,
                "sender_allowlist": list(sender_allowlist),
                "subject_patterns": list(subject_patterns or []),
                "poll_interval_seconds": poll_interval_seconds,
            },
            default_health_state="no_messages_observed",
        )
        return self.get_email_collector(collector_id)  # type: ignore[return-value]

    def _email_collector_row_to_dict(self, row: dict) -> dict:
        cfg = row["provider_config"]
        return {
            "id": row["id"],
            "connection_mode": row["provider"],
            "identity_ref": row["identity_ref"],
            "credential_env_var": row["credential_env_var"],
            "imap_host": cfg.get("imap_host"),
            "imap_port": cfg.get("imap_port"),
            "imap_folder": cfg.get("imap_folder"),
            "sender_allowlist": cfg.get("sender_allowlist", []),
            "subject_patterns": cfg.get("subject_patterns", []),
            "provider_name": row["provider_name"],
            "allowed_uses": row["allowed_uses"],
            "poll_interval_seconds": cfg.get("poll_interval_seconds"),
            "last_qualified_at": row["last_qualified_at"],
            "qualification_evidence": row["qualification_evidence"],
            "checkpoint_uid": row["checkpoint"],
            "checkpoint_updated_at": row["checkpoint_updated_at"],
            "health_state": row["health_state"],
            "health_detail": row["health_detail"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_email_collector(self, collector_id: str) -> dict | None:
        row = self.get_collector(collector_id)
        if row is None or row["kind"] != "email":
            return None
        return self._email_collector_row_to_dict(row)

    def list_email_collectors(self) -> list[dict]:
        return [self._email_collector_row_to_dict(r) for r in self.list_collectors(kind="email")]

    def update_email_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None:
        """Point 6: the ONE place an email collector's incident/health
        state is written. Validated against `app.email_collectors.
        CollectorHealth` so a caller can never persist a state string
        this registry doesn't recognize."""
        from app.email_collectors import CollectorHealth

        state = CollectorHealth(health_state)  # raises ValueError for an unrecognized state
        try:
            self.update_collector_health(collector_id, state.value, detail=detail)
        except KeyError:
            raise KeyError(f"no email collector registered with id={collector_id!r}") from None

    def record_email_collector_qualification_evidence(
        self,
        collector_id: str,
        *,
        evidence: dict,
        qualified_at: datetime | None = None,
    ) -> None:
        """Real evidence that authorized real-message receipt was
        confirmed for this collector -- mirrors
        `record_telegram_collector_qualification_evidence`'s own
        contract. Always advances `health_state` to `healthy_qualified`
        (no `noforwards`-equivalent restricted state exists for email)."""
        from app.email_collectors import CollectorHealth

        try:
            self.record_collector_qualification_evidence(
                collector_id,
                evidence=evidence,
                health_state=CollectorHealth.HEALTHY_QUALIFIED.value,
                qualified_at=qualified_at,
            )
        except KeyError:
            raise KeyError(f"no email collector registered with id={collector_id!r}") from None

    # -- Track 7: email collector checkpoint (app/sources/email_source.py) --

    def get_email_collector_checkpoint(self, collector_id: str) -> int | None:
        """Point 5: the last IMAP UID this collector has admitted to LIVE
        routing -- `None` for a collector that has never processed a live
        message (including one that has only ever gone through a
        historical import, which never touches this column -- see
        `advance_email_collector_checkpoint`)."""
        try:
            return self.get_collector_checkpoint(collector_id)  # type: ignore[return-value]
        except KeyError:
            raise KeyError(f"no email collector registered with id={collector_id!r}") from None

    def advance_email_collector_checkpoint(self, collector_id: str, uid: int) -> None:
        """Point 5: called ONLY after a message has been genuinely
        admitted to live routing. Monotonic -- never moves the checkpoint
        backward, mirrors `advance_telegram_collector_checkpoint`'s own
        `MAX(...)` guard exactly (enforced here, in Python -- see
        `SignalStore.advance_collector_checkpoint`'s own docstring)."""
        try:
            current = self.get_collector_checkpoint(collector_id)
        except KeyError:
            raise KeyError(f"no email collector registered with id={collector_id!r}") from None
        new_value = max(current, uid) if current is not None else uid
        self.advance_collector_checkpoint(collector_id, new_value)

    # -- Track 10: notification-bridge device registry (app/notification_bridge.py) --

    def _notification_bridge_device_row_to_dict(self, row: tuple) -> dict:
        from app.notification_bridge import DEFAULT_HEARTBEAT_STALE_SECONDS, DeviceHealth

        last_heartbeat_at = row[4]
        stored_health = row[6]
        effective_health = stored_health
        # Point 8: "no_heartbeat_recently" is a READ-TIME override, never
        # a value this table's own writers persist -- see this table's own
        # CREATE TABLE comment. A device that was never paired at all
        # keeps reporting `never_paired` here (that's already an honest,
        # visible non-green state); every other stored state is
        # overridden the instant the heartbeat is missing or stale.
        if stored_health != DeviceHealth.NEVER_PAIRED.value:
            if last_heartbeat_at is None:
                effective_health = DeviceHealth.NO_HEARTBEAT_RECENTLY.value
            else:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_heartbeat_at)).total_seconds()
                if age > DEFAULT_HEARTBEAT_STALE_SECONDS:
                    effective_health = DeviceHealth.NO_HEARTBEAT_RECENTLY.value
        return {
            "device_id": row[0],
            "pairing_token_hash": row[1],
            "app_packages": json.loads(row[2]) if row[2] else [],
            "provider_mapping": json.loads(row[3]) if row[3] else {},
            "last_heartbeat_at": last_heartbeat_at,
            "recent_completeness": json.loads(row[5]) if row[5] else [],
            "health_state": effective_health,
            "stored_health_state": stored_health,
            "health_detail": row[7],
            # Track 20: device-reported metadata -- honestly None/absent
            # until the device itself reports it (see
            # app.notification_bridge.NotificationBridgeDevice's own
            # docstring). `is_charging`/the four permission-and-capability
            # booleans are stored as INTEGER (0/1) or NULL -- converted
            # back to a real Python bool/None here, never coerced to
            # False for NULL.
            "device_name": row[8],
            "platform": row[9],
            "model": row[10],
            "os_version": row[11],
            "agent_version": row[12],
            "network_status": row[13],
            "battery_level": row[14],
            "is_charging": bool(row[15]) if row[15] is not None else None,
            "notification_permission_granted": bool(row[16]) if row[16] is not None else None,
            "accessibility_permission_granted": bool(row[17]) if row[17] is not None else None,
            "screen_control_capability": bool(row[18]) if row[18] is not None else None,
            "ai_agent_capability": bool(row[19]) if row[19] is not None else None,
            "allowed_apps": json.loads(row[20]) if row[20] else [],
            "blocked_apps": json.loads(row[21]) if row[21] else [],
            "created_at": row[22],
            "updated_at": row[23],
        }

    _NOTIFICATION_BRIDGE_DEVICE_COLUMNS = (
        "device_id, pairing_token_hash, app_packages, provider_mapping, last_heartbeat_at, "
        "recent_completeness, health_state, health_detail, "
        "device_name, platform, model, os_version, agent_version, network_status, battery_level, is_charging, "
        "notification_permission_granted, accessibility_permission_granted, screen_control_capability, "
        "ai_agent_capability, allowed_apps, blocked_apps, created_at, updated_at"
    )

    def get_notification_bridge_device(self, device_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._NOTIFICATION_BRIDGE_DEVICE_COLUMNS} FROM notification_bridge_devices "
                "WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return self._notification_bridge_device_row_to_dict(row) if row else None

    def list_notification_bridge_devices(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._NOTIFICATION_BRIDGE_DEVICE_COLUMNS} FROM notification_bridge_devices "
                "ORDER BY device_id"
            ).fetchall()
        return [self._notification_bridge_device_row_to_dict(r) for r in rows]

    def update_notification_bridge_device_health(
        self, device_id: str, health_state: str, *, detail: str | None = None
    ) -> None:
        """Point 8: the ONE place a device's stored incident/health state
        is written (the `no_heartbeat_recently` override happens
        separately, at read time -- see `_notification_bridge_device_row_to_dict`).
        Validated against `app.notification_bridge.DeviceHealth` so a
        caller can never persist a state string this registry doesn't
        recognize."""
        from app.notification_bridge import DeviceHealth

        state = DeviceHealth(health_state)  # raises ValueError for an unrecognized state
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE notification_bridge_devices SET health_state = ?, health_detail = ?, updated_at = ? "
                "WHERE device_id = ?",
                (state.value, detail, datetime.now(timezone.utc).isoformat(), device_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no notification-bridge device registered with device_id={device_id!r}")

    def record_notification_bridge_heartbeat(self, device_id: str) -> None:
        """Called on every authenticated contact from the device (both the
        Android app's dedicated periodic heartbeat AND every notification
        upload -- any successful auth'd contact is real evidence the
        device is reachable). Promotes a device that has NEVER received a
        heartbeat before straight from `NEVER_PAIRED` to
        `NO_NOTIFICATIONS_OBSERVED` (the app/pairing is confirmed
        working; no notification content has been recorded yet) -- never
        touches a health state beyond that (a heartbeat alone is not
        evidence of real notification receipt, so it must never advance a
        device to `HEALTHY_QUALIFIED` by itself)."""
        from app.notification_bridge import DeviceHealth

        with self._connect() as conn:
            row = conn.execute(
                "SELECT health_state FROM notification_bridge_devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no notification-bridge device registered with device_id={device_id!r}")
            now = datetime.now(timezone.utc).isoformat()
            new_health = row[0]
            if row[0] == DeviceHealth.NEVER_PAIRED.value:
                new_health = DeviceHealth.NO_NOTIFICATIONS_OBSERVED.value
            conn.execute(
                "UPDATE notification_bridge_devices SET last_heartbeat_at = ?, health_state = ?, updated_at = ? "
                "WHERE device_id = ?",
                (now, new_health, now, device_id),
            )

    def record_notification_bridge_completeness(self, device_id: str, *, complete: bool) -> bool:
        """Point 8 (`CONTENT_COMPLETENESS_DEGRADED`): append `complete` to
        this device's rolling `recent_completeness` window (capped to
        `app.notification_bridge.COMPLETENESS_WINDOW_SIZE`, oldest
        dropped first), then return whether the window now shows a real
        degraded pattern -- true only once at least
        `COMPLETENESS_MIN_SAMPLE` events are in the window AND the
        incomplete fraction is >= `COMPLETENESS_DEGRADED_RATIO`. A single
        truncated notification is ordinary provider noise, never itself a
        degraded verdict -- see this module's own docstring for why a
        sustained pattern, not one bad sample, is what this state means.
        Does NOT itself write `health_state` -- the caller (app/main.py)
        decides what to do with the returned verdict, same separation as
        every other health-affecting write in this registry."""
        from app.notification_bridge import COMPLETENESS_DEGRADED_RATIO, COMPLETENESS_MIN_SAMPLE, COMPLETENESS_WINDOW_SIZE

        with self._connect() as conn:
            row = conn.execute(
                "SELECT recent_completeness FROM notification_bridge_devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no notification-bridge device registered with device_id={device_id!r}")
            window: list[bool] = json.loads(row[0]) if row[0] else []
            window.append(bool(complete))
            window = window[-COMPLETENESS_WINDOW_SIZE:]
            conn.execute(
                "UPDATE notification_bridge_devices SET recent_completeness = ?, updated_at = ? WHERE device_id = ?",
                (json.dumps(window), datetime.now(timezone.utc).isoformat(), device_id),
            )
        if len(window) < COMPLETENESS_MIN_SAMPLE:
            return False
        incomplete_fraction = sum(1 for c in window if not c) / len(window)
        return incomplete_fraction >= COMPLETENESS_DEGRADED_RATIO

    def find_notification_bridge_event(self, device_id: str, notification_key: str) -> dict | None:
        """The most recent event already recorded for this exact
        (device_id, notification_key) -- Track 10's own analogue of
        `find_signal_id_by_provider_identity`, used to tell an exact
        content-hash duplicate/retry from a genuine Android notification
        UPDATE (a different hash for the same key -- see
        `app.notification_bridge.content_fingerprint`'s own docstring)."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, device_id, app_package, notification_key, content_hash, revision_seq,
                          content_completeness, posted_at, received_at, classification, signal_id, created_at,
                          needs_escalation, escalation_status
                   FROM notification_bridge_events
                   WHERE device_id = ? AND notification_key = ?
                   ORDER BY revision_seq DESC LIMIT 1""",
                (device_id, notification_key),
            ).fetchone()
        if row is None:
            return None
        return self._notification_bridge_event_row_to_dict(row)

    def save_notification_bridge_event(
        self,
        *,
        device_id: str,
        app_package: str,
        notification_key: str,
        content_hash: str,
        revision_seq: int,
        content_completeness: str,
        posted_at: datetime | None,
        received_at: datetime,
        classification: str,
        signal_id: str | None,
        needs_escalation: bool = False,
    ) -> str:
        """`needs_escalation` (Track 12): true for any event whose
        `content_completeness` (app.notification_bridge.
        ContentCompleteness) isn't COMPLETE -- see this table's own
        CREATE TABLE comment for the escalation_status state machine
        this seeds (`"pending"` here, `None` for an event that was never
        flagged at all)."""
        event_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO notification_bridge_events
                       (id, device_id, app_package, notification_key, content_hash, revision_seq,
                        content_completeness, posted_at, received_at, classification, signal_id, created_at,
                        needs_escalation, escalation_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    device_id,
                    app_package,
                    notification_key,
                    content_hash,
                    revision_seq,
                    content_completeness,
                    posted_at.isoformat() if posted_at else None,
                    received_at.isoformat(),
                    classification,
                    signal_id,
                    datetime.now(timezone.utc).isoformat(),
                    1 if needs_escalation else 0,
                    "pending" if needs_escalation else None,
                ),
            )
        return event_id

    def list_notification_bridge_events(self, device_id: str) -> list[dict]:
        """Every event recorded for one device, most recent first -- a
        dashboard/audit read, never used for dedup itself (see
        `find_notification_bridge_event`)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, device_id, app_package, notification_key, content_hash, revision_seq,
                          content_completeness, posted_at, received_at, classification, signal_id, created_at,
                          needs_escalation, escalation_status
                   FROM notification_bridge_events WHERE device_id = ? ORDER BY created_at DESC""",
                (device_id,),
            ).fetchall()
        return [self._notification_bridge_event_row_to_dict(r) for r in rows]

    @staticmethod
    def _notification_bridge_event_row_to_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "device_id": row[1],
            "app_package": row[2],
            "notification_key": row[3],
            "content_hash": row[4],
            "revision_seq": row[5],
            "content_completeness": row[6],
            "posted_at": row[7],
            "received_at": row[8],
            "classification": row[9],
            "signal_id": row[10],
            "created_at": row[11],
            "needs_escalation": bool(row[12]),
            "escalation_status": row[13],
        }

    def list_notification_bridge_events_needing_escalation(self, device_id: str | None = None) -> list[dict]:
        """Track 12/Track 13 interface: every notification-bridge event
        still awaiting escalation -- `needs_escalation = 1 AND
        escalation_status = 'pending'` -- optionally scoped to one
        device. THIS is the read Track 13's active AI phone-retrieval
        escalation layer polls (or is woken by) to find PARTIAL/
        POINTER_ONLY/TRUNCATED/UNKNOWN captures that a human/AI-assisted
        follow-up (e.g. calling the provider, opening the source app)
        might be able to fill in -- this codebase does not attempt that
        retrieval itself; it only classifies and flags (see
        app.notification_bridge.classify_notification_completeness and
        app/main.py's `_process_notification_bridge_event`).

        Once Track 13 (or an owner, manually) has resolved one of these
        -- successfully recovered the full content and, if it now
        parses, produced a real Signal, OR determined it genuinely
        can't be recovered -- it calls
        `resolve_notification_bridge_event_escalation` to close it out;
        this read never returns an event a second time once resolved."""
        with self._connect() as conn:
            if device_id is not None:
                rows = conn.execute(
                    """SELECT id, device_id, app_package, notification_key, content_hash, revision_seq,
                              content_completeness, posted_at, received_at, classification, signal_id, created_at,
                              needs_escalation, escalation_status
                       FROM notification_bridge_events
                       WHERE needs_escalation = 1 AND escalation_status = 'pending' AND device_id = ?
                       ORDER BY created_at ASC""",
                    (device_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, device_id, app_package, notification_key, content_hash, revision_seq,
                              content_completeness, posted_at, received_at, classification, signal_id, created_at,
                              needs_escalation, escalation_status
                       FROM notification_bridge_events
                       WHERE needs_escalation = 1 AND escalation_status = 'pending'
                       ORDER BY created_at ASC"""
                ).fetchall()
        return [self._notification_bridge_event_row_to_dict(r) for r in rows]

    def resolve_notification_bridge_event_escalation(
        self, event_id: str, *, status: str, resolved_signal_id: str | None = None
    ) -> None:
        """Track 12/Track 13 interface (the write half -- see `list_
        notification_bridge_events_needing_escalation`'s own docstring).
        `status` is caller-owned vocabulary (same "the calling module
        owns this string" convention as `record_signal_correlation_
        evidence`'s `match_type`) -- Track 13 is expected to use
        `"resolved"` (content was recovered) or `"failed"` (recovery was
        attempted and genuinely could not succeed), never silently
        reusing `"pending"`. `resolved_signal_id`, when given, overwrites
        this event's own `signal_id` -- the real Signal Track 13's
        recovered content produced, if any (a resolution that recovered
        no parseable trade at all, e.g. it turned out to be a closed
        position update already handled elsewhere, passes `None` and
        leaves `signal_id` as whatever it already was)."""
        with self._connect() as conn:
            if resolved_signal_id is not None:
                cur = conn.execute(
                    "UPDATE notification_bridge_events SET escalation_status = ?, signal_id = ? WHERE id = ?",
                    (status, resolved_signal_id, event_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE notification_bridge_events SET escalation_status = ? WHERE id = ?",
                    (status, event_id),
                )
            if cur.rowcount == 0:
                raise KeyError(f"no notification-bridge event with id={event_id!r}")

    # -- Track 13: phone-escalation config registry (app/phone_escalation.py) --

    _PHONE_ESCALATION_CONFIG_COLUMNS = (
        "id, app_package, provider_name, adapter_backend, capability_state, notes, created_at, updated_at"
    )

    def _phone_escalation_config_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "app_package": row[1],
            "provider_name": row[2],
            "adapter_backend": row[3],
            "capability_state": row[4],
            "notes": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }

    def register_phone_escalation_config(
        self,
        *,
        app_package: str,
        provider_name: str,
        adapter_backend: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Insert (or, idempotently, re-describe) one provider's active-
        retrieval config. Validated BEFORE anything is written by
        `app.phone_escalation.validate_config_registration`, which
        (deliberately) takes no `capability_state` argument at all -- a
        FRESH row is always inserted at `capability_state='disabled'`; an
        already-registered `app_package` re-registering here updates its
        `provider_name`/`adapter_backend`/`notes` but NEVER its
        `capability_state` (re-describing a provider must never silently
        reset -- or silently preserve past a config change -- an
        operator's own prior promotion decision; use
        `set_phone_escalation_capability_state` explicitly for that)."""
        from app.phone_escalation import validate_config_registration

        validate_config_registration(app_package=app_package, provider_name=provider_name)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM phone_escalation_configs WHERE app_package = ?", (app_package,)
            ).fetchone()
            config_id = existing[0] if existing else str(uuid.uuid4())
            created_at = existing[1] if existing else now
            conn.execute(
                """INSERT INTO phone_escalation_configs
                       (id, app_package, provider_name, adapter_backend, capability_state, notes,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'disabled', ?, ?, ?)
                   ON CONFLICT(app_package) DO UPDATE SET
                       provider_name = excluded.provider_name,
                       adapter_backend = excluded.adapter_backend,
                       notes = excluded.notes,
                       updated_at = excluded.updated_at""",
                (config_id, app_package, provider_name, adapter_backend, notes, created_at, now),
            )
        return self.get_phone_escalation_config(app_package)  # type: ignore[return-value]

    def get_phone_escalation_config(self, app_package: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._PHONE_ESCALATION_CONFIG_COLUMNS} FROM phone_escalation_configs "
                "WHERE app_package = ?",
                (app_package,),
            ).fetchone()
        return self._phone_escalation_config_row_to_dict(row) if row else None

    def list_phone_escalation_configs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._PHONE_ESCALATION_CONFIG_COLUMNS} FROM phone_escalation_configs ORDER BY app_package"
            ).fetchall()
        return [self._phone_escalation_config_row_to_dict(r) for r in rows]

    def set_phone_escalation_capability_state(self, app_package: str, target_state: str) -> dict:
        """The ONE owner-gated write path that ever changes a provider's
        `capability_state` -- validated against
        `app.phone_escalation.validate_state_transition`'s DISABLED ->
        SHADOW -> ENABLED promotion policy (raises
        `app.phone_escalation.PhoneEscalationError` for a disallowed
        jump, e.g. straight from DISABLED to ENABLED). The HTTP route
        that calls this (`POST /phone-escalation/configs/{app_package}/
        promote` in app/main.py) is itself behind
        `Depends(require_owner)` -- the same owner-session gate every
        other owner-action-card in this codebase uses."""
        from app.phone_escalation import CapabilityState, validate_state_transition

        current = self.get_phone_escalation_config(app_package)
        if current is None:
            raise KeyError(f"no phone-escalation config registered with app_package={app_package!r}")
        current_state = CapabilityState(current["capability_state"])
        target = CapabilityState(target_state)  # raises ValueError for an unrecognized state
        validate_state_transition(current_state, target)
        with self._connect() as conn:
            conn.execute(
                "UPDATE phone_escalation_configs SET capability_state = ?, updated_at = ? WHERE app_package = ?",
                (target.value, datetime.now(timezone.utc).isoformat(), app_package),
            )
        return self.get_phone_escalation_config(app_package)  # type: ignore[return-value]

    def record_phone_escalation_attempt(
        self,
        *,
        device_id: str,
        app_package: str,
        notification_key: str,
        content_hash: str,
        capability_state_at_attempt: str,
        disposition: str,
        extraction_status: str | None = None,
        extraction_detail: str | None = None,
        signal_id: str | None = None,
    ) -> str:
        """Always called after `app.phone_escalation.evaluate_escalation`
        returns, REGARDLESS of disposition -- see that function's own
        docstring for why every decision (not only ones where retrieval
        actually ran) is recorded here."""
        attempt_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO phone_escalation_attempts
                       (id, device_id, app_package, notification_key, content_hash,
                        capability_state_at_attempt, disposition, extraction_status, extraction_detail,
                        signal_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt_id,
                    device_id,
                    app_package,
                    notification_key,
                    content_hash,
                    capability_state_at_attempt,
                    disposition,
                    extraction_status,
                    extraction_detail,
                    signal_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return attempt_id

    def list_phone_escalation_attempts(self, *, device_id: str | None = None) -> list[dict]:
        """Every recorded escalation decision, most recent first --
        optionally scoped to one device (a dashboard/audit read, never
        used for dedup)."""
        query = (
            "SELECT id, device_id, app_package, notification_key, content_hash, capability_state_at_attempt, "
            "disposition, extraction_status, extraction_detail, signal_id, created_at "
            "FROM phone_escalation_attempts"
        )
        params: tuple = ()
        if device_id is not None:
            query += " WHERE device_id = ?"
            params = (device_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "device_id": r[1],
                "app_package": r[2],
                "notification_key": r[3],
                "content_hash": r[4],
                "capability_state_at_attempt": r[5],
                "disposition": r[6],
                "extraction_status": r[7],
                "extraction_detail": r[8],
                "signal_id": r[9],
                "created_at": r[10],
            }
            for r in rows
        ]

    # -- Track 20: per-(device, app_package) mobile app configuration
    # (app/notification_bridge.py's MobileAppConfig) --

    _MOBILE_APP_CONFIG_COLUMNS = (
        "id, device_id, package_name, display_name, capture_notifications, active_retrieval_allowed, "
        "retrieval_mode, notification_title_patterns, conversation_patterns, expected_screens, "
        "navigation_recipe, ai_fallback_allowed, max_navigation_steps, timeout_seconds, screenshot_retention, "
        "content_extraction_schema, created_at, updated_at"
    )

    def _mobile_app_config_row_to_dict(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "device_id": row[1],
            "package_name": row[2],
            "display_name": row[3],
            "capture_notifications": bool(row[4]),
            "active_retrieval_allowed": bool(row[5]),
            "retrieval_mode": row[6],
            "notification_title_patterns": json.loads(row[7]) if row[7] else [],
            "conversation_patterns": json.loads(row[8]) if row[8] else [],
            "expected_screens": json.loads(row[9]) if row[9] else [],
            "navigation_recipe": json.loads(row[10]) if row[10] else [],
            "ai_fallback_allowed": bool(row[11]),
            "max_navigation_steps": row[12],
            "timeout_seconds": row[13],
            "screenshot_retention": row[14],
            "content_extraction_schema": json.loads(row[15]) if row[15] else {},
            "created_at": row[16],
            "updated_at": row[17],
        }

    def register_mobile_app_config(
        self,
        *,
        device_id: str,
        package_name: str,
        display_name: str | None = None,
        capture_notifications: bool = True,
        active_retrieval_allowed: bool = False,
        retrieval_mode: str = "notification_only",
        notification_title_patterns: list[str] | None = None,
        conversation_patterns: list[str] | None = None,
        expected_screens: list[str] | None = None,
        navigation_recipe: list[dict] | None = None,
        ai_fallback_allowed: bool = False,
        max_navigation_steps: int = 10,
        timeout_seconds: int = 30,
        screenshot_retention: str = "none",
        content_extraction_schema: dict | None = None,
    ) -> dict:
        """Insert (or, idempotently, re-describe) one `(device_id,
        package_name)` config row -- see
        `app.notification_bridge.validate_mobile_app_config` for the
        validation applied BEFORE anything is written (in particular:
        `package_name` must already be in the owning device's own
        `app_packages`). Re-registering the SAME `(device_id,
        package_name)` replaces every field -- unlike the device/provider
        registries above, there is no partial "observed evidence" state
        on this row worth preserving across a re-describe."""
        from app.notification_bridge import validate_mobile_app_config

        device = self.get_notification_bridge_device(device_id)
        if device is None:
            raise KeyError(f"no notification-bridge device registered with device_id={device_id!r}")
        validate_mobile_app_config(
            device_app_packages=device["app_packages"],
            package_name=package_name,
            retrieval_mode=retrieval_mode,
            max_navigation_steps=max_navigation_steps,
            timeout_seconds=timeout_seconds,
            screenshot_retention=screenshot_retention,
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM mobile_app_configs WHERE device_id = ? AND package_name = ?",
                (device_id, package_name),
            ).fetchone()
            config_id = existing[0] if existing else str(uuid.uuid4())
            created_at = existing[1] if existing else now
            conn.execute(
                """INSERT INTO mobile_app_configs
                       (id, device_id, package_name, display_name, capture_notifications,
                        active_retrieval_allowed, retrieval_mode, notification_title_patterns,
                        conversation_patterns, expected_screens, navigation_recipe, ai_fallback_allowed,
                        max_navigation_steps, timeout_seconds, screenshot_retention, content_extraction_schema,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id, package_name) DO UPDATE SET
                       display_name = excluded.display_name,
                       capture_notifications = excluded.capture_notifications,
                       active_retrieval_allowed = excluded.active_retrieval_allowed,
                       retrieval_mode = excluded.retrieval_mode,
                       notification_title_patterns = excluded.notification_title_patterns,
                       conversation_patterns = excluded.conversation_patterns,
                       expected_screens = excluded.expected_screens,
                       navigation_recipe = excluded.navigation_recipe,
                       ai_fallback_allowed = excluded.ai_fallback_allowed,
                       max_navigation_steps = excluded.max_navigation_steps,
                       timeout_seconds = excluded.timeout_seconds,
                       screenshot_retention = excluded.screenshot_retention,
                       content_extraction_schema = excluded.content_extraction_schema,
                       updated_at = excluded.updated_at""",
                (
                    config_id,
                    device_id,
                    package_name,
                    display_name,
                    int(capture_notifications),
                    int(active_retrieval_allowed),
                    retrieval_mode,
                    json.dumps(notification_title_patterns or []),
                    json.dumps(conversation_patterns or []),
                    json.dumps(expected_screens or []),
                    json.dumps(navigation_recipe or []),
                    int(ai_fallback_allowed),
                    max_navigation_steps,
                    timeout_seconds,
                    screenshot_retention,
                    json.dumps(content_extraction_schema or {}),
                    created_at,
                    now,
                ),
            )
        return self.get_mobile_app_config(device_id, package_name)  # type: ignore[return-value]

    def get_mobile_app_config(self, device_id: str, package_name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._MOBILE_APP_CONFIG_COLUMNS} FROM mobile_app_configs "
                "WHERE device_id = ? AND package_name = ?",
                (device_id, package_name),
            ).fetchone()
        return self._mobile_app_config_row_to_dict(row) if row else None

    def list_mobile_app_configs(self, device_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._MOBILE_APP_CONFIG_COLUMNS} FROM mobile_app_configs "
                "WHERE device_id = ? ORDER BY package_name",
                (device_id,),
            ).fetchall()
        return [self._mobile_app_config_row_to_dict(r) for r in rows]

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

    def list_accounts_with_fills_for_source(self, source: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT o.account_id FROM orders o JOIN signals s ON o.signal_id = s.id "
                "LEFT JOIN signals fs ON fs.id = o.family_id "
                "WHERE (o.status = 'filled' OR o.filled_quantity > 0) AND COALESCE(fs.source, s.source) = ?",
                (source,),
            ).fetchall()
        return [r[0] for r in rows]

    def list_filled_orders_chronological(self, account_id: str, source: str | None = None) -> list[dict]:
        """Every order with an actual fill (status='filled' or filled_quantity > 0)
        for this account, oldest first -- the replay
        order app/economics.py needs to reconstruct realized P&L via
        average-cost lot accounting. Unlike `list_recent_orders`, this has
        no LIMIT: a P&L computation that silently dropped older fills would
        misstate cost basis and realized gains, not just show fewer rows."""
        query = """SELECT o.id, o.account_id, o.broker, o.symbol, o.side, o.requested_quantity, o.signal_id,
                          o.status, o.broker_order_id, o.filled_quantity, o.filled_price, o.message, o.executed_at
                   FROM orders o {join} WHERE o.account_id = ? AND (o.status = 'filled' OR o.filled_quantity > 0) {extra}
                   ORDER BY o.executed_at ASC, o.id ASC"""
        params: list[Any] = [account_id]
        if source is None:
            query = query.format(join="", extra="")
        else:
            # A lifecycle- or operator-initiated exit carries a synthetic
            # source ("lifecycle_manager", "manual_exit"); its economic owner
            # is the entry signal named by `family_id`.
            query = query.format(
                join="JOIN signals s ON o.signal_id = s.id LEFT JOIN signals fs ON fs.id = o.family_id",
                extra="AND COALESCE(fs.source, s.source) = ?",
            )
            params.append(source)
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
            }
            for r in rows
        ]

    def list_filled_orders_with_signal_timing(self, account_id: str) -> list[dict]:
        """Every order with an actual fill (status='filled' or filled_quantity > 0) for this account joined to its originating
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
                   WHERE o.account_id = ? AND (o.status = 'filled' OR o.filled_quantity > 0)
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
                   WHERE o.account_id = ? AND (o.status = 'filled' OR o.filled_quantity > 0)
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

    def get_oldest_entry_signal_id(self, account_id: str, symbol: str, closing_side: str) -> str | None:
        """WP-26/E-06: Find the oldest FILLED entry signal for a plain account close.

        For a close order, find the oldest FILLED entry order (opposite side from the
        close) so we can match them via FIFO for episode grouping. The closing_side
        parameter is the side of the close order (e.g., 'sell' to close a long).

        Returns the signal_id of the oldest entry, or None if no entry found."""
        # Determine the entry side (opposite of closing side)
        entry_side = "buy" if closing_side == "sell" else "sell"

        query = """SELECT o.signal_id
                   FROM orders o
                   WHERE o.account_id = ? AND o.symbol = ? AND o.side = ? AND o.status = 'filled'
                   ORDER BY o.executed_at ASC, o.id ASC
                   LIMIT 1"""

        with self._connect() as conn:
            row = conn.execute(query, (account_id, symbol, entry_side)).fetchone()

        return row[0] if row else None

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

    def persist_margin_call_alert(
        self,
        account_id: str,
        current_equity: float,
        maintenance_requirement: float,
        excess_margin: float,
        broker: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO margin_call_alerts
                   (account_id, current_equity, maintenance_requirement, excess_margin, broker, alert_time, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    account_id,
                    current_equity,
                    maintenance_requirement,
                    excess_margin,
                    broker,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def get_unresolved_margin_calls(self, account_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, account_id, current_equity, maintenance_requirement, excess_margin, broker, alert_time
                   FROM margin_call_alerts
                   WHERE account_id = ? AND resolved = 0
                   ORDER BY alert_time DESC""",
                (account_id,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "account_id": r[1],
                "current_equity": r[2],
                "maintenance_requirement": r[3],
                "excess_margin": r[4],
                "broker": r[5],
                "alert_time": r[6],
            }
            for r in rows
        ]

    def resolve_margin_call_alert(self, alert_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE margin_call_alerts
                   SET resolved = 1, resolved_at = ?
                   WHERE id = ?""",
                (datetime.now(timezone.utc).isoformat(), alert_id),
            )

    def persist_alert(
        self,
        kind: str,
        account_id: str | None,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Record an alert to the alerts table.

        Args:
            kind: Alert type (e.g., "protection_deficit", "loss_halt")
            account_id: Account UUID, or None for system-level alerts
            message: Human-readable description
            payload: Structured data (dict), stored as JSON

        Returns:
            Alert ID (UUID string)
        """
        import uuid

        alert_id = str(uuid.uuid4())
        payload_json = json.dumps(payload) if payload else None
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO alerts
                   (id, kind, account_id, message, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (alert_id, kind, account_id, message, payload_json, now),
            )
        return alert_id

    def list_alerts(
        self, *, unacknowledged: bool = False, account_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        """List alerts, optionally filtered.

        Args:
            unacknowledged: If True, only return alerts where acknowledged_at IS NULL
            account_id: If provided, filter by account
            limit: Maximum rows to return

        Returns:
            List of alert dicts with id, kind, account_id, message, payload (parsed),
            acknowledged_at, created_at
        """
        query = "SELECT id, kind, account_id, message, payload, acknowledged_at, created_at FROM alerts WHERE 1=1"
        params: list[Any] = []

        if unacknowledged:
            query += " AND acknowledged_at IS NULL"
        if account_id is not None:
            query += " AND account_id = ?"
            params.append(account_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "id": r[0],
                "kind": r[1],
                "account_id": r[2],
                "message": r[3],
                "payload": json.loads(r[4]) if r[4] else None,
                "acknowledged_at": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Mark an alert as acknowledged.

        Args:
            alert_id: Alert UUID

        Returns:
            True if the alert was updated, False if not found
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE alerts SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
                (now, alert_id),
            )
        return cur.rowcount > 0

