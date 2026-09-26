"""Initial schema snapshot (C03, bounded)

Revision ID: 0001
Revises:
Create Date: 2026-09-26

This is NOT how this database actually got here for any existing
deployment: every database this app has ever created got its schema from
app/db.py's SCHEMA executescript + the (now-frozen) _COLUMN_MIGRATIONS
list, never from this file. This revision exists so:

- `alembic upgrade head` against a genuinely empty database (someone
  provisioning a fresh one purely through the Alembic CLI, bypassing
  SignalStore entirely) produces the exact same schema SignalStore's own
  bootstrap does.
- Every *actual* SignalStore-created database (fresh or pre-existing) is
  stamped at this revision without re-running it (see app/db.py's
  `_stamp_alembic_head_if_needed`) -- its schema is already exactly this,
  by construction.

Any schema change from here on should be a NEW revision (`alembic
revision -m "..."`) with a real, reviewed upgrade() -- not another
addition to _COLUMN_MIGRATIONS, which stops growing as of this commit.
"""
from __future__ import annotations

from alembic import op

from app.db import SCHEMA

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().connection.executescript(SCHEMA)


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade is not supported for the initial schema -- see this project's other "
        "one-way-migration precedent (app/db.py's _COLUMN_MIGRATIONS never had a downgrade path either)"
    )
