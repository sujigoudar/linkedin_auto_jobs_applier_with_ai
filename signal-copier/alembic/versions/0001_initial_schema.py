"""Initial schema snapshot (C03, bounded)

Revision ID: 0001
Revises:
Create Date: 2026-09-26

This is NOT how this database actually got here for any existing
deployment: every database this app has ever created got its schema from
app/db.py's SCHEMA executescript + the (now-frozen) _COLUMN_MIGRATIONS
list, never from this file. This revision exists so `alembic upgrade
head` against a genuinely empty database (someone provisioning a fresh
one purely through the Alembic CLI, bypassing SignalStore entirely)
produces the exact same schema SignalStore's own bootstrap does -- but
only when combined with every later revision (0002-0015), the same way
every *actual* SignalStore-created database gets there through SCHEMA
plus _COLUMN_MIGRATIONS, not from SCHEMA alone.

Like every revision after it, this is a FROZEN, independent DDL snapshot,
not a live mirror of app/db.py's current SCHEMA string. It captures the
schema exactly as it stood the moment this revision was cut -- i.e.
app/db.py's current SCHEMA string with every column and table added by a
later revision (0002's `orders.reserved_notional`, 0003's `export_events`,
0004's `position_excursions`, 0005's `orders.submitted_at` /
`orders.protection_confirmed_at`, 0006's `account_equity_snapshots`,
0007's `stop_target_events`, 0008's `orders.purpose` / `orders.family_id`,
0009's `backtest_runs`, 0010's `backtest_runs.capital_contention_json`,
0011's `capital_reservations`, 0012's `orders.confirmed_cumulative_fill` /
`applied_execution_delta` / `outstanding_possible_fill`, 0013's
`route_qualifications`, 0014's `command_ledger`, and 0015's
`writer_lease`) subtracted back out. Importing the live `SCHEMA` string
here would re-introduce every one of those later columns/tables at this
revision, making every subsequent `create_table`/`add_column` revision
collide with something already there -- exactly the bug this snapshot
fixes; see this project's migration-review notes for the reproduction.

Any schema change from here on should be a NEW revision (`alembic
revision -m "..."`) with a real, reviewed upgrade() -- not another
addition to _COLUMN_MIGRATIONS, which stops growing as of this commit,
and never another edit to this file's frozen DDL.
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Frozen as of revision 0001 -- see this module's docstring for exactly
# what was subtracted from app/db.py's current SCHEMA string to arrive at
# this snapshot, and why. Never edit this to match a later SCHEMA change;
# add a new revision instead.
_SCHEMA_0001 = """
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

CREATE TABLE IF NOT EXISTS positions (
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    net_quantity REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol)
);

CREATE TABLE IF NOT EXISTS lifecycle_state (
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol)
);

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
    management_recipe TEXT,
    qualification_level TEXT,
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

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    credential_epoch TEXT
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS close_claims (
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol)
);

CREATE TABLE IF NOT EXISTS seed_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    seeded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    screen TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saved_views_screen ON saved_views (screen);

CREATE INDEX IF NOT EXISTS idx_orders_executed_at ON orders (executed_at);
CREATE INDEX IF NOT EXISTS idx_orders_account_id ON orders (account_id);
CREATE INDEX IF NOT EXISTS idx_signals_received_at ON signals (received_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at);
"""


def upgrade() -> None:
    op.get_bind().connection.executescript(_SCHEMA_0001)


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade is not supported for the initial schema -- see this project's other "
        "one-way-migration precedent (app/db.py's _COLUMN_MIGRATIONS never had a downgrade path either)"
    )
