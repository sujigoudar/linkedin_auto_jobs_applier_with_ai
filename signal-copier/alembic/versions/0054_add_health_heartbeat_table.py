"""WP-35 (F-08): health_heartbeat table used by the disk-health probe.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-02 00:00:00.000000

SignalStore's bootstrap SCHEMA already creates this table; this revision
makes the CLI-only `alembic upgrade head` path produce the same schema.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_heartbeat",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "last_write",
            sa.TIMESTAMP,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("health_heartbeat")
