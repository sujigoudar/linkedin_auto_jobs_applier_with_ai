"""Add orders.reserved_quantity / orders.acknowledged_quantity (TRK-Q1,
the two QuantityBreakdown fields with no existing column)

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-30

Nullable, additive columns -- see app/db.py's SCHEMA comment on the
`orders` table for exactly what each real value means and app/models.py's
`QuantityBreakdown` for the full seven-field quantity model these two
fields complete (the other five were already covered by
`requested_quantity`, `confirmed_cumulative_fill`, `applied_execution_delta`,
`outstanding_possible_fill`, and `positions.net_quantity`). A fresh
database gets them straight from app/db.py's SCHEMA string (SignalStore's
own bootstrap, not this file -- see 0001's docstring on why that's the
real source of truth for every SignalStore-created database). This
revision only matters for someone provisioning a database purely through
the Alembic CLI, or an operator running `alembic upgrade head` by hand
against an existing deployment whose `alembic_version` table was already
stamped at 0017.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("reserved_quantity", sa.Float(), nullable=True))
    op.add_column("orders", sa.Column("acknowledged_quantity", sa.Float(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
