"""Add idx_orders_signal_id and idx_orders_status (disclosed performance
gap: orders.signal_id had no index despite being a FOREIGN KEY target
enforced at runtime via `PRAGMA foreign_keys = ON`, joined in
`list_pending_orders`/`list_filled_orders_with_signal_chronological`/
`list_filled_orders_with_signal_timing`, and filtered directly in
`list_orders_for_signal` -- `WHERE signal_id = ?`, called on EVERY
incoming signal for SIG-01's dedup check, a genuine hot path that would
otherwise degrade to a full table scan as `orders` grows. `orders.status`
had no standalone index either, used bare in `list_pending_orders`'s
`WHERE o.status = 'pending'` and only partially served by the existing
`idx_orders_account_id` prefix in `list_filled_orders_chronological`'s
`WHERE account_id = ? AND status = 'filled'`.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-29

See app/db.py's own SCHEMA string for the live source of truth: a fresh
database gets both indexes straight from there (SignalStore's own
bootstrap re-runs `CREATE INDEX IF NOT EXISTS` on every open, so a
pre-existing database picks up the new indexes too) -- same "this file is
not the real source of truth for a SignalStore-created database"
precedent as every earlier revision's own docstring. This revision only
matters for someone provisioning a database purely through the Alembic
CLI, or an operator running `alembic upgrade head` by hand against an
existing deployment whose `alembic_version` table was already stamped at
an earlier revision.
"""
from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("idx_orders_signal_id", "orders", ["signal_id"])
    op.create_index("idx_orders_status", "orders", ["status"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
