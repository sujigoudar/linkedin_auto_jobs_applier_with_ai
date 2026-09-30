"""Add orders.confirmed_cumulative_fill / applied_execution_delta /
outstanding_possible_fill (AUD-01, real distinct-field quantity model
replacing optimistic PENDING-order accounting)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-29

Nullable, additive columns -- see app/db.py's SCHEMA comment on the
`orders` table for exactly what each real value means and why all three
are nullable (a pre-existing row predating this migration, or a call site
with genuinely nothing to report). A fresh database gets them straight
from app/db.py's SCHEMA string (SignalStore's own bootstrap, not this file
-- see 0001's docstring on why that's the real source of truth for every
SignalStore-created database). This revision only matters for someone
provisioning a database purely through the Alembic CLI, or an operator
running `alembic upgrade head` by hand against an existing deployment
whose `alembic_version` table was already stamped at 0011.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("confirmed_cumulative_fill", sa.Float(), nullable=True))
    op.add_column("orders", sa.Column("applied_execution_delta", sa.Float(), nullable=True))
    op.add_column("orders", sa.Column("outstanding_possible_fill", sa.Float(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
