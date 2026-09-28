"""Minimal SQLite persistence for signal/order history.

Kept deliberately simple (stdlib sqlite3, no ORM) since this is a scaffold —
swap for SQLAlchemy + Postgres when volume/concurrency needs it.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from alembic import command  # type: ignore[attr-defined]  # real, working import; alembic's __init__.py doesn't re-export it in a way mypy can see
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.models import OrderResult, Side, Signal
from signal_platform_contracts import EventEnvelope

_ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"


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
    -- E03 (bounded): the capital_allocator.py notional this specific order
    -- reserved, set only while its status is 'pending' and there's a real
    -- broker_order_id to poll -- see app/capital_allocator.py's "Known gap"
    -- section and app/reconciliation.py's _correct_position, the one place
    -- that releases it once this row's status is confirmed terminal. NULL
    -- for every other order (nothing to release).
    reserved_notional REAL,
    FOREIGN KEY (signal_id) REFERENCES signals (id)
);

-- Net position per (account, symbol), maintained by the engine so a
-- 'close' signal knows what to close. Positive = net long, negative = net
-- short, zero = flat. This is this service's own record of what it has
-- sent, not a live read of the broker's actual position — see
-- app/engine.py's docstring for the accuracy caveat on brokers that report
-- PENDING rather than a confirmed fill.
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
    max_notional_exposure REAL
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

CREATE INDEX IF NOT EXISTS idx_orders_executed_at ON orders (executed_at);
CREATE INDEX IF NOT EXISTS idx_orders_account_id ON orders (account_id);
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

    def save_signal(self, signal: Signal) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO signals
                   (id, source, symbol, side, asset_class, quantity, price, stop_loss, take_profit,
                    analyst, received_at, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ),
            )

    def save_order_result(
        self,
        result: OrderResult,
        *,
        broker: str | None = None,
        symbol: str | None = None,
        side: Side | None = None,
        requested_quantity: float | None = None,
        applied_quantity: float | None = None,
        reserved_notional: float | None = None,
        export_envelope: EventEnvelope | None = None,
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
        tracked position (`SignalStore.positions`) for this order, if
        anything — pass it whenever `record_fill`/`adjust_position` was
        called alongside this save. It is NOT always `result.filled_quantity`:
        a broker reporting PENDING with `filled_quantity=None` still gets an
        optimistic quantity applied to the tracked position (the requested
        quantity, per the "protect first" design), and that applied amount,
        not the broker's still-unconfirmed `None`, is what
        app/reconciliation.py's `_correct_position` must use as its baseline
        when the real fill is confirmed later — otherwise it corrects
        against a baseline of 0 and adds the confirmed quantity a second
        time on top of what was already applied (a real, confirmed bug this
        parameter exists to close). Omit this for calls that never touched
        the tracked position (REJECTED/ERROR results, or a save with no
        `symbol`/`side` at all).
        """
        stored_filled_quantity = applied_quantity if applied_quantity is not None else result.filled_quantity
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO orders
                   (account_id, broker, symbol, side, requested_quantity, signal_id, status,
                    broker_order_id, filled_quantity, filled_price, message, executed_at, reserved_notional)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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

    def update_order_status(self, order_row_id: int, result: OrderResult) -> None:
        with self._connect() as conn:
            self._update_order_status_locked(conn, order_row_id, result)

    def _update_order_status_locked(self, conn: sqlite3.Connection, order_row_id: int, result: OrderResult) -> None:
        conn.execute(
            """UPDATE orders SET status = ?, filled_quantity = ?, filled_price = ?,
                      message = ?, executed_at = ? WHERE id = ?""",
            (
                result.status.value,
                result.filled_quantity,
                result.filled_price,
                result.message,
                result.executed_at.isoformat(),
                order_row_id,
            ),
        )

    def correct_position_and_update_order_status(
        self, order_row_id: int, account_id: str, symbol: str, signed_delta: float, result: OrderResult
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
        re-processed."""
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
            self._update_order_status_locked(conn, order_row_id, result)
        return new_quantity

    def get_position(self, account_id: str, symbol: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT net_quantity FROM positions WHERE account_id = ? AND symbol = ?",
                (account_id, symbol),
            ).fetchone()
        return row[0] if row else 0.0

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
                          stop_loss, take_profit, raw
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

    # --- Live-editable config: accounts, routing rules, providers/analysts ---
    # See app/main.py's CRUD endpoints and app/routing.py's/app/providers.py's
    # `*_from_store` loaders — this is what makes account/routing/provider
    # changes take effect immediately, no YAML edit or restart required.

    def list_config_accounts(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT account_id, broker, multiplier, fixed_quantity, symbol_map, enabled,
                          managed_lifecycle, max_notional_exposure FROM config_accounts ORDER BY account_id"""
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
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO config_accounts
                   (account_id, broker, multiplier, fixed_quantity, symbol_map, enabled, managed_lifecycle,
                    max_notional_exposure)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (account_id) DO UPDATE SET
                     broker = excluded.broker, multiplier = excluded.multiplier,
                     fixed_quantity = excluded.fixed_quantity, symbol_map = excluded.symbol_map,
                     enabled = excluded.enabled, managed_lifecycle = excluded.managed_lifecycle,
                     max_notional_exposure = excluded.max_notional_exposure""",
                (
                    account_id,
                    broker,
                    multiplier,
                    fixed_quantity,
                    json.dumps(symbol_map or {}),
                    int(enabled),
                    int(managed_lifecycle),
                    max_notional_exposure,
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
        cover (in particular: a managed-lifecycle stop/target/trailing exit
        never reaches this table at all -- see that module's docstring)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT o.account_id, o.symbol, o.side, o.filled_quantity, o.filled_price, o.executed_at,
                          s.source, s.analyst, s.asset_class
                   FROM orders o
                   JOIN signals s ON s.id = o.signal_id
                   WHERE o.status = 'filled'
                   ORDER BY o.executed_at ASC, o.id ASC"""
            ).fetchall()
        return [
            {
                "account_id": r[0],
                "symbol": r[1],
                "side": r[2],
                "filled_quantity": r[3],
                "filled_price": r[4],
                "executed_at": r[5],
                "source": r[6],
                "analyst": r[7],
                "asset_class": r[8],
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
        signal's `received_at` -- app/execution_quality.py's only source
        for signal-to-fill latency. There is no separately tracked
        decision/submission/acknowledgement timestamp in this schema
        (see that module's docstring for why its own report says so
        honestly rather than inventing finer-grained stages)."""
        query = """SELECT o.symbol, o.executed_at, s.received_at
                   FROM orders o JOIN signals s ON o.signal_id = s.id
                   WHERE o.account_id = ? AND o.status = 'filled'
                   ORDER BY o.executed_at ASC"""
        with self._connect() as conn:
            rows = conn.execute(query, (account_id,)).fetchall()
        return [{"symbol": r[0], "executed_at": r[1], "received_at": r[2]} for r in rows]

    def list_recent_orders(self, limit: int = 50, account_id: str | None = None) -> list[dict]:
        query = """SELECT id, account_id, broker, symbol, side, requested_quantity, signal_id,
                          status, broker_order_id, filled_quantity, filled_price, message, executed_at
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
