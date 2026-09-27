"""Add orders.reserved_notional (E03, bounded reservation-timing fix)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

Nullable, additive column -- see app/db.py's SCHEMA comment on
`orders.reserved_notional` and app/reconciliation.py's `_correct_position`
for what it's used for. A fresh database gets this straight from
app/db.py's SCHEMA string (SignalStore's own bootstrap, not this file --
see 0001's docstring on why that's the real source of truth for every
SignalStore-created database). This revision only matters for someone
provisioning a database purely through the Alembic CLI, or an operator
running `alembic upgrade head` by hand against an existing deployment
whose `alembic_version` table was already stamped at 0001 (SignalStore's
own bootstrap never re-runs schema changes against an already-stamped
database -- see `_stamp_alembic_head_if_needed`).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("reserved_notional", sa.Float(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
