"""WP-32: Add max_gross_leverage for leverage cap enforcement.

Revision ID: 0043
Revises: 0036
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add max_gross_leverage column to config_accounts for leverage cap."""
    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_gross_leverage",
                sa.Numeric(precision=5, scale=2),
                nullable=True,
                comment="Maximum gross leverage ceiling (e.g., 1.0=no leverage, 1.25=25% leverage). When set, exposure is refused if it would exceed max_gross_leverage × (equity − maintenance_margin)."
            )
        )


def downgrade() -> None:
    """Remove max_gross_leverage column from config_accounts."""
    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.drop_column("max_gross_leverage")
