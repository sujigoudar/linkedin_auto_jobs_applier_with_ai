"""Add stop_target_events table (PU-A4, real append-only stop/target
lifecycle event log)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-29

See app/db.py's own stop_target_events table comment for what this is and
app/lifecycle/models.py's StopTargetEventType for exactly which event
types exist (and which catalog-requested ones -- breakeven, trailing
activation -- are a documented gap on this branch rather than a
fabricated event type). Written by app/lifecycle/manager.py at the exact
call sites where each real state change happens, and read via
app/main.py's `GET /positions/{account_id}/{symbol}/stop-events`. A fresh
database gets this straight from app/db.py's SCHEMA string (SignalStore's
own bootstrap re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a
pre-existing database picks up the new table too) -- same "this file is
not the real source of truth for a SignalStore-created database"
precedent as 0001-0006's own docstrings.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stop_target_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("at", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("previous_price", sa.Float(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_stop_target_events_account_symbol_at",
        "stop_target_events",
        ["account_id", "symbol", "at"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
