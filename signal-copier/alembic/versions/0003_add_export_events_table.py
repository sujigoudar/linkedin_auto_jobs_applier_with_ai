"""Add export_events table (Signal Platform Integration Correction Pack,
transactional private export outbox)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28

See app/db.py's own export_events table comment for what this is and
app/db.py's `_insert_export_event`/`append_export_event`/
`list_undelivered_export_events`/`mark_export_events_delivered` for how
it's used. A fresh database gets this straight from app/db.py's SCHEMA
string (SignalStore's own bootstrap re-runs `CREATE TABLE IF NOT EXISTS`
on every open, so a pre-existing database picks up the new table too) --
same "this file is not the real source of truth for a SignalStore-created
database" precedent as 0001/0002's own docstrings.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("source_stream", sa.Text(), nullable=False),
        sa.Column("export_sequence", sa.Integer(), nullable=False),
        sa.Column("envelope_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("appended_at", sa.Text(), nullable=False),
        sa.Column("delivered_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("source_stream", "export_sequence"),
    )
    op.create_index(
        "idx_export_events_undelivered", "export_events", ["source_stream", "export_sequence"],
        sqlite_where=sa.text("delivered_at IS NULL"),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
