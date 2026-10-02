"""Add alerts table for operational notifications (WP-34).

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create alerts table."""
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("kind", sa.String(255), nullable=False),
        sa.Column(
            "account_id",
            sa.String(255),
            nullable=True,
            comment="Account UUID, or NULL for system-level alerts",
        ),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=True, comment="JSON-encoded dict"),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when alert was acknowledged; NULL = unacknowledged",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Indexes for common queries
    op.create_index("ix_alerts_account_id", "alerts", ["account_id"])
    op.create_index(
        "ix_alerts_unacknowledged",
        "alerts",
        ["acknowledged_at"],
        sqlite_where=sa.text("acknowledged_at IS NULL"),
    )


def downgrade() -> None:
    """Drop alerts table."""
    op.drop_table("alerts")
