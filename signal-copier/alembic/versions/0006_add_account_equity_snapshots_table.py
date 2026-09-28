"""Add account_equity_snapshots table (PU-A3, real periodic equity/P&L
snapshots per account)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28

See app/db.py's own account_equity_snapshots table comment for what this
is and app/equity_history.py's EquitySnapshotter for how it's written and
app/main.py's `GET /accounts/{account_id}/equity-history` for how it's
read. A fresh database gets this straight from app/db.py's SCHEMA string
(SignalStore's own bootstrap re-runs `CREATE TABLE IF NOT EXISTS` on every
open, so a pre-existing database picks up the new table too) -- same "this
file is not the real source of truth for a SignalStore-created database"
precedent as 0001/0002/0003/0004/0005's own docstrings.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_equity_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.Text(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False),
        sa.Column("cumulative_pnl", sa.Float(), nullable=False),
        sa.Column("unpriced_open_symbols", sa.Text(), nullable=False, server_default="[]"),
    )
    op.create_index(
        "idx_account_equity_snapshots_account_captured",
        "account_equity_snapshots",
        ["account_id", "captured_at"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
