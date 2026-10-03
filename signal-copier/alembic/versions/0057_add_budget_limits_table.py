"""Add budget_limits table for WC-30.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-02 00:00:00.000000

Implements WC-30: hierarchical budget persistence with multi-level limit
configuration for analyst, underlying, cluster and account levels.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create budget_limits table."""
    op.create_table(
        "budget_limits",
        sa.Column("limit_id", sa.String(255), primary_key=True),
        sa.Column("level", sa.String(50), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("max_cents", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("level", "key", name="uq_budget_limits_level_key"),
    )
    op.create_index("idx_budget_limits_level_key", "budget_limits", ["level", "key"])


def downgrade() -> None:
    """Drop budget_limits table."""
    op.drop_table("budget_limits")
