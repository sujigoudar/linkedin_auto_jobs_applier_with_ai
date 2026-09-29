"""Add capital_reservations table (P0-4, durable provisional reservations)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29

See app/db.py's own `capital_reservations` SCHEMA comment for what this is
and app/capital_allocator.py's module docstring for why it exists: a
restart must not assume "no in-flight admissions to lose" -- a remote
broker can accept an order before this process dies, and this table is
what lets a freshly-constructed `CapitalAllocator` reload exactly the
reservations a crash left unresolved instead of starting from a clean
slate. A fresh database gets this straight from app/db.py's SCHEMA string
(SignalStore's own bootstrap re-runs `CREATE TABLE IF NOT EXISTS` on every
open, so a pre-existing database picks up the new table too) -- same "this
file is not the real source of truth for a SignalStore-created database"
precedent as every earlier revision's own docstring.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capital_reservations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("notional", sa.Float(), nullable=False),
        sa.Column("signal_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_capital_reservations_account_unresolved",
        "capital_reservations",
        ["account_id"],
        sqlite_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
