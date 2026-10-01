"""Track 42: add `export_events.terminal_park_reason`/`terminal_parked_at`
(see app/db.py's `export_events` CREATE TABLE comment, and
app/relay_worker.py's own `classify_parked_reason` for how they get set)
-- the real, distinct "structurally parked, will never resolve without a
code change, stop resending it" marker, kept separate from
`delivered_at` so a structurally parked event is never mistaken for one
the commercial relay genuinely applied.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-01

Purely additive -- no existing table is dropped or altered beyond
adding two new nullable columns, same precedent as every earlier
revision in this directory. No backfill: nothing in this codebase
before this track ever terminally parked an export event, so both
columns start NULL for every pre-existing row, exactly as they do for a
fresh database.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("export_events", sa.Column("terminal_park_reason", sa.Text(), nullable=True))
    op.add_column("export_events", sa.Column("terminal_parked_at", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
