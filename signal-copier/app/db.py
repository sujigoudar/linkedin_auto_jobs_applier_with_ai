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

from app.models import OrderResult, Side, Signal

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
    managed_lifecycle INTEGER NOT NULL DEFAULT 0
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

-- Server-side owner sessions (see app/auth.py). `session_id` is the opaque
-- value carried in the session cookie; `csrf_token` is returned once at
-- login and must be echoed back as the X-CSRF-Token header on every
-- mutating request (double-submit defense). Deleting a row revokes that
-- session immediately (logout, or an owner-initiated "sign out everywhere").
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- Recent financial-command results, keyed by an idempotency key the caller
-- supplies (see app/main.py's close/flatten endpoints): a retried or
-- duplicated request with the same key replays the stored result instead
-- of executing the command again.
CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_executed_at ON orders (executed_at);
CREATE INDEX IF NOT EXISTS idx_orders_account_id ON orders (account_id);
CREATE INDEX IF NOT EXISTS idx_signals_received_at ON signals (received_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at);
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

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
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
    ) -> int:
        """Persist an order result and return its row id.

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
                    broker_order_id, filled_quantity, filled_price, message, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ),
            )
            return cursor.lastrowid

    def list_pending_orders(self) -> list[dict]:
        """Orders still PENDING with a broker_order_id to re-check (see
        app/reconciliation.py)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, account_id, broker, symbol, side, requested_quantity,
                          filled_quantity, broker_order_id
                   FROM orders WHERE status = 'pending' AND broker_order_id IS NOT NULL"""
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
            }
            for r in rows
        ]

    def update_order_status(self, order_row_id: int, result: OrderResult) -> None:
        with self._connect() as conn:
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
                """SELECT id, source, symbol, side, asset_class, quantity, price, received_at
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

    # --- Live-editable config: accounts, routing rules, providers/analysts ---
    # See app/main.py's CRUD endpoints and app/routing.py's/app/providers.py's
    # `*_from_store` loaders — this is what makes account/routing/provider
    # changes take effect immediately, no YAML edit or restart required.

    def list_config_accounts(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT account_id, broker, multiplier, fixed_quantity, symbol_map, enabled,
                          managed_lifecycle FROM config_accounts ORDER BY account_id"""
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
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO config_accounts
                   (account_id, broker, multiplier, fixed_quantity, symbol_map, enabled, managed_lifecycle)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (account_id) DO UPDATE SET
                     broker = excluded.broker, multiplier = excluded.multiplier,
                     fixed_quantity = excluded.fixed_quantity, symbol_map = excluded.symbol_map,
                     enabled = excluded.enabled, managed_lifecycle = excluded.managed_lifecycle""",
                (
                    account_id,
                    broker,
                    multiplier,
                    fixed_quantity,
                    json.dumps(symbol_map or {}),
                    int(enabled),
                    int(managed_lifecycle),
                ),
            )

    def delete_config_account(self, account_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM config_accounts WHERE account_id = ?", (account_id,))

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

    def create_session(self, session_id: str, csrf_token: str, expires_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (session_id, csrf_token, datetime.now(timezone.utc).isoformat(), expires_at.isoformat()),
            )

    def get_session(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, csrf_token, expires_at FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return {"session_id": row[0], "csrf_token": row[1], "expires_at": row[2]}

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

    def save_idempotent_response(self, idempotency_key: str, response: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO idempotency_records (idempotency_key, response_json, created_at)
                   VALUES (?, ?, ?)""",
                (idempotency_key, json.dumps(response), datetime.now(timezone.utc).isoformat()),
            )
