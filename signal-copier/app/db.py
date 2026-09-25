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
"""


class SignalStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self._connect() as conn:
            conn.executescript(SCHEMA)

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
                   (id, source, symbol, side, asset_class, quantity, price, received_at, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.id,
                    signal.source,
                    signal.symbol,
                    signal.side.value,
                    signal.asset_class.value,
                    signal.quantity,
                    signal.price,
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
    ) -> int:
        """Persist an order result and return its row id.

        `broker`/`symbol`/`side`/`requested_quantity` are what was actually
        sent to the broker for this order (not just the original signal —
        for a resolved close, `side` is the opposing buy/sell, not
        Side.CLOSE). app/reconciliation.py needs these to re-check and
        correct a PENDING order's tracked position later.
        """
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
                    result.filled_quantity,
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

    def record_fill(self, account_id: str, symbol: str, side: Side, quantity: float) -> float:
        """Update the tracked position after a buy/sell and return the new net quantity.

        A `side` of BUY adds `quantity`, SELL subtracts it. Never called with
        CLOSE — the engine resolves a close into the opposing BUY/SELL before
        this is reached (see app/engine.py).
        """
        delta = quantity if side == Side.BUY else -quantity
        return self.adjust_position(account_id, symbol, delta)

    def adjust_position(self, account_id: str, symbol: str, delta: float) -> float:
        """Apply a raw signed adjustment to a tracked position and return the new net
        quantity. Used directly by app/reconciliation.py to correct an optimistic fill
        (e.g. reverse it if the order actually got rejected, or true it up to the real
        filled quantity)."""
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
