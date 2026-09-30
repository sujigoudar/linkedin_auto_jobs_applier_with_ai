"""Add route_qualifications table (per-exact-route live qualification
state -- see app/qualification.py's module docstring)

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29

See app/db.py's own route_qualifications table comment for exactly what
each real column holds and app/qualification.py for the state ladder and
its prerequisite/feedback-capability enforcement. A fresh database gets
this straight from app/db.py's SCHEMA string (SignalStore's own bootstrap
re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a pre-existing
database picks up the new table too) -- same "this file is not the real
source of truth for a SignalStore-created database" precedent as every
earlier numbered revision's own docstring.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "route_qualifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("adapter_type", sa.Text(), nullable=False),
        sa.Column("route_key", sa.Text(), nullable=False),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("product_type", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "adapter_type", "route_key", "asset_class", "product_type", "state",
            name="uq_route_qualifications_route_state",
        ),
    )
    op.create_index(
        "idx_route_qualifications_route",
        "route_qualifications",
        ["adapter_type", "route_key", "asset_class", "product_type"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
