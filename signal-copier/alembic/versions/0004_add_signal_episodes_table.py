"""Add signal_episodes table (SignalEpisode correlation engine, batch B)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28

See app/db.py's own signal_episodes table comment for what this is and
app/signal_episode.py's module docstring for the SignalEpisode/EpisodeCorrelator
design it backs. A fresh database gets this straight from app/db.py's
SCHEMA string (SignalStore's own bootstrap re-runs `CREATE TABLE IF NOT
EXISTS` on every open, so a pre-existing database picks up the new table
too) -- same "this file is not the real source of truth for a
SignalStore-created database" precedent as 0001/0002/0003's own
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
        "signal_episodes",
        sa.Column("episode_id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("analyst", sa.Text(), nullable=True),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lifecycle_state", sa.Text(), nullable=False, server_default="open"),
        sa.Column("source_message_ids", sa.Text(), nullable=False),
        sa.Column("current_intent", sa.Text(), nullable=False),
        sa.Column("entry_plan", sa.Text(), nullable=False),
        sa.Column("stop_plan", sa.Float(), nullable=True),
        sa.Column("targets", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_signal_episodes_identity",
        "signal_episodes",
        ["provider", "analyst", "instrument", "lifecycle_state"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
