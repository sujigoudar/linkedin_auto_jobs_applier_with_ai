"""Add orders.submitted_at / orders.protection_confirmed_at (PU-A2, real
multi-stage execution-latency timestamp capture)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28

Nullable, additive columns -- see app/db.py's SCHEMA comment on these two
columns and app/execution_quality.py's module docstring for exactly which
of the conceptual "publication -> receipt -> decision -> submission ->
acknowledgement -> fill -> protection" stages this schema tracks real,
distinct timestamps for today. A fresh database gets both straight from
app/db.py's SCHEMA string (SignalStore's own bootstrap, not this file --
see 0001's docstring on why that's the real source of truth for every
SignalStore-created database). This revision only matters for someone
provisioning a database purely through the Alembic CLI, or an operator
running `alembic upgrade head` by hand against an existing deployment
whose `alembic_version` table was already stamped at 0004.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("submitted_at", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("protection_confirmed_at", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
