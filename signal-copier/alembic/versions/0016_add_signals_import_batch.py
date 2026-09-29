"""Add signals.import_batch (E02, history-import batch label)

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-29

Nullable, additive column -- see Signal.import_batch's docstring in
app/models.py and app/db.py's `_COLUMN_MIGRATIONS` comment on this same
column for exactly what it means: NULL for every live-received signal
(this codebase's only other signal-creation path); a batch label for one
created by the owner-gated batch-classify-and-import review workflow. A
fresh database gets it straight from app/db.py's SCHEMA string plus
`_COLUMN_MIGRATIONS` (SignalStore's own bootstrap, not this file -- see
0001's docstring on why that's the real source of truth for every
SignalStore-created database). This revision only matters for someone
provisioning a database purely through the Alembic CLI, or an operator
running `alembic upgrade head` by hand against an existing deployment
whose `alembic_version` table was already stamped at 0015 (see
0015_add_writer_lease_table.py). Before this revision existed,
`signals.import_batch` was reachable only through `_COLUMN_MIGRATIONS`
(SignalStore's own bootstrap ALTER TABLE), never through a numbered
Alembic revision -- a disclosed gap this revision closes.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("import_batch", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
