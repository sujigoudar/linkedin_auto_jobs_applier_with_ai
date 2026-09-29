"""Add writer_lease table (cross-process/cross-host single-writer fencing)

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-29

See app/db.py's SCHEMA comment on `writer_lease`, app/writer_lease.py's
module docstring, and docs/FAILOVER.md for what this table is and exactly
how it's used. A fresh database gets it straight from app/db.py's SCHEMA
string (SignalStore's own bootstrap, not this file -- see 0001's
docstring on why that's the real source of truth for every
SignalStore-created database). This revision only matters for someone
provisioning a database purely through the Alembic CLI, or an operator
running `alembic upgrade head` by hand against an existing deployment
whose `alembic_version` table was already stamped at 0014 (see
0011_add_capital_reservations_table.py, P0-4;
0012_add_orders_distinct_quantity_fields.py, AUD-01;
0013_add_route_qualifications_table.py; and
0014_add_command_ledger_table.py).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writer_lease",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("holder_id", sa.Text(), nullable=False),
        sa.Column("acquired_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("renewed_at", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="writer_lease_single_row"),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
