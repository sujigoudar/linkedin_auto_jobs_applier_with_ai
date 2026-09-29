"""Add orders.purpose / orders.family_id (DB-0X, real order purpose/family
grouping)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-29

Nullable, additive columns -- see app/db.py's SCHEMA comment on the
`orders` table for exactly what each real value means (and the disclosed
gap for a plain account's close, which has no real family to report). A
fresh database gets both straight from app/db.py's SCHEMA string
(SignalStore's own bootstrap, not this file -- see 0001's docstring on why
that's the real source of truth for every SignalStore-created database).
This revision only matters for someone provisioning a database purely
through the Alembic CLI, or an operator running `alembic upgrade head` by
hand against an existing deployment whose `alembic_version` table was
already stamped at 0007.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("purpose", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("family_id", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
