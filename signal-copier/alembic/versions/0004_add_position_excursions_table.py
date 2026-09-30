"""Add position_excursions table (PU-A1, real MAE/MFE tracking per closed
position)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28

See app/db.py's own position_excursions table comment for what this is and
app/lifecycle/manager.py's `_persist_closed_excursion`/
`SignalStore.record_position_excursion`/`list_position_excursions` for how
it's written and read. A fresh database gets this straight from
app/db.py's SCHEMA string (SignalStore's own bootstrap re-runs `CREATE
TABLE IF NOT EXISTS` on every open, so a pre-existing database picks up
the new table too) -- same "this file is not the real source of truth for
a SignalStore-created database" precedent as 0001/0002/0003's own
docstrings.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_excursions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("highest_price_since_entry", sa.Float(), nullable=True),
        sa.Column("highest_price_at", sa.Text(), nullable=True),
        sa.Column("lowest_price_since_entry", sa.Float(), nullable=True),
        sa.Column("lowest_price_at", sa.Text(), nullable=True),
        sa.Column("mae", sa.Float(), nullable=True),
        sa.Column("mfe", sa.Float(), nullable=True),
        sa.Column("has_price_data", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("closed_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_position_excursions_account_symbol", "position_excursions", ["account_id", "symbol"]
    )
    op.create_index("idx_position_excursions_closed_at", "position_excursions", ["closed_at"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
