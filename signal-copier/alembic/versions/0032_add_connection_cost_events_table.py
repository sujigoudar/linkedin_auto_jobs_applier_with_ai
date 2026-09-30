"""Add the connection_cost_events table (Track 19 -- see
app/db.py's own SCHEMA comment and SignalStore.record_connection_cost_event/
get_connection_cost_summary).

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-30

Purely additive -- no existing table is dropped or altered, same
precedent as every earlier revision in this directory. No backfill:
nothing in this codebase before this track recorded a connection cost
event, so this table starts genuinely empty for an existing database,
exactly as it does for a fresh one.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connection_cost_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("connection_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ai_calls", sa.Integer(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("browser_minutes", sa.Float(), nullable=True),
        sa.Column("mobile_agent_calls", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("idx_connection_cost_events_connection_id", "connection_cost_events", ["connection_id"])
    op.create_index(
        "idx_connection_cost_events_occurred_at", "connection_cost_events", ["connection_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_connection_cost_events_occurred_at", table_name="connection_cost_events")
    op.drop_index("idx_connection_cost_events_connection_id", table_name="connection_cost_events")
    op.drop_table("connection_cost_events")
