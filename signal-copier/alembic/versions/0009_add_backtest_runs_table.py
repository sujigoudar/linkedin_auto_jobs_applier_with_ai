"""Add backtest_runs table (TR-15, real persistence for a completed
POST /backtest replay)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29

See app/db.py's own backtest_runs table comment for exactly what each real
column holds (config_hash, the real request/summary/trades, and optional
cost-stress fields) and app/main.py's `run_backtest`/`compute_backtest_
config_hash` for how it's written, and `GET /backtest/runs` / `GET
/backtest/runs/{id}` for how it's read. A fresh database gets this
straight from app/db.py's SCHEMA string (SignalStore's own bootstrap
re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a pre-existing
database picks up the new table too) -- same "this file is not the real
source of truth for a SignalStore-created database" precedent as every
earlier numbered revision's own docstring.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("trades_json", sa.Text(), nullable=False),
        sa.Column("stressed_summary_json", sa.Text(), nullable=True),
        sa.Column("cost_stress_note", sa.Text(), nullable=True),
    )
    op.create_index("idx_backtest_runs_created_at", "backtest_runs", ["created_at"])
    op.create_index("idx_backtest_runs_config_hash", "backtest_runs", ["config_hash"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
