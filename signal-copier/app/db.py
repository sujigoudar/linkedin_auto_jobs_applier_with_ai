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
    account_id TEXT NOT NULL,
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

    def save_order_result(self, result: OrderResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO orders
                   (account_id, signal_id, status, broker_order_id, filled_quantity,
                    filled_price, message, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.account_id,
                    result.signal_id,
                    result.status.value,
                    result.broker_order_id,
                    result.filled_quantity,
                    result.filled_price,
                    result.message,
                    result.executed_at.isoformat(),
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
